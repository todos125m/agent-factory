import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import Base, get_session
from app.main import app


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        with TestSession() as session:
            yield session

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def project(client):
    user = client.post("/users", json={"email": "a@example.com"}).json()
    return client.post(
        "/projects", json={"owner_id": user["id"], "title": "SaaS idea", "goal": "Validate a SaaS for small firms"}
    ).json()
