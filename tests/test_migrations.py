"""Alembic migrations (migrations/, owner decision d34): an empty database, one the app built with create_all before
migrations existed, and one already migrated all end at exactly the models' schema, keeping every row."""

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app.db import Base, alembic_config, migrate


@pytest.fixture
def engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    yield e
    e.dispose()


def _diff(engine):
    with engine.connect() as c:
        return compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)


def _upgrade_to(engine, revision):
    config = alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def test_an_empty_database_gets_exactly_the_models_schema_and_a_rerun_changes_nothing(engine):
    migrate(engine)
    assert _diff(engine) == []
    migrate(engine)
    assert _diff(engine) == []


def test_a_database_from_before_login_keeps_its_rows_and_its_owner(engine):
    """The owner's database as PR1 left it (made by create_all, so no migration history): every account becomes
    claimable once, the first is the admin and owns the workspaces, and nothing is lost."""
    _upgrade_to(engine, "0001_baseline")
    with engine.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))  # create_all never made one
        c.execute(text("INSERT INTO users (id, email, created_at) VALUES (1, 'o@x', '2026-10-01'), (2, 'p@x', '2026-10-02')"))
        c.execute(text("INSERT INTO workspaces (id, name, created_at) VALUES (1, 'W', '2026-10-01')"))
        c.execute(text("INSERT INTO projects (id, owner_id, workspace_id, title, goal, mode, stage, paused, created_at) "
                       "VALUES (1, 1, 1, 'P', 'g', 'AUTOMATIC', 'IDEA', 0, '2026-10-01')"))
    migrate(engine)
    assert _diff(engine) == []
    with engine.connect() as c:
        users = c.execute(text("SELECT id, is_admin, claimable FROM users ORDER BY id")).all()
        assert [tuple(u) for u in users] == [(1, 1, 1), (2, 0, 1)]
        assert c.execute(text("SELECT owner_id FROM workspaces")).scalar() == 1
        assert c.execute(text("SELECT title FROM projects")).scalar() == "P"


def test_an_older_database_without_newer_tables_gets_them(engine):
    """Before PR1 there was no user_preferences table: the baseline creates only what is missing."""
    _upgrade_to(engine, "0001_baseline")
    with engine.begin() as c:
        c.execute(text("DROP TABLE alembic_version"))
        c.execute(text("DROP TABLE user_preferences"))
    migrate(engine)
    assert "user_preferences" in inspect(engine).get_table_names()
    assert _diff(engine) == []


def test_a_create_all_database_of_this_version_migrates_without_touching_anything(engine):
    Base.metadata.create_all(engine)
    with engine.begin() as c:
        c.execute(text("INSERT INTO users (id, email, created_at, is_admin, claimable) VALUES (1, 'o@x', '2026-10-01', 1, 0)"))
    migrate(engine)
    assert _diff(engine) == []
    with engine.connect() as c:
        assert tuple(c.execute(text("SELECT is_admin, claimable FROM users")).one()) == (1, 0)


def test_the_app_starts_on_migrations(monkeypatch, tmp_path):
    """The lifespan runs `migrate` (not create_all) on the configured database."""
    from fastapi.testclient import TestClient

    from app import db, main

    e = create_engine(f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setattr(main, "engine", e)
    monkeypatch.setattr(main, "SessionLocal", db.sessionmaker(bind=e, autoflush=False, expire_on_commit=False))
    with TestClient(main.app):
        pass
    assert "alembic_version" in inspect(e).get_table_names()
    assert _diff(e) == []
    e.dispose()
