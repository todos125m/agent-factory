import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool, StaticPool

from app.db import Base, SessionLocal, get_session, make_engine
from app.main import app
from app.registry import sync_from_files


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


@pytest.fixture
def client(session_factory):
    def override():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    # No `with`: the app lifespan (real DB file) must not run in tests.
    yield TestClient(app)
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
