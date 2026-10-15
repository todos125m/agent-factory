"""The emulation (tests/postgres_limits.py) against a real PostgreSQL, so it can't drift from what the server does.

Skipped unless AGENT_FACTORY_TEST_PG_URL names a server (and `pip install -e ".[postgres]"` is done), e.g. the
docker-compose one:

    AGENT_FACTORY_TEST_PG_URL=postgresql+psycopg://user:password@localhost:5432/agent_factory pytest tests/test_postgres_parity.py

Every test works in a schema of its own that is dropped afterwards, so no table of the database is read or touched.
The value checks of the rest of the suite stay on SQLite; this file is what says the SQLite side is right.
"""

import os
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import JSON, BigInteger, Column, Integer, MetaData, SmallInteger, String, Table, Text, create_engine, exc, insert, text
from sqlalchemy.orm import sessionmaker

from app.db import Base, SessionLocal, ensure_column_lengths, get_session, narrower_columns
from app.gateway.providers import FakeProvider
from app.main import app
from app.registry import sync_from_files
from tests.postgres_limits import VERDICTS
from tests.test_column_limits import API, Ctx, limited_columns
from tests.test_project_pause import use_provider
from tests.test_task_run import RUN_RESULT, use_fake_role

PG_URL = os.getenv("AGENT_FACTORY_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="set AGENT_FACTORY_TEST_PG_URL to run against a real PostgreSQL")


@pytest.fixture
def pg_engine():
    schema = f"af_test_{uuid.uuid4().hex[:12]}"
    admin = create_engine(PG_URL)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(PG_URL, connect_args={"options": f"-csearch_path={schema}"})
    yield engine
    engine.dispose()
    with admin.begin() as c:
        c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def pg_client(pg_engine):
    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(**{**SessionLocal.kw, "bind": pg_engine})
    with factory() as s:
        sync_from_files(s)

    def override():
        with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app), factory
    app.dependency_overrides.clear()


# The column each kind of verdict is stored in.
_COLUMNS = {String: "v5", Text: "tx", Integer: "i", SmallInteger: "s", BigInteger: "b", JSON: "j"}


@pytest.mark.parametrize("label, column_type, value, why", VERDICTS, ids=[v[0] for v in VERDICTS])
def test_the_server_gives_the_verdict_the_emulation_expects(pg_engine, label, column_type, value, why):
    metadata = MetaData()
    verdicts = Table(
        "verdicts", metadata, Column("id", Integer, primary_key=True), Column("v5", String(5)), Column("tx", Text),
        Column("i", Integer), Column("s", SmallInteger), Column("b", BigInteger), Column("j", JSON),
    )
    metadata.create_all(pg_engine)
    column = _COLUMNS[type(column_type)]
    try:
        with pg_engine.begin() as c:
            c.execute(insert(verdicts).values({column: value}))
    except exc.DataError as e:
        assert why is not None, f"the server refused what the emulation allows: {e.orig}"
        assert why in str(e.orig)
    else:
        assert why is None, f"the server stored what the emulation refuses ({why})"


def test_every_request_field_takes_its_longest_value_on_the_server(pg_client):
    """The emulation says a value of exactly the column's length is stored; the server agrees, for every column an API
    request body can fill (and one more character is a 422 before the store, with no database involved)."""
    client, factory = pg_client
    use_provider(client, factory, FakeProvider())
    user = client.post("/users", json={"email": "o@example.com"}).json()
    project = client.post("/projects", json={"owner_id": user["id"], "title": "p", "goal": "g"}).json()
    ctx = Ctx(uid=user["id"], pid=project["id"])
    for column, (request, _field) in sorted(API.items()):
        n = limited_columns()[column]
        assert request(client, ctx, "x" * n).status_code in (200, 201), column
        assert request(client, ctx, "x" * (n + 1)).status_code == 422, column


def test_a_completed_task_keeps_its_whole_title_on_the_server(pg_client):
    """The case that found this: a 201-300 character title was a 500 right after the paid run in manual_learning mode."""
    client, factory = pg_client
    user = client.post("/users", json={"email": "o@example.com"}).json()
    project = client.post("/projects", json={
        "owner_id": user["id"], "title": "p", "goal": "g", "mode": "manual_learning"}).json()
    pid, title = project["id"], "T" * 300
    t = client.post(f"/projects/{pid}/tasks", json={"title": title, "owner": "researcher"}).json()
    for status in ("READY", "RUNNING"):
        client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": status})
    use_fake_role(client, factory, "research", [RUN_RESULT])

    assert client.post(f"/projects/{pid}/tasks/{t['id']}/run").status_code == 200
    assert [lt["concept"] for lt in client.get(f"/projects/{pid}/learning").json()] == [title]


def test_a_database_from_before_the_widening_is_widened_without_losing_a_row(pg_engine, caplog):
    Base.metadata.create_all(pg_engine)
    with pg_engine.begin() as c:  # as the previous version created it
        c.execute(text("ALTER TABLE learning_traces ALTER COLUMN concept TYPE VARCHAR(200)"))
        c.execute(text("INSERT INTO users (email, created_at) VALUES ('o@example.com', now())"))
        c.execute(text("INSERT INTO projects (owner_id, title, goal, mode, stage, paused, created_at) "
                       "VALUES (1, 'p', 'g', 'AUTOMATIC', 'IDEA', false, now())"))
        c.execute(text("INSERT INTO learning_traces (project_id, concept, explanation, created_at) "
                       "VALUES (1, 'kept', 'e', now())"))
    assert [(t.name, c.name, n) for t, c, n in narrower_columns(pg_engine)] == [("learning_traces", "concept", 300)]

    ensure_column_lengths(pg_engine)
    ensure_column_lengths(pg_engine)  # idempotent

    assert narrower_columns(pg_engine) == []
    assert caplog.text.count("widened learning_traces.concept to VARCHAR(300)") == 1  # said once, on the run that did it
    with pg_engine.begin() as c:
        assert c.scalar(text("select concept from learning_traces")) == "kept"
        c.execute(text("INSERT INTO learning_traces (project_id, concept, explanation, created_at) "
                       "VALUES (1, :c, 'e', now())"), {"c": "T" * 300})


def test_a_widening_that_cannot_get_its_lock_fails_fast_instead_of_hanging_the_startup(pg_engine, monkeypatch):
    Base.metadata.create_all(pg_engine)
    with pg_engine.begin() as c:
        c.execute(text("ALTER TABLE learning_traces ALTER COLUMN concept TYPE VARCHAR(200)"))
    monkeypatch.setattr("app.db.LOCK_TIMEOUT", "1s")
    holder = pg_engine.connect()  # a transaction that never ends, as a stuck instance of the previous release would hold
    try:
        holder.execute(text("LOCK TABLE learning_traces IN ACCESS SHARE MODE"))
        started = time.monotonic()
        with pytest.raises(exc.OperationalError, match="lock timeout"):
            ensure_column_lengths(pg_engine)
        assert time.monotonic() - started < 10
    finally:
        holder.rollback()
        holder.close()
    ensure_column_lengths(pg_engine)  # the lock is free again: nothing was left half done
    assert narrower_columns(pg_engine) == []


def test_a_column_longer_on_the_server_than_in_the_models_is_never_shrunk(pg_engine):
    Base.metadata.create_all(pg_engine)
    with pg_engine.begin() as c:
        c.execute(text("ALTER TABLE learning_traces ALTER COLUMN concept TYPE VARCHAR(500)"))
    ensure_column_lengths(pg_engine)
    with pg_engine.connect() as c:
        assert c.scalar(text(
            "select character_maximum_length from information_schema.columns "
            "where table_schema = current_schema() and table_name = 'learning_traces' and column_name = 'concept'"
        )) == 500
