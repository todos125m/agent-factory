import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_session
from app.main import app
from app.registry import sync_from_files


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
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
