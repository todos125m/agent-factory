"""Credentials: provider API keys the owner adds once, kept encrypted, shown only masked (PR6b of issue #2; d39).

- A key is encrypted (Fernet) with a master secret from the environment, AGENT_FACTORY_SECRET_KEY, never from the
  repository or the database. Without it (or without the `cryptography` package) adding a key is refused and says why.
- The key is never returned by any route, never in a log, event, error, settings layer, blueprint, workflow or model
  text: everything else holds a reference, `cred:<id>`. The UI gets ••••<last four> and the result of the last test.
- A reference works only for its owner's own calls (their projects and builds) and only for its own provider. Another
  account that ends up with the same reference in a shared settings layer simply cannot use it.
- Testing a connection is the owner's explicit action, makes one free request to the provider's own list-models
  endpoint (a fixed URL, redirects not followed so the key is never sent elsewhere), and the result is what the
  provider answered — ok only for a 200, rejected for 401/403, unreachable for anything else. Nothing is assumed.
- Revoking wipes the ciphertext; the row stays so what refers to it says "revoked", not "missing".
"""

import base64
import functools
import hashlib
import os
import re
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import AppError, not_found
from app.models import Credential, User, utcnow

PROVIDERS = ("anthropic", "openai")
TEST_RESULTS = ("ok", "rejected", "unreachable")
REF_PATTERN = re.compile(r"^cred:([0-9]{1,9})$")
KEY_MIN, KEY_MAX = 20, 300
SECRET_ENV = "AGENT_FACTORY_SECRET_KEY"
SECRET_MIN = 32
SECRET_DISTINCT = 12  # a secret of one repeated character is as good as none
KDF_SALT = b"agent-factory/credentials/v1"
KDF_ROUNDS = 200_000
TEST_URLS = {"anthropic": "https://api.anthropic.com/v1/models", "openai": "https://api.openai.com/v1/models"}
TEST_TIMEOUT_S = 10


def _headers(provider: str, key: str) -> dict[str, str]:
    if provider == "anthropic":
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {key}"}


# ---------- the master secret ----------


def status() -> str | None:
    """Why keys can't be kept right now (the reason's code), or None when they can."""
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return "crypto_unavailable"
    secret = os.getenv(SECRET_ENV, "")
    if len(secret) < SECRET_MIN or len(set(secret)) < SECRET_DISTINCT:
        return "secret_key_missing"
    return None


@functools.lru_cache(maxsize=4)
def _derive(secret: str) -> bytes:
    """The Fernet key: stretched (PBKDF2) from the master secret, once per process."""
    return base64.urlsafe_b64encode(hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), KDF_SALT, KDF_ROUNDS))


def _fernet():
    why = status()
    if why == "crypto_unavailable":
        raise AppError(503, "crypto_unavailable", "Keys can't be kept: install the 'cryptography' package")
    if why == "secret_key_missing":
        raise AppError(503, "secret_key_missing",
                       f"Keys can't be kept: set {SECRET_ENV} (at least {SECRET_MIN} characters, {SECRET_DISTINCT} different) "
                       "in the server's environment")
    from cryptography.fernet import Fernet

    return Fernet(_derive(os.environ[SECRET_ENV]))


def mask(credential: Credential) -> str:
    return "" if credential.revoked_at is not None else "••••" + credential.last4


# ---------- the owner's side ----------


def load(db: Session, credential_id: int, owner: User) -> Credential:
    credential = db.get(Credential, credential_id)
    if credential is None or credential.owner_id != owner.id:
        raise not_found("credential")
    return credential


def view(credential: Credential) -> dict[str, Any]:
    """Everything the UI may see: never the key, not even encrypted."""
    return {"id": credential.id, "ref": f"cred:{credential.id}", "name": credential.name,
            "provider": credential.provider, "masked": mask(credential), "revoked": credential.revoked_at is not None,
            "tested_at": credential.tested_at, "test_result": credential.test_result,
            "created_at": credential.created_at}


def listing(db: Session, owner: User) -> dict[str, Any]:
    rows = db.scalars(select(Credential).where(Credential.owner_id == owner.id, Credential.revoked_at.is_(None))
                      .order_by(Credential.id))
    return {"available": status() is None, "reason": status(), "providers": list(PROVIDERS),
            "items": [view(c) for c in rows]}


def add(db: Session, owner: User, *, name: str, provider: str, key: str, request_id: str) -> tuple[Credential, bool]:
    if provider not in PROVIDERS:
        raise AppError(422, "credential_invalid", "Unknown provider", field="provider")
    key = key.strip()
    if not (KEY_MIN <= len(key) <= KEY_MAX) or not re.fullmatch(r"[\x21-\x7e]+", key):
        raise AppError(422, "credential_invalid", "That doesn't look like an API key", field="key")  # never echoes it
    existing = db.scalar(select(Credential).where(Credential.owner_id == owner.id, Credential.request_id == request_id))
    if existing is not None:
        if (existing.name, existing.provider, existing.last4) != (name, provider, key[-4:]):
            raise AppError(409, "credential_request_reused", "That request id was used for a different connection")
        return existing, False
    db.execute(select(User.id).where(User.id == owner.id).with_for_update())  # one add per owner at a time: names stay unique
    if db.scalar(select(Credential.id).where(Credential.owner_id == owner.id, Credential.name == name,
                                              Credential.revoked_at.is_(None))) is not None:
        raise AppError(409, "credential_name_taken", f"You already have a connection named '{name}'", name=name)
    token = _fernet().encrypt(key.encode("utf-8")).decode("ascii")
    credential = Credential(owner_id=owner.id, name=name, provider=provider, ciphertext=token, last4=key[-4:],
                            request_id=request_id)
    db.add(credential)
    try:
        db.commit()
    except IntegrityError:  # a concurrent identical request won
        db.rollback()
        return db.scalar(select(Credential).where(Credential.owner_id == owner.id,
                                                  Credential.request_id == request_id)), False
    return credential, True


def revoke(db: Session, owner: User, credential_id: int) -> Credential:
    credential = load(db, credential_id, owner)
    if credential.revoked_at is None:
        credential.ciphertext, credential.revoked_at = "", utcnow()
        db.commit()
    return credential


def _http_status(url: str, headers: dict[str, str]) -> int | None:
    """The status a GET answered, or None if there was no answer. A redirect is not followed: the key goes only to the
    provider's own fixed URL."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers=headers), timeout=TEST_TIMEOUT_S) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:  # noqa: BLE001 - no network, DNS, TLS, timeout: all "no answer"
        return None


def test(db: Session, owner: User, credential_id: int) -> Credential:
    """One free request to the provider, because the owner asked. Records exactly what came back."""
    credential = load(db, credential_id, owner)
    if credential.revoked_at is not None:
        raise AppError(409, "credential_revoked", "This connection was revoked")
    try:
        key = _fernet().decrypt(credential.ciphertext.encode("ascii")).decode("utf-8")
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 - a changed master secret (InvalidToken); quote nothing
        raise AppError(409, "credential_unreadable", "This key can't be read with the server's current secret") from e
    provider = credential.provider
    db.rollback()  # nothing of this request is open while the provider answers
    code = _http_status(TEST_URLS[provider], _headers(provider, key))
    credential = load(db, credential_id, owner)
    credential.test_result = "ok" if code == 200 else ("rejected" if code in (401, 403) else "unreachable")
    credential.tested_at = utcnow()
    db.commit()
    return credential


# ---------- the gateway's side ----------


class CredentialUnavailable(RuntimeError):
    """The reason a reference can't be used (its text never holds a key)."""


class CredentialNotYours(CredentialUnavailable):
    """The reference is another account's: for this call it means nothing (a shared layer may carry it)."""


def key_for_call(db: Session, owner_id: int | None, ref: str, provider: str) -> str:
    """The decrypted key for a model route's reference — only for its owner's own calls and its own provider."""
    match = REF_PATTERN.fullmatch(str(ref))
    if match is None:
        raise CredentialUnavailable("the model route's credential reference is malformed")
    credential = db.get(Credential, int(match.group(1)))
    if credential is None:
        raise CredentialUnavailable("the credential doesn't exist")
    if owner_id is None or credential.owner_id != owner_id:
        raise CredentialNotYours("the credential belongs to another account")
    if credential.revoked_at is not None:
        raise CredentialUnavailable("the credential was revoked")
    if credential.provider != provider:
        raise CredentialUnavailable(f"the credential is for {credential.provider}, not {provider}")
    try:
        return _fernet().decrypt(credential.ciphertext.encode("ascii")).decode("utf-8")
    except AppError as e:
        raise CredentialUnavailable(e.detail) from e
    except Exception as e:  # noqa: BLE001 - a wrong master secret (InvalidToken): say so, quote nothing
        raise CredentialUnavailable("the credential can't be decrypted with this server's secret") from e


def redact(text: str, key: str | None) -> str:
    return text.replace(key, "[redacted]") if key else text
