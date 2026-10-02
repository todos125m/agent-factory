"""The suite's SQLite refuses what PostgreSQL refuses (tests/postgres_limits.py), on every way a value is written.

A column nobody guards would otherwise pass every test here and fail only in production: as a 500 at the store, after
the paid model call when the text came from a model, again on every retry. FakeProvider only.
"""

import json

import pytest
from sqlalchemy import JSON, insert, select, update
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.db import Base, make_engine
from app.models import Task, Workspace
from tests import postgres_limits
from tests.postgres_limits import VERDICTS


@pytest.mark.parametrize("label, column_type, value, why", VERDICTS, ids=[v[0] for v in VERDICTS])
def test_the_verdicts_measured_on_postgresql(label, column_type, value, why):
    processed = json.dumps(value) if isinstance(column_type, JSON) else value  # what a JSON column's bind hands over
    assert postgres_limits.refusal(column_type, processed) == why


def test_an_over_long_value_is_refused_by_the_suites_own_session(session):
    """The fixtures every test uses, not a special engine: the bug that went unnoticed was exactly this store."""
    session.add(Workspace(name="x" * 200))
    session.commit()  # exactly the column's length: stored

    session.add(Workspace(name="x" * 201))
    with pytest.raises(DataError, match=r"value too long for type character varying\(200\)"):
        session.commit()
    session.rollback()  # like PostgreSQL's aborted transaction: nothing else works until then


def test_a_session_that_met_a_refusal_must_roll_back_like_postgresql(session):
    session.add(Workspace(name="x" * 201))
    with pytest.raises(DataError):
        session.flush()
    with pytest.raises(Exception, match="rolled back|PendingRollback|inactive"):
        session.execute(select(Workspace))
    session.rollback()
    assert session.scalars(select(Workspace)).all() == []


def test_every_row_of_a_batch_is_checked(session):
    """One flush of several rows is several statements (or one executemany): the last row is checked too."""
    session.add_all([Workspace(name="a"), Workspace(name="b"), Workspace(name="c" * 201)])
    with pytest.raises(DataError):
        session.flush()
    session.rollback()
    assert session.scalars(select(Workspace)).all() == []


def test_an_update_is_checked_including_an_executemany_one(session):
    session.add_all([Workspace(name=f"w{i}") for i in range(3)])
    session.commit()
    workspaces = session.scalars(select(Workspace).order_by(Workspace.id)).all()
    for w in workspaces[:2]:
        w.name = "y" * 200
    session.commit()  # two rows with the same columns: one executemany, all within the limit

    workspaces[0].name = "z" * 200
    workspaces[2].name = "z" * 201  # the third of a batch
    with pytest.raises(DataError, match=r"character varying\(200\)"):
        session.commit()
    session.rollback()


def test_core_and_bulk_statements_are_checked_too(session):
    with pytest.raises(DataError):
        session.execute(insert(Workspace).values(name="x" * 201))
    session.rollback()
    with pytest.raises(DataError):
        session.execute(insert(Workspace), [{"name": "ok"}, {"name": "x" * 201}])  # an ORM bulk insert: executemany
    session.rollback()
    session.add(Workspace(name="a"))
    session.commit()
    with pytest.raises(DataError):
        session.execute(update(Workspace).values(name="x" * 201))
    session.rollback()


def test_integers_and_json_are_checked_too(session):
    session.add(Task(project_id=1, title="t", priority=2**31))
    with pytest.raises(DataError, match="integer out of range"):
        session.flush()
    session.rollback()
    session.add(Task(project_id=1, title="t", input={"x": float("nan")}))
    with pytest.raises(DataError, match="invalid input syntax for type json"):
        session.flush()
    session.rollback()


def test_an_engine_a_test_builds_itself_is_covered_too():
    """The check is on the Engine class, not on the fixtures' engines."""
    engine = make_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Workspace(name="x" * 201))
        with pytest.raises(DataError):
            s.commit()
    engine.dispose()


def test_what_is_only_read_is_not_checked(session):
    """A comparison with a longer value is a plain query on PostgreSQL too (it just finds nothing)."""
    assert session.scalars(select(Workspace).where(Workspace.name == "x" * 500)).all() == []
    assert session.scalars(select(Task).where(Task.priority == 2**40)).all() == []


def test_the_where_clause_of_an_update_is_a_comparison_not_a_write(session):
    session.add(Workspace(name="kept"))
    session.commit()
    session.execute(update(Workspace).where(Workspace.name == "x" * 500).values(name="not touched"))  # matches nothing
    session.execute(update(Workspace).where(Workspace.name == "kept").values(name="y" * 200))  # its SET is what counts
    session.commit()
    with pytest.raises(DataError, match=r"character varying\(200\)"):
        session.execute(update(Workspace).where(Workspace.name == "x" * 500).values(name="z" * 201))
    session.rollback()


def test_the_escape_hatch_is_explicit_and_scoped(session):
    with postgres_limits.unenforced():
        session.add(Workspace(name="x" * 201))  # a row only a SQLite database could hold
        session.commit()
    session.add(Workspace(name="y" * 201))
    with pytest.raises(DataError):  # enforced again right after
        session.commit()
    session.rollback()
