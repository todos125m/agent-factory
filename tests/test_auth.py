"""Login and access control (app/auth.py, owner decision d33), with a plain TestClient: real cookies, no helpers."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import auth
from app.db import get_session
from app.main import app
from app.models import LoginCode, User, utcnow


@pytest.fixture
def raw(session_factory):
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield lambda: TestClient(app)  # each call: a separate browser
    app.dependency_overrides.clear()


def _admin(raw):
    browser = raw()
    r = browser.post("/users", json={"email": "owner@example.com"})
    assert r.status_code == 201, r.text
    return browser, r.json()["id"]


def _account_for(admin, raw, email):
    """The admin makes an account and hands it over with a one-time code; a new browser signs in with it."""
    uid = admin.post("/users", json={"email": email}).json()["id"]
    code = admin.post("/auth/login-codes", json={"user_id": uid}).json()["code"]
    browser = raw()
    assert browser.post("/auth/login", json={"code": code}).status_code == 200
    return browser, uid


# ---------- accounts and sessions ----------


def test_the_first_account_is_the_admin_and_is_signed_in_with_a_safe_cookie(raw):
    browser = raw()
    assert browser.get("/auth/state").json() == {"signed_in": False, "accounts_exist": False}
    r = browser.post("/users", json={"email": "owner@example.com"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    me = browser.get("/auth/me").json()
    assert me["is_admin"] is True and me["email"] == "owner@example.com"
    assert browser.get("/auth/state").json() == {"signed_in": True, "accounts_exist": True}


def test_after_the_first_account_nobody_signs_up_alone(raw):
    _admin(raw)
    stranger = raw()
    r = stranger.post("/users", json={"email": "x@example.com"})
    assert r.status_code == 401 and r.json()["code"] == "unauthenticated"


def test_only_the_admin_makes_accounts_and_stays_signed_in_as_themselves(raw):
    admin, admin_id = _admin(raw)
    member, member_id = _account_for(admin, raw, "m@example.com")
    assert admin.get("/auth/me").json()["id"] == admin_id
    assert member.get("/auth/me").json() == {"id": member_id, "email": "m@example.com", "name": None, "is_admin": False}
    r = member.post("/users", json={"email": "y@example.com"})
    assert r.status_code == 403 and r.json()["params"] == {"reason": "admin_only"}


def test_the_session_token_is_stored_only_as_a_hash(raw, session_factory):
    browser, _ = _admin(raw)
    token = browser.cookies.get(auth.COOKIE)
    with session_factory() as s:
        hashes = [row.token_hash for row in s.scalars(select(auth.AuthSession))]
    assert token not in hashes and auth._digest(token) in hashes


def test_logout_revokes_the_session(raw):
    browser, _ = _admin(raw)
    token = browser.cookies.get(auth.COOKIE)
    assert browser.post("/auth/logout").status_code == 204
    replay = raw()
    replay.cookies.set(auth.COOKIE, token)
    assert replay.get("/auth/me").status_code == 401


def test_an_expired_session_signs_no_one_in(raw, session_factory):
    browser, _ = _admin(raw)
    with session_factory() as s:
        for row in s.scalars(select(auth.AuthSession)):
            row.expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    assert browser.get("/auth/me").status_code == 401


@pytest.mark.parametrize("cookie", ["", "garbage", "x" * 5000])
def test_a_forged_cookie_is_a_401(raw, cookie):
    _admin(raw)
    browser = raw()
    browser.cookies.set(auth.COOKIE, cookie)
    assert browser.get("/projects").status_code == 401


# ---------- every route needs a signed-in account ----------

OPEN_ROUTES = {
    ("POST", "/users"),  # the server's first account; afterwards the admin's (tested above)
    ("GET", "/auth/state"), ("POST", "/auth/login"), ("POST", "/auth/claim"), ("POST", "/auth/logout"),
    ("GET", "/health"),
}


def test_every_route_but_the_open_ones_needs_a_session(raw):
    """A new route is closed unless it is listed here, with a reason: a forgotten check fails the suite."""
    _admin(raw)
    stranger = raw()
    checked = 0
    for path, operations in app.openapi()["paths"].items():
        for method in operations:
            if (method.upper(), path) in OPEN_ROUTES:
                continue
            concrete = path.replace("{scope}", "global")
            for name in ("project_id", "tid", "task_id", "user_id", "run_id", "scope_id", "name", "session_id", "rid"):
                concrete = concrete.replace("{" + name + "}", "1")
            assert "{" not in concrete, path
            r = stranger.request(method.upper(), concrete, json={})
            assert r.status_code == 401, (method, path, r.status_code)
            checked += 1
    assert checked > 40


# ---------- isolation between accounts ----------


def test_another_accounts_project_is_invisible_and_untouchable(raw):
    admin, _ = _admin(raw)
    alice, alice_id = _account_for(admin, raw, "alice@example.com")
    bob, _ = _account_for(admin, raw, "bob@example.com")
    pid = alice.post("/projects", json={"title": "A", "goal": "g"}).json()["id"]
    tid = alice.post(f"/projects/{pid}/tasks", json={"title": "t"}).json()["id"]

    for method, path, body in [
        ("GET", f"/projects/{pid}", None), ("GET", f"/projects/{pid}/events", None),
        ("GET", f"/projects/{pid}/tasks", None), ("GET", f"/projects/{pid}/tasks/{tid}", None),
        ("GET", f"/projects/{pid}/usage", None), ("GET", f"/projects/{pid}/memory", None),
        ("GET", f"/projects/{pid}/chat", None), ("GET", f"/projects/{pid}/model_calls", None),
        ("POST", f"/projects/{pid}/pause", {"paused": True}), ("POST", f"/projects/{pid}/plan/approve", None),
        ("POST", f"/projects/{pid}/tasks/{tid}/transition", {"status": "READY"}),
        ("POST", f"/projects/{pid}/memory", {"category": "brief", "title": "x", "content": "y"}),
        ("GET", f"/settings/project/{pid}", None), ("PUT", f"/settings/project/{pid}", {"depth": "deep"}),
        ("GET", f"/settings/task/{tid}", None), ("GET", "/settings/resolved?project_id=%d" % pid, None),
        ("POST", "/feedback", {"project_id": pid, "agent": "manager", "rating": "up"}),
        ("GET", f"/users/{alice_id}/preferences", None),
    ]:
        r = bob.request(method, path, json=body)
        assert r.status_code == 404, (method, path, r.status_code, r.text)  # the same as a missing one
    assert bob.get("/projects").json() == []
    assert bob.get("/inbox").json() == []
    r = bob.post("/projects", json={"owner_id": alice_id, "title": "B", "goal": "g"})
    assert r.status_code == 403
    assert alice.get(f"/projects/{pid}").json()["paused"] is False  # nothing of Bob's reached it


def test_workspaces_are_their_owners(raw):
    admin, _ = _admin(raw)
    alice, _ = _account_for(admin, raw, "alice@example.com")
    bob, _ = _account_for(admin, raw, "bob@example.com")
    ws = alice.post("/workspaces", json={"name": "W"}).json()["id"]
    assert [w["id"] for w in alice.get("/workspaces").json()] == [ws]
    assert bob.get("/workspaces").json() == []
    assert bob.get(f"/settings/workspace/{ws}").status_code == 404
    assert bob.post("/projects", json={"title": "B", "goal": "g", "workspace_id": ws}).status_code == 404


def test_feedback_lists_only_your_own(raw):
    admin, _ = _admin(raw)
    alice, _ = _account_for(admin, raw, "alice@example.com")
    bob, _ = _account_for(admin, raw, "bob@example.com")
    pid = alice.post("/projects", json={"title": "A", "goal": "g"}).json()["id"]
    alice.post("/feedback", json={"project_id": pid, "agent": "manager", "rating": "down", "note": "private"})
    assert len(alice.get("/agents/manager/feedback").json()) == 1
    assert bob.get("/agents/manager/feedback").json() == []


def test_feedback_cannot_point_at_a_task_of_another_project(raw):
    admin, _ = _admin(raw)
    p1 = admin.post("/projects", json={"title": "1", "goal": "g"}).json()["id"]
    p2 = admin.post("/projects", json={"title": "2", "goal": "g"}).json()["id"]
    t2 = admin.post(f"/projects/{p2}/tasks", json={"title": "t"}).json()["id"]
    r = admin.post("/feedback", json={"project_id": p1, "task_id": t2, "agent": "manager", "rating": "up"})
    assert r.status_code == 404


# ---------- what only the admin changes ----------


def test_shared_things_are_the_admins(raw):
    admin, _ = _admin(raw)
    member, _ = _account_for(admin, raw, "m@example.com")
    assert member.get("/settings/global/0").status_code == 200  # everyone reads what they inherit
    for method, path, body in [
        ("PUT", "/settings/global/0", {"depth": "deep"}),
        ("POST", "/agents", {"name": "x1", "description": "d", "model_role": "research", "capabilities": ["c"]}),
        ("POST", "/benchmarks/run", {"routes": [{"provider": "fake", "model": "m"}]}),
        ("GET", "/benchmarks", None),
        ("GET", "/users", None),
        ("POST", "/auth/login-codes", {"user_id": 1}),
    ]:
        r = member.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["params"] == {"reason": "admin_only"}, (path, r.text)
    assert admin.put("/settings/global/0", json={"depth": "deep"}).status_code == 200


# ---------- one-time login codes ----------


def test_a_login_code_works_once(raw):
    admin, admin_id = _admin(raw)
    code = admin.post("/auth/login-codes", json={}).json()["code"]
    phone = raw()
    assert phone.post("/auth/login", json={"code": code.lower().replace("-", " ")}).json()["id"] == admin_id
    again = raw()
    r = again.post("/auth/login", json={"code": code})
    assert r.status_code == 401 and r.json()["code"] == "login_code_invalid"


def test_an_expired_or_wrong_code_signs_no_one_in(raw, session_factory):
    admin, _ = _admin(raw)
    code = admin.post("/auth/login-codes", json={}).json()["code"]
    with session_factory() as s:
        for row in s.scalars(select(LoginCode)):
            row.expires_at = utcnow() - timedelta(seconds=1)
        s.commit()
    for attempt in (code, "AAAA-BBBB-CCCC", ""):
        r = raw().post("/auth/login", json={"code": attempt})
        assert r.status_code in (401, 422)


def test_a_code_is_random_and_never_stored(raw, session_factory):
    admin, _ = _admin(raw)
    codes = {admin.post("/auth/login-codes", json={}).json()["code"] for _ in range(20)}
    assert len(codes) == 20
    with session_factory() as s:
        stored = {row.code_hash for row in s.scalars(select(LoginCode))}
    assert not codes & stored and {auth._digest(auth.normalize_code(c)) for c in codes} == stored


# ---------- claiming an account from before login existed ----------


def _legacy(session_factory, email="legacy@example.com", admin=True):
    with session_factory() as s:
        user = User(email=email, claimable=True, is_admin=admin)
        s.add(user)
        s.commit()
        return user.id


def test_the_owners_old_browser_claims_its_account_once_from_this_machine(raw, session_factory):
    uid = _legacy(session_factory)
    browser = raw()
    r = browser.post("/auth/claim", json={"user_id": uid})
    assert r.status_code == 200 and browser.get("/auth/me").json()["id"] == uid
    r = raw().post("/auth/claim", json={"user_id": uid})
    assert r.status_code == 404  # once only


def test_behind_a_proxy_a_claim_needs_the_owner_token(raw, session_factory, monkeypatch):
    uid = _legacy(session_factory)
    proxied = {"x-forwarded-for": "203.0.113.9"}
    r = raw().post("/auth/claim", json={"user_id": uid}, headers=proxied)
    assert r.status_code == 403
    monkeypatch.setenv("AGENT_FACTORY_OWNER_TOKEN", "s3cret-token")
    assert raw().post("/auth/claim", json={"user_id": uid, "owner_token": "wrong"}, headers=proxied).status_code == 403
    assert raw().post("/auth/claim", json={"user_id": uid, "owner_token": "s3cret-token"},
                      headers=proxied).status_code == 200


def test_a_new_account_is_never_claimable(raw):
    _, admin_id = _admin(raw)
    assert raw().post("/auth/claim", json={"user_id": admin_id}).status_code == 404
