"""A String(n) column widened after its table was created is widened in the existing database at startup.

`create_all` never alters a table, so without this a database made before learning_traces.concept was widened (200 ->
300, to hold a task's title) would keep refusing the long titles on PostgreSQL. app/db.py::ensure_column_lengths does it,
like ensure_indexes: idempotent, PostgreSQL only (SQLite enforces no length and can't alter a column's type), widening
only. SQLite's reflection reports a VARCHAR's declared length, so the detection is tested here against a database built
with the old width; the ALTER itself runs on a real server in tests/test_postgres_parity.py (AGENT_FACTORY_TEST_PG_URL).
"""

from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateTable

from app.db import Base, ensure_column_lengths, make_engine, narrower_columns, widen_statement
from app.main import app
from app.models import LearningTrace


def _database_with_concept_of(tmp_path, length):
    """A database as an older version created it: learning_traces.concept `length` long."""
    engine = make_engine(f"sqlite:///{tmp_path / 'agent_factory.db'}")
    Base.metadata.create_all(engine)
    ddl = str(CreateTable(LearningTrace.__table__).compile(engine)).replace("concept VARCHAR(300)", f"concept VARCHAR({length})")
    assert f"concept VARCHAR({length})" in ddl
    with engine.begin() as c:
        c.execute(text("DROP TABLE learning_traces"))
        c.execute(text(ddl))
    return engine


def test_a_database_made_by_the_current_models_has_nothing_to_widen(tmp_path):
    engine = _database_with_concept_of(tmp_path, 300)
    assert narrower_columns(engine) == []
    engine.dispose()


def test_a_database_from_before_the_widening_is_found(tmp_path):
    engine = _database_with_concept_of(tmp_path, 200)
    assert [(table.name, column.name, length) for table, column, length in narrower_columns(engine)] == [
        ("learning_traces", "concept", 300)
    ]
    engine.dispose()


def test_a_column_longer_in_the_database_than_in_the_models_is_left_alone(tmp_path):
    """Only ever widens: shrinking could cut what is stored."""
    engine = _database_with_concept_of(tmp_path, 400)
    assert narrower_columns(engine) == []
    engine.dispose()


def test_a_table_the_database_does_not_have_yet_is_not_an_error(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agent_factory.db'}")  # nothing created
    assert narrower_columns(engine) == []
    engine.dispose()


def test_sqlite_is_left_alone(tmp_path):
    """No length to refuse there, and SQLite can't alter a column's type: nothing is attempted, nothing lost."""
    engine = _database_with_concept_of(tmp_path, 200)
    with engine.begin() as c:
        c.execute(insert(LearningTrace).values(project_id=1, concept="kept", explanation="e"))  # (SQLite: no FK check)
    ensure_column_lengths(engine)
    ensure_column_lengths(engine)
    with engine.connect() as c:
        assert c.scalar(select(LearningTrace.concept)) == "kept"
    engine.dispose()


def test_the_statement_postgresql_runs():
    table = LearningTrace.__table__
    assert widen_statement(postgresql.dialect(), table, table.c.concept, 300) == (
        "ALTER TABLE learning_traces ALTER COLUMN concept TYPE VARCHAR(300)"
    )


def test_startup_widens_an_existing_database(tmp_path, monkeypatch):
    """The lifespan (not only the function) does it: an upgraded database must not stay behind."""
    engine = make_engine(f"sqlite:///{tmp_path / 'agent_factory.db'}")
    monkeypatch.setattr("app.main.engine", engine)  # the lifespan runs on this database, not the real file
    monkeypatch.setattr("app.main.SessionLocal", sessionmaker(bind=engine, autoflush=False, expire_on_commit=False))
    seen = []
    monkeypatch.setattr("app.main.ensure_column_lengths", lambda bind: seen.append(bind))

    with TestClient(app):
        pass
    assert seen == [engine]
    engine.dispose()
