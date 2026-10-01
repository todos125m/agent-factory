"""Every paid call sums its project's and its task's logged cost (and in-flight holds) while it holds the project's
queue (app/gateway/service.py), so those columns are indexed: the time inside the queue must not grow with every
call ever logged. `create_all` only creates the indexes of the tables it creates, so databases made before an index
was declared get it from app/db.py::ensure_indexes at startup.
"""

from sqlalchemy import inspect, text

from app.db import Base, ensure_indexes, make_engine

BUDGET_INDEXES = {
    "ix_model_calls_project_id", "ix_model_calls_task_id", "ix_budget_holds_project_id", "ix_budget_holds_task_id",
}


def _indexes(engine):
    return {i["name"] for table in ("model_calls", "budget_holds") for i in inspect(engine).get_indexes(table)}


def test_the_budget_sums_use_an_index(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agent_factory.db'}")
    Base.metadata.create_all(engine)
    assert BUDGET_INDEXES <= _indexes(engine)
    with engine.connect() as c:  # the statement spent_usd runs for a project
        plan = c.execute(text(
            "EXPLAIN QUERY PLAN SELECT coalesce(sum(model_calls.cost_usd), 0.0) FROM model_calls "
            "WHERE model_calls.project_id = 1"
        )).all()
    assert "USING INDEX ix_model_calls_project_id" in str(plan)
    engine.dispose()


def test_ensure_indexes_adds_what_create_all_leaves_out_of_an_existing_table(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'agent_factory.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as c:  # a database from before the indexes were declared
        c.execute(text("DROP INDEX ix_model_calls_project_id"))
        c.execute(text("DROP INDEX ix_budget_holds_task_id"))
    Base.metadata.create_all(engine)  # what every startup did until now: leaves existing tables alone
    assert BUDGET_INDEXES - _indexes(engine) == {"ix_model_calls_project_id", "ix_budget_holds_task_id"}

    ensure_indexes(engine)
    assert BUDGET_INDEXES <= _indexes(engine)
    ensure_indexes(engine)  # idempotent
    assert BUDGET_INDEXES <= _indexes(engine)
    engine.dispose()
