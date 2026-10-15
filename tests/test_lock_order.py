"""Every locker takes the project row before any other row it writes (app/pause.py::_paused). PostgreSQL's row locks
would otherwise deadlock one that wrote its task rows first (a reject deleting a task) against one that holds the
project row and then wants those rows (a completion promoting that task): a 500, and a paid result rolled back.
SQLite can't show a deadlock, but the order of the statements is the same there, so it is pinned from the SQL a
request emits (a read that locks is tagged: SQLite renders no lock clause, and an unlocked read of `paused` has
the same text); the lock mode is pinned compiled for PostgreSQL. A task a model call is about to move is read again
under the project lock, and only then written (app/pause.py::lock_task). FakeProvider only.
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql

from app import manager, pause
from app.gateway.providers import FakeProvider
from app.models import Project, Task
from tests.test_budget_holds import _call
from tests.test_manager import CHECKPOINT, _ready_task
from tests.test_project_pause import ActsDuringCall, _running_task, use_real_gateway
from tests.test_task_changed_during_call import S, _moves
from tests.test_task_run import PLAN, RUN_RESULT

LOCK = "LOCKING SELECT projects.paused FROM projects WHERE projects.id = ?"


@contextmanager
def sql_log(engine):
    log = []

    def record(conn, cursor, statement, parameters, context, executemany):
        sql = " ".join(statement.split())
        compiled = getattr(context, "compiled", None)
        if getattr(getattr(compiled, "statement", None), "_for_update_arg", None) is not None:
            sql = "LOCKING " + sql
        log.append(sql)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield log
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _is_write(sql):
    return sql.startswith(("INSERT", "UPDATE", "DELETE"))


def _past_the_calls_log(log):
    """Where the request's own work resumes: after the gateway's log of its model call (that call's ModelCall row,
    then its hold's release)."""
    logged = next(i for i, sql in enumerate(log) if sql.startswith("INSERT INTO model_calls"))
    return 1 + next(i for i in range(logged, len(log)) if log[i].startswith("DELETE FROM budget_holds"))


def _first_write_follows_the_lock(log, *, after_a_model_call=True):
    """The first write of the request, past the gateway's own log of its model call, comes straight after a locking
    read of `paused`: nothing was written before the project row was locked."""
    start = _past_the_calls_log(log) if after_a_model_call else 0
    first_write = next(i for i in range(start, len(log)) if _is_write(log[i]))
    assert log[first_write - 1] == LOCK, log[max(start - 1, 0):first_write + 1]


def _the_task_is_read_again_under_the_lock(log):
    """Past the model call the project row is locked and what the request staged is flushed, and only then is the
    task row locked and read again (straight after the lock's own read of `paused`, which follows that flush): a move
    made during the call is seen before the task is changed, SQLite holds its write lock by then (the flush), and on
    PostgreSQL a transition that takes no project lock (a cancel) waits for the commit instead of landing between this
    read and the write. Returns the log from the re-read on."""
    start = _past_the_calls_log(log)
    _first_write_follows_the_lock(log)
    first_write = next(i for i in range(start, len(log)) if _is_write(log[i]))
    reread = next(i for i in range(start, len(log)) if log[i].startswith("LOCKING SELECT tasks."))
    assert first_write < reread and log[reread - 1] == LOCK, log[start:reread + 1]
    written = [sql for sql in log[start:reread] if sql.startswith("UPDATE tasks")]
    assert not written, "the task was written before it was read again"
    return log[reread + 1:]


@pytest.mark.parametrize("locker", [
    lambda session, loaded: pause.lock_project(session, loaded.id),
    lambda session, loaded: manager._paused_now(session, loaded),
], ids=["lock_project", "completion_check"])
def test_the_lock_is_taken_before_the_callers_work_is_written_and_read_again_after(session, project, locker):
    loaded = session.get(Project, project["id"])
    session.add(Task(project_id=loaded.id, title="staged"))  # the caller's work, not yet written
    with sql_log(session.get_bind()) as log:
        assert locker(session, loaded) is False
    assert log[0] == LOCK and log[-1] == LOCK and any(_is_write(sql) for sql in log[1:-1]), log


def test_every_locker_locks_the_project_row_for_no_key_update_on_postgresql(session, project):
    """Both reads of the lock (before the flush, after it) carry the clause: FOR NO KEY UPDATE conflicts with the
    pause's UPDATE and with the other lockers, but not with the KEY SHARE that every insert of a child row takes
    (a paid call's model_calls row, say), which FOR UPDATE would block while the lock is held."""
    session.add(Task(project_id=project["id"], title="staged"))
    statements = []
    event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))

    manager._paused_now(session, session.get(Project, project["id"]))
    locks = [str(st.compile(dialect=postgresql.dialect())) for st in statements if "FOR" in str(st)]
    assert len(locks) == 2 and all("FROM projects" in sql and sql.endswith("FOR NO KEY UPDATE") for sql in locks)


def test_a_reservation_locks_before_it_writes_its_hold(session, project):
    with sql_log(session.get_bind()) as log:
        _call(session, FakeProvider(), project["id"])
    hold = next(i for i, sql in enumerate(log) if sql.startswith("INSERT INTO budget_holds"))
    assert log[hold - 1] == LOCK and log[hold + 1] == LOCK


def test_a_calls_log_inserts_its_row_before_it_releases_the_hold(session, project):
    """The ModelCall's foreign keys take their shared lock on the call's task first; a delete of that task (a
    rejected plan's) locks the task and then updates the hold (SET NULL): the opposite order would deadlock."""
    with sql_log(session.get_bind()) as log:
        _call(session, FakeProvider(), project["id"])
    logged = next(i for i, sql in enumerate(log) if sql.startswith("INSERT INTO model_calls"))
    released = next(i for i, sql in enumerate(log) if sql.startswith("DELETE FROM budget_holds"))
    assert logged < released


def test_a_completion_locks_before_it_writes_its_result(client, session_factory, project, monkeypatch):
    pid = project["id"]
    t = _running_task(client, pid)
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[RUN_RESULT]))
    with sql_log(session_factory.kw["bind"]) as log:
        assert client.post(f"/projects/{pid}/tasks/{t['id']}/run").status_code == 200
    _first_write_follows_the_lock(log)


def test_an_automatic_decision_locks_before_it_writes_its_checkpoint(client, session_factory, project, monkeypatch):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[CHECKPOINT]))
    with sql_log(session_factory.kw["bind"]) as log:
        r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is True, r.text
    _first_write_follows_the_lock(log)


def test_a_reject_locks_before_it_deletes_the_old_plans_tasks(client, session_factory, project, monkeypatch):
    pid = project["id"]
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[PLAN, PLAN]))
    client.post(f"/projects/{pid}/plan")
    with sql_log(session_factory.kw["bind"]) as log:
        assert client.post(f"/projects/{pid}/plan/reject", json={"feedback": "too shallow"}).status_code == 200
    assert any(sql.startswith("DELETE FROM tasks") for sql in log)
    _first_write_follows_the_lock(log)


def test_an_approve_locks_before_it_moves_the_tasks(client, session_factory, project, monkeypatch):
    pid = project["id"]
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[PLAN]))
    client.post(f"/projects/{pid}/plan")
    with sql_log(session_factory.kw["bind"]) as log:
        assert client.post(f"/projects/{pid}/plan/approve").status_code == 200
    _first_write_follows_the_lock(log, after_a_model_call=False)


def test_a_decision_locks_before_it_moves_the_task(client, session_factory, manual_project):
    pid = manual_project["id"]
    t = _ready_task(client, pid, risk="low")
    with sql_log(session_factory.kw["bind"]) as log:
        assert client.post(f"/projects/{pid}/tasks/{t['id']}/decide", json={"option": 0}).status_code == 200
    _first_write_follows_the_lock(log, after_a_model_call=False)


def test_a_transition_to_ready_locks_before_it_moves_the_task(client, session_factory, project):
    pid = project["id"]
    t = client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()
    with sql_log(session_factory.kw["bind"]) as log:
        r = client.post(f"/projects/{pid}/tasks/{t['id']}/transition", json={"status": "READY"})
    assert r.status_code == 200, r.text
    _first_write_follows_the_lock(log, after_a_model_call=False)


# ---------- a task a model call is about to move is read again under the lock, and only then written ----------


def test_a_completion_reads_its_task_again_under_the_lock_before_it_writes_it(
    client, session_factory, project, monkeypatch
):
    pid = project["id"]
    t = _running_task(client, pid)
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[RUN_RESULT]))
    with sql_log(session_factory.kw["bind"]) as log:
        assert client.post(f"/projects/{pid}/tasks/{t['id']}/run").status_code == 200
    after = _the_task_is_read_again_under_the_lock(log)
    assert any(sql.startswith("UPDATE tasks") for sql in after), after  # the completion, written after the re-read


def test_an_automatic_decision_reads_its_task_again_under_the_lock_before_it_writes_it(
    client, session_factory, project, monkeypatch
):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_real_gateway(client, monkeypatch, FakeProvider(replies=[CHECKPOINT]))
    with sql_log(session_factory.kw["bind"]) as log:
        r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is True, r.text
    after = _the_task_is_read_again_under_the_lock(log)
    assert any(sql.startswith("UPDATE tasks") for sql in after), after  # the decision, written after the re-read


def test_a_run_refused_for_a_moved_task_writes_nothing_but_its_record(client, session_factory, project, monkeypatch):
    """The staged result is rolled back with the locks; the only write after the re-read is the event that says why."""
    pid = project["id"]
    t = _running_task(client, pid)
    use_real_gateway(
        client, monkeypatch,
        ActsDuringCall([RUN_RESULT], lambda: _moves(session_factory, pid, t["id"], S.FAILED)),
    )
    with sql_log(session_factory.kw["bind"]) as log:
        assert client.post(f"/projects/{pid}/tasks/{t['id']}/run").status_code == 409
    after = _the_task_is_read_again_under_the_lock(log)
    assert len(after) == 1 and after[0].startswith("INSERT INTO run_events"), after


def test_a_withheld_decision_writes_nothing_but_its_record_after_the_re_read(
    client, session_factory, project, monkeypatch
):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_real_gateway(
        client, monkeypatch,
        ActsDuringCall([CHECKPOINT], lambda: _moves(session_factory, pid, t["id"], S.CANCELLED)),
    )
    with sql_log(session_factory.kw["bind"]) as log:
        r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is False, r.text
    after = _the_task_is_read_again_under_the_lock(log)
    assert len(after) == 1 and after[0].startswith("INSERT INTO run_events"), after


def test_the_task_is_locked_for_no_key_update_on_postgresql(client, session, project):
    """FOR NO KEY UPDATE, like the project row's lock: it makes a transition that takes no project lock wait for the
    caller's commit, but not the child rows the caller writes (their KEY SHARE), which FOR UPDATE would block."""
    t = client.post(f"/projects/{project['id']}/tasks", json={"title": "T"}).json()
    statements = []
    event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))

    assert pause.lock_task(session, t["id"]) is not None
    sql = str(statements[-1].compile(dialect=postgresql.dialect()))
    assert "FROM tasks" in sql and sql.endswith("FOR NO KEY UPDATE"), sql
