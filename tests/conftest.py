import os
from contextlib import contextmanager

os.environ.setdefault("AGENT_FACTORY_WORKERS", "0")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from app import auth
from app.db import Base, SessionLocal, get_session, make_engine
from app.main import app
from app.models import Project, User
from app.registry import sync_from_files
from tests import postgres_limits

# SQLite stores what PostgreSQL (production) refuses: from here on every engine of every test refuses it too.
postgres_limits.install()


@pytest.fixture
def db_engine(request):
    """In memory by default: fast, but every session shares one connection, so SQLite's database-wide write
    lock can't show. Parametrize it indirectly with "file" for a file-based database like the default
    agent_factory.db, where each session gets its own connection and the lock is real."""
    if getattr(request, "param", "memory") == "file":
        engine = make_engine(f"sqlite:///{request.getfixturevalue('tmp_path') / 'agent_factory.db'}")
        assert isinstance(engine.pool, QueuePool)  # one connection per session, even within one thread
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(db_engine):
    Base.metadata.create_all(db_engine)
    # Production's session options (autoflush decides what a session writes before a model call).
    factory = sessionmaker(**{**SessionLocal.kw, "bind": db_engine})
    with factory() as s:
        sync_from_files(s)
    return factory


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


class ActingClient(TestClient):
    """The suite's client. Every route needs a signed-in account (app/auth.py), and most tests are about something
    else, so this client signs in for them the way the single owner used the app before login existed:

    - it acts as the account the test created last (`POST /users`): the first is the server's admin and is signed in
      by the API itself; a later one, which the API makes as the admin without switching, is signed in here;
    - a request before any account exists signs in a default admin first;
    - `as_admin()` runs a block as the admin (test setup that changes what every account shares: the global
      settings layer, the agent registry).

    The session cookie is kept here and sent as a header, never in httpx's jar. Login itself is tested with a plain
    TestClient (tests/test_auth.py), cookies and all."""

    _OPEN = ("/auth/", "/health", "/app")

    def __init__(self, factory):
        super().__init__(app)
        self._factory = factory
        self.token: str | None = None
        self.admin_token: str | None = None

    def sign_in_as(self, user_id: int) -> str:
        with self._factory() as s:
            token = auth.start_session(s, s.get(User, user_id))
            s.commit()
        self.token = token
        return token

    def _ensure_admin(self) -> str:
        if self.admin_token is None:
            with self._factory() as s:
                admin = s.scalar(select(User).where(User.is_admin).order_by(User.id))
                if admin is None:
                    admin = auth.create_account(s, "admin@test.local", "admin")
                token = auth.start_session(s, admin)
                s.commit()
            self.admin_token = token
        return self.admin_token

    @contextmanager
    def as_admin(self):
        saved = self.token
        self.token = self._ensure_admin()
        try:
            yield self
        finally:
            self.token = saved

    def request(self, method, url, *args, **kwargs):
        path = str(url)
        if self.token is None and not path.startswith(self._OPEN) and not (method.upper() == "POST" and path == "/users"):
            self.token = self._ensure_admin()
        if self.token is not None:
            kwargs["headers"] = {**(kwargs.get("headers") or {}), "cookie": f"{auth.COOKIE}={self.token}"}
        response = super().request(method, url, *args, **kwargs)
        self.cookies.clear()
        if method.upper() == "POST" and path == "/users" and response.status_code == 201:
            issued = response.cookies.get(auth.COOKIE)
            if issued:  # the server's first account: the admin, signed in by the API
                self.token = self.admin_token = issued
            else:
                self.sign_in_as(response.json()["id"])
        return response


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    # No `with`: the app lifespan (real DB file) must not run in tests.
    yield ActingClient(session_factory)
    app.dependency_overrides.clear()


@pytest.fixture
def project(client):
    user = client.post("/users", json={"email": "a@example.com"}).json()
    return client.post(
        "/projects", json={"owner_id": user["id"], "title": "SaaS idea", "goal": "Validate a SaaS for small firms"}
    ).json()


@pytest.fixture
def manual_project(client):
    """`project` defaults to mode "automatic" (the API default); this one waits for the owner at checkpoints."""
    user = client.post("/users", json={"email": "m@example.com"}).json()
    return client.post("/projects", json={
        "owner_id": user["id"], "title": "Manual idea", "goal": "Validate a SaaS for small firms",
        "mode": "manual_learning",
    }).json()


def owner_of(session, project_id: int) -> User:
    """The account a project belongs to: for tests that call a route function directly, as its owner would."""
    return session.get(User, session.get(Project, project_id).owner_id)
