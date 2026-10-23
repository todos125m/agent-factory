"""Credentials (app/credentials.py, issue #2 PR6b, owner decision d39): a provider key is added once, kept encrypted, and
after that only a reference, a mask and a test result exist anywhere. FakeProvider only; no request leaves the machine
(the one HTTP function is replaced)."""

import json

import pytest
from sqlalchemy import select

from app import credentials
from app.gateway.providers import FakeProvider, ProviderError
from app.gateway.service import Gateway
from app.models import Credential, ModelCall, RunEvent, SettingsLayer
from tests.test_factory_intake import rid

KEY = "sk-test-0123456789abcdefghijKLMNOP"


def add(client, name="Main", provider="anthropic", key=KEY, **kw):
    return client.post("/credentials", json={"name": name, "provider": provider, "key": key,
                                              "request_id": kw.pop("request_id", None) or rid()})


def made(client, **kw):
    r = add(client, **kw)
    assert r.status_code == 201, r.text
    return r.json()


def test_a_key_is_added_encrypted_and_shown_only_masked(client, session_factory):
    c = made(client)
    assert (c["masked"], c["ref"], c["provider"], c["revoked"]) == ("••••MNOP", f"cred:{c['id']}", "anthropic", False)
    assert KEY not in json.dumps(c) and "key" not in c and "ciphertext" not in c
    listing = client.get("/credentials").json()
    assert listing["available"] is True and listing["reason"] is None and [i["id"] for i in listing["items"]] == [c["id"]]
    assert KEY not in json.dumps(listing)
    with session_factory() as s:
        row = s.get(Credential, c["id"])
        assert row.ciphertext and KEY not in row.ciphertext and row.last4 == "MNOP"
        assert credentials.key_for_call(s, row.owner_id, c["ref"], "anthropic") == KEY  # and it decrypts back


@pytest.mark.parametrize("key", ["short", "has a space in it 0123456789", "ключ-api-0123456789abcdefgh", "k" * 301])
def test_a_key_that_is_not_one_is_refused_without_being_echoed(client, key):
    r = add(client, key=key)
    assert r.status_code == 422 and r.json()["code"] == "credential_invalid", r.text
    assert key not in r.text


def test_a_very_long_key_is_not_echoed_by_the_validation_error_either(client):
    r = add(client, key="z" * 2500)
    assert r.status_code == 422 and "zzzzzzzzzz" not in r.text


def test_an_unknown_provider_is_refused(client):
    assert add(client, provider="acme").json()["code"] == "credential_invalid"


def test_a_replayed_add_and_a_taken_name(client, session_factory):
    same = rid()
    first, second = add(client, request_id=same).json(), add(client, request_id=same).json()
    assert second["id"] == first["id"] and second["duplicate"] is True
    taken = add(client)
    assert taken.status_code == 409 and taken.json()["code"] == "credential_name_taken"
    with session_factory() as s:
        assert s.query(Credential).count() == 1


def test_without_the_master_secret_or_the_package_nothing_is_kept_and_it_says_why(client, monkeypatch):
    monkeypatch.delenv(credentials.SECRET_ENV)
    listing = client.get("/credentials").json()
    assert listing["available"] is False and listing["reason"] == "secret_key_missing"
    r = add(client)
    assert r.status_code == 503 and r.json()["code"] == "secret_key_missing"
    monkeypatch.setenv(credentials.SECRET_ENV, "short")
    assert add(client).json()["code"] == "secret_key_missing"  # too short counts as missing
    monkeypatch.setattr(credentials, "status", lambda: "crypto_unavailable")
    assert client.get("/credentials").json()["reason"] == "crypto_unavailable"


def test_a_wrong_master_secret_cannot_read_a_key_and_quotes_nothing(client, session_factory, monkeypatch):
    c = made(client)
    monkeypatch.setenv(credentials.SECRET_ENV, "another-master-secret-of-more-than-32-chars")
    with session_factory() as s:
        with pytest.raises(credentials.CredentialUnavailable) as e:
            credentials.key_for_call(s, s.get(Credential, c["id"]).owner_id, c["ref"], "anthropic")
        assert KEY not in str(e.value)


# ---------- testing a connection: exactly what the provider answered ----------


@pytest.mark.parametrize("answer, result", [(200, "ok"), (401, "rejected"), (403, "rejected"), (429, "unreachable"),
                                            (500, "unreachable"), (None, "unreachable")])
def test_a_test_records_what_the_provider_answered(client, monkeypatch, answer, result):
    seen = []
    monkeypatch.setattr(credentials, "_http_status", lambda url, headers: seen.append((url, headers)) or answer)
    c = made(client)
    assert c["test_result"] is None and c["tested_at"] is None  # nothing is assumed
    assert seen == []  # adding a key makes no request
    r = client.post(f"/credentials/{c['id']}/test")
    assert r.status_code == 200 and r.json()["test_result"] == result and r.json()["tested_at"]
    assert seen == [("https://api.anthropic.com/v1/models", {"x-api-key": KEY, "anthropic-version": "2023-06-01"})]
    assert KEY not in r.text


def test_an_openai_test_goes_to_openais_own_fixed_url(client, monkeypatch):
    seen = []
    monkeypatch.setattr(credentials, "_http_status", lambda url, headers: seen.append((url, headers)) or 200)
    c = made(client, provider="openai", name="OpenAI")
    client.post(f"/credentials/{c['id']}/test")
    assert seen == [("https://api.openai.com/v1/models", {"Authorization": f"Bearer {KEY}"})]


def test_a_redirect_is_never_followed_with_the_key():
    """The opener has no redirect handler that follows: a 3xx is the answer, the key goes nowhere else."""
    import urllib.request

    seen = []

    class Answer302(urllib.request.HTTPHandler):
        def http_open(self, req):
            seen.append(req.full_url)
            import io
            import urllib.response

            resp = urllib.response.addinfourl(io.BytesIO(b""), {"Location": "http://elsewhere.invalid/"}, req.full_url, 302)
            resp.msg = "Found"
            return resp

    # the module's own opener, with only the transport swapped
    original = urllib.request.build_opener
    urllib.request.build_opener = lambda *handlers: original(*handlers, Answer302)
    try:
        assert credentials._http_status("http://provider.invalid/v1/models", {"x-api-key": KEY}) in (302, None)
    finally:
        urllib.request.build_opener = original
    assert seen == ["http://provider.invalid/v1/models"]


# ---------- revoking ----------


def test_revoking_wipes_the_key_and_what_refers_to_it_says_revoked(client, session_factory, monkeypatch):
    monkeypatch.setattr(credentials, "_http_status", lambda url, headers: 200)
    c = made(client)
    r = client.delete(f"/credentials/{c['id']}")
    assert r.status_code == 200 and r.json()["revoked"] is True and r.json()["masked"] == ""
    assert client.get("/credentials").json()["items"] == []
    with session_factory() as s:
        row = s.get(Credential, c["id"])
        assert row.ciphertext == "" and row.revoked_at is not None
        with pytest.raises(credentials.CredentialUnavailable, match="revoked"):
            credentials.key_for_call(s, row.owner_id, c["ref"], "anthropic")
    assert client.post(f"/credentials/{c['id']}/test").json()["code"] == "credential_revoked"
    assert client.delete(f"/credentials/{c['id']}").status_code == 200  # again: nothing more to do
    assert add(client).status_code == 201  # and the name is free again


def test_another_account_sees_and_touches_nothing(client):
    c = made(client)
    client.post("/users", json={"email": "other@example.com"})
    assert client.get("/credentials").json()["items"] == []
    assert client.post(f"/credentials/{c['id']}/test").status_code == 404
    assert client.delete(f"/credentials/{c['id']}").status_code == 404


# ---------- a model route refers to it ----------


def route(ref, provider="anthropic"):
    return {"models": {"research": {"provider": provider, "model": "claude-sonnet-5", "credential_ref": ref}}}


def test_a_settings_layer_may_name_only_ones_own_live_connection_of_the_routes_provider(client, project):
    pid = project["id"]
    c = made(client)
    assert client.put(f"/settings/project/{pid}", json=route(c["ref"])).status_code == 200
    stored = client.get(f"/settings/project/{pid}").json()
    assert stored["values"]["models"]["research"]["credential_ref"] == c["ref"] and KEY not in json.dumps(stored)
    for bad, why in [("cred:99999", "not one of your"), (c["ref"], "is for anthropic")]:
        r = client.put(f"/settings/project/{pid}", json=route(bad, provider="openai" if bad == c["ref"] else "anthropic"))
        assert r.status_code == 422 and why in r.json()["detail"], r.text
    for malformed in ["sk-secret-in-the-wrong-place", "cred:abc", "cred:", f"cred:{c['id']} "]:
        assert client.put(f"/settings/project/{pid}", json=route(malformed)).status_code == 422
    assert client.put(f"/settings/project/{pid}", json={"models": {"research": {"credential_ref": None}}}).status_code == 200
    client.delete(f"/credentials/{c['id']}")
    assert client.put(f"/settings/project/{pid}", json=route(c["ref"])).status_code == 422  # revoked


def test_a_missing_field_does_not_echo_the_body_with_the_key(client):
    r = client.post("/credentials", json={"provider": "anthropic", "key": KEY, "request_id": rid()})  # no name
    assert r.status_code == 422 and KEY not in r.text and "MNOP" not in r.text
    r = client.post("/credentials", content='{"name": "n", "key": "%s"' % KEY, headers={"content-type": "application/json"})
    assert r.status_code == 422 and KEY not in r.text  # and a broken body


def test_a_test_after_the_master_secret_changed_says_so(client, monkeypatch):
    c = made(client)
    monkeypatch.setenv(credentials.SECRET_ENV, "another-master-secret-of-more-than-32-chars")
    r = client.post(f"/credentials/{c['id']}/test")
    assert r.status_code == 409 and r.json()["code"] == "credential_unreadable" and KEY not in r.text


def test_the_connection_test_holds_no_transaction_while_the_provider_answers(client, session_factory, monkeypatch):
    c = made(client)
    seen = []

    def during(url, headers):
        with session_factory() as other:  # a writer from elsewhere is not blocked by the test's read
            other.execute(select(Credential.id))
            seen.append(True)
        return 200

    monkeypatch.setattr(credentials, "_http_status", during)
    assert client.post(f"/credentials/{c['id']}/test").json()["test_result"] == "ok" and seen == [True]


def test_a_request_id_reused_for_another_key_is_not_a_silent_success(client):
    same = rid()
    assert add(client, request_id=same).status_code == 201
    r = add(client, key="sk-test-a-different-key-0000000000", request_id=same)
    assert r.status_code == 409 and r.json()["code"] == "credential_request_reused"


def test_a_weak_master_secret_is_refused(client, monkeypatch):
    monkeypatch.setenv(credentials.SECRET_ENV, "a" * 64)
    assert add(client).json()["code"] == "secret_key_missing"


def test_a_foreign_connection_cannot_be_named(client, project):
    c = made(client)
    pid = project["id"]
    client.post("/users", json={"email": "other@example.com"})
    other = client.post("/projects", json={"owner_id": client.get("/auth/me").json()["id"], "title": "T", "goal": "g"}).json()
    r = client.put(f"/settings/project/{other['id']}", json=route(c["ref"]))
    assert r.status_code == 422 and pid != other["id"]


# ---------- the gateway uses it for its owner's calls only ----------


class Recording(FakeProvider):
    def complete(self, request):
        self.keys = getattr(self, "keys", []) + [request.api_key]
        return super().complete(request)


def gateway(session, fake):
    return Gateway(session, providers={"anthropic": fake})


def call(session, fake, pid):
    return gateway(session, fake).call("research", system="s", user="u", project_id=pid)


def test_the_gateway_sends_the_owners_own_key_and_nothing_logs_it(client, session_factory, project):
    pid = project["id"]
    c = made(client)
    client.put(f"/settings/project/{pid}", json=route(c["ref"]))
    fake = Recording(replies=[{"ok": True}])
    with session_factory() as s:
        call(s, fake, pid)
        assert fake.keys == [KEY]
        assert KEY not in json.dumps([m.error or "" for m in s.scalars(select(ModelCall))])
        assert KEY not in json.dumps([e.payload for e in s.scalars(select(RunEvent))], default=str)
        assert KEY not in json.dumps([layer.values for layer in s.scalars(select(SettingsLayer))])
    # a route with no reference sends no key of its own
    client.put(f"/settings/project/{pid}", json={"models": {"research": {"provider": "anthropic", "model": "claude-sonnet-5"}}})
    fake2 = Recording(replies=[{"ok": True}])
    with session_factory() as s:
        call(s, fake2, pid)
    assert fake2.keys == [None]


def test_a_reference_in_a_shared_layer_is_ignored_for_anyone_else(client, session_factory, project):
    """The admin's global layer names the admin's connection; another account's project must never get that key —
    its call goes on with the server's own, as before the reference existed, instead of failing."""
    c = made(client)
    client.put("/settings/global/0", json=route(c["ref"]))
    client.post("/users", json={"email": "other@example.com"})
    other = client.post("/projects", json={"owner_id": client.get("/auth/me").json()["id"], "title": "T", "goal": "g"}).json()
    fake = Recording(replies=[{"ok": True}])
    with session_factory() as s:
        call(s, fake, other["id"])
    assert fake.keys == [None]  # the admin's key was not sent for someone else's call


@pytest.mark.parametrize("fix", ["revoke", "wrong_provider", "bad_secret"])
def test_a_reference_that_cannot_be_used_stops_the_call_before_it_is_made(client, session_factory, project, monkeypatch, fix):
    pid = project["id"]
    c = made(client)
    client.put(f"/settings/project/{pid}", json=route(c["ref"]))
    if fix == "revoke":
        client.delete(f"/credentials/{c['id']}")
    elif fix == "wrong_provider":
        with session_factory() as s:
            s.get(Credential, c["id"]).provider = "openai"
            s.commit()
    else:
        monkeypatch.setenv(credentials.SECRET_ENV, "another-master-secret-of-more-than-32-chars")
    fake = Recording(replies=[{"ok": True}])
    with session_factory() as s, pytest.raises(ProviderError) as e:
        call(s, fake, pid)
    assert fake.requests == [] and KEY not in str(e.value)
    with session_factory() as s:
        assert s.query(ModelCall).count() == 0  # no spend, no log of a call that never happened


def test_a_provider_error_that_quotes_the_key_is_redacted_everywhere(client, session_factory, project):
    pid = project["id"]
    c = made(client)
    client.put(f"/settings/project/{pid}", json=route(c["ref"]))

    class Quoting(FakeProvider):
        def complete(self, request):
            raise RuntimeError(f"401 invalid x-api-key {request.api_key} for this request")

    with session_factory() as s, pytest.raises(ProviderError) as e:
        call(s, Quoting(), pid)
    assert KEY not in str(e.value) and "[redacted]" in str(e.value)
    with session_factory() as s:
        [logged] = s.scalars(select(ModelCall)).all()
        assert logged.ok is False and KEY not in logged.error and "[redacted]" in logged.error


def test_a_builds_call_uses_the_builds_owners_key(client, session_factory):
    from tests.test_factory_intake import new

    c = made(client)
    client.put("/settings/global/0", json=route(c["ref"]))
    st = new(client)
    fake = Recording(replies=[{"ok": True}])
    with session_factory() as s:
        gateway(s, fake).call("research", system="s", user="u", factory_session_id=st["session"]["id"])
    assert fake.keys == [KEY]


# ---------- rotating the master secret ----------

OLD = "the-old-master-secret-of-more-than-32-chars"
NEW = "the-new-master-secret-of-more-than-32-chars"


def test_rotation_moves_every_key_to_the_new_secret_and_loses_none(client, session_factory, monkeypatch):
    monkeypatch.setenv(credentials.SECRET_ENV, OLD)
    a, b = made(client, name="A"), made(client, name="B", key="sk-test-second-key-0123456789ABCD")
    revoked = made(client, name="C")
    client.delete(f"/credentials/{revoked['id']}")

    # the new secret alone can't read them; with the old one as "previous" it can
    monkeypatch.setenv(credentials.SECRET_ENV, NEW)
    with session_factory() as s:
        owner = s.get(Credential, a["id"]).owner_id
        with pytest.raises(credentials.CredentialUnavailable):
            credentials.key_for_call(s, owner, a["ref"], "anthropic")
    monkeypatch.setenv(credentials.PREVIOUS_ENV, OLD)
    with session_factory() as s:
        assert credentials.key_for_call(s, owner, a["ref"], "anthropic") == KEY
        assert credentials.rotate(s) == {"rotated": 2, "unreadable": 0}  # the revoked one is not touched
        assert credentials.rotate(s) == {"rotated": 2, "unreadable": 0}  # and again: nothing breaks

    # now the old secret is no longer needed
    monkeypatch.delenv(credentials.PREVIOUS_ENV)
    with session_factory() as s:
        assert credentials.key_for_call(s, owner, a["ref"], "anthropic") == KEY
        assert credentials.key_for_call(s, owner, b["ref"], "anthropic") == "sk-test-second-key-0123456789ABCD"
    # new keys are made under the new secret
    assert add(client, name="D").status_code == 201


def test_a_key_neither_secret_opens_is_counted_and_left_alone(client, session_factory, monkeypatch):
    monkeypatch.setenv(credentials.SECRET_ENV, OLD)
    c = made(client)
    monkeypatch.setenv(credentials.SECRET_ENV, NEW)
    monkeypatch.setenv(credentials.PREVIOUS_ENV, "some-third-secret-that-is-long-enough-xx")
    with session_factory() as s:
        before = s.get(Credential, c["id"]).ciphertext
        assert credentials.rotate(s) == {"rotated": 0, "unreadable": 1}
        assert s.get(Credential, c["id"]).ciphertext == before
