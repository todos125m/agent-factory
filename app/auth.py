"""Login and access control (owner decision d33; docs/HUMAN_GUIDED_FACTORY.md §10).

Minimal and passwordless:
- The first account of a server is created by whoever opens it first and becomes the admin. Every later account is
  created by the admin (POST /users while signed in), who hands it over with a one-time login code — so the server's
  model access (the owner's subscription or API key) is never open to strangers.
- A signed-in browser holds a random session token in an HttpOnly, SameSite=Strict cookie: page scripts can't read it
  and other sites' requests don't carry it (no CSRF). The database keeps only the token's SHA-256.
- Another device signs in with a one-time code made by a signed-in one (10 minutes, single use, 60 random bits).
- An account from before login existed is claimable once (POST /auth/claim) from the server's own machine, or anywhere
  with the owner token (AGENT_FACTORY_OWNER_TOKEN) — so the owner's existing browser keeps its data.

Every account sees only its own projects, workspaces, layers and preferences; a missing or foreign id is the same 404,
so ids can't be probed. The admin alone changes what every account shares: the global settings layer, the agent
registry, benchmarks; and creates accounts.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import get_session
from app.errors import AppError, not_found
from app.models import AuthSession, LoginCode, Project, User, Workspace, utcnow

COOKIE = "af_session"
SESSION_TTL = timedelta(days=90)
LOGIN_CODE_TTL = timedelta(minutes=10)
# `last_seen_at` is written at most this often: a write per request would hold SQLite's one write lock for nothing.
LAST_SEEN_EVERY = timedelta(minutes=10)
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
FORWARDING_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"  # no 0/O, 1/I/L: read aloud or typed on a phone


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _aware(moment: datetime) -> datetime:
    """SQLite gives back naive datetimes; every one stored here is UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def unauthenticated() -> AppError:
    return AppError(401, "unauthenticated", "Sign in first")


def forbidden(reason: str) -> AppError:
    return AppError(403, "forbidden", f"Not allowed: {reason}", reason=reason)


# ---------- accounts and sessions ----------


def accounts_exist(session: Session) -> bool:
    return session.scalar(select(func.count(User.id))) > 0


def create_account(session: Session, email: str, name: str | None) -> User:
    """A new account; the first of the server is its admin. The caller commits."""
    user = User(email=email, name=name, is_admin=not accounts_exist(session))
    session.add(user)
    session.flush()
    return user


def start_session(session: Session, user: User) -> str:
    """A new signed-in session for `user`; returns the token for the cookie (the only place it ever exists)."""
    token = secrets.token_urlsafe(32)
    now = utcnow()
    session.add(AuthSession(user_id=user.id, token_hash=_digest(token), created_at=now, last_seen_at=now,
                            expires_at=now + SESSION_TTL))
    return token


def set_cookie(response: Response, request: Request, token: str) -> None:
    secure = request.url.scheme == "https" or os.getenv("AGENT_FACTORY_SECURE_COOKIE") == "1"
    response.set_cookie(COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), httponly=True, samesite="strict",
                        secure=secure, path="/")


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")


def _active_session(session: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    return session.scalar(select(AuthSession).where(
        AuthSession.token_hash == _digest(token), AuthSession.revoked_at.is_(None), AuthSession.expires_at > utcnow(),
    ))


def current_user(request: Request, session: Session = Depends(get_session)) -> User:
    """The signed-in account, or 401. A route's other dependencies reuse it (FastAPI caches it per request)."""
    auth = _active_session(session, request.cookies.get(COOKIE))
    user = session.get(User, auth.user_id) if auth is not None else None
    if user is None:
        raise unauthenticated()
    if utcnow() - _aware(auth.last_seen_at) > LAST_SEEN_EVERY:
        auth.last_seen_at = utcnow()
        session.commit()
    request.state.auth_session_id = auth.id
    return user


def optional_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    """The signed-in account, or None (no 401): for the one route anyone may call before an account exists."""
    auth = _active_session(session, request.cookies.get(COOKIE))
    return session.get(User, auth.user_id) if auth is not None else None


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise forbidden("admin_only")
    return user


def end_session(session: Session, request: Request) -> None:
    auth = _active_session(session, request.cookies.get(COOKIE))
    if auth is not None:
        auth.revoked_at = utcnow()
        session.commit()


# ---------- one-time login codes ----------


def new_login_code(session: Session, user: User) -> tuple[str, datetime]:
    """A one-time code that signs `user` in on another device; shown once, only its hash is kept. The caller commits."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(12))  # 30^12 ≈ 2^59
    expires = utcnow() + LOGIN_CODE_TTL
    session.add(LoginCode(user_id=user.id, code_hash=_digest(raw), expires_at=expires))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}", expires


def normalize_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def redeem_login_code(session: Session, code: str) -> User:
    """The account a valid, unused, unexpired code belongs to; the code is spent. Any other code is the same 401.
    Spending is one conditional UPDATE, so two devices racing with the same code can't both get in."""
    digest = _digest(normalize_code(code))
    now = utcnow()
    spent = session.execute(
        update(LoginCode)
        .where(LoginCode.code_hash == digest, LoginCode.used_at.is_(None), LoginCode.expires_at > now)
        .values(used_at=now)
        .returning(LoginCode.user_id)
    ).first()
    user = session.get(User, spent[0]) if spent else None
    if user is None:
        session.rollback()
        raise AppError(401, "login_code_invalid", "This code is wrong, expired or already used")
    return user


# ---------- claiming an account from before login existed ----------


def _from_this_machine(request: Request) -> bool:
    """A direct request from the server's own machine. Behind a proxy every request looks local, so any forwarding
    header disqualifies it: then only the owner token works."""
    if any(h in request.headers for h in FORWARDING_HEADERS):
        return False
    return request.client is not None and request.client.host in LOOPBACK


def owner_token_ok(given: str | None) -> bool:
    expected = os.getenv("AGENT_FACTORY_OWNER_TOKEN") or ""
    return bool(expected) and bool(given) and hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))


def claim(session: Session, request: Request, user_id: int, owner_token: str | None) -> User:
    """Sign in to a claimable (pre-login) account, once. The flag is cleared with one conditional UPDATE, so a second
    claim — a race or a replay — finds nothing. Not claimable and not found are the same 404."""
    if not (_from_this_machine(request) or owner_token_ok(owner_token)):
        raise forbidden("claim_needs_this_machine_or_owner_token")
    cleared = session.execute(
        update(User).where(User.id == user_id, User.claimable.is_(True)).values(claimable=False).returning(User.id)
    ).first()
    if cleared is None:
        session.rollback()
        raise not_found("user", "No account to claim")
    return session.get(User, user_id)


# ---------- ownership ----------


def own_project(session: Session, project_id: int, user: User) -> Project:
    """The project if it is `user`'s; someone else's is the same 404 as a missing one (ids can't be probed)."""
    project = session.get(Project, project_id)
    if project is None or project.owner_id != user.id:
        raise not_found("project")
    return project


def own_workspace(session: Session, workspace_id: int, user: User) -> Workspace:
    workspace = session.get(Workspace, workspace_id)
    if workspace is None or workspace.owner_id != user.id:
        raise not_found("workspace")
    return workspace
