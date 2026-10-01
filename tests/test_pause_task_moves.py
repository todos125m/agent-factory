"""The task-move half of the pause backstop (owner decision d16): every task move in app/ goes through
app/pause.py::check_task_move, which gives check_task_transition a fresh `paused`, so a paused project's task
doesn't move to READY/RUNNING on a path that skips app/manager.py::ensure_active (e.g. a future bulk approve);
approve, decide and a transition re-check under a lock as they commit (commit_unless_paused), so a pause that
lands after the check is caught too. A refusal is HTTP 409 plus `pause.blocked` and rolls back the request's
uncommitted moves. The entry checks, the gateway's backstop and the mid-flight handling (incl. resume's
promotions, which go through check_task_move too) are in tests/test_project_pause.py. No model calls.
"""

import ast
import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app import manager, pause
from app.models import Task, TaskStatus
from app.routers.projects import set_paused
from app.schemas import ProjectPauseUpdate
from app.state_machine import TASK_TRANSITIONS, ProjectPaused, TransitionError, check_task_transition
from tests.test_manager import _ready_task
from tests.test_project_pause import _assert_refused, _events, _pause, _status, file_client  # noqa: F401

ALLOWED = {"dependency_statuses": [], "retries": 0, "max_retries": 2}
STARTS = {TaskStatus.READY, TaskStatus.RUNNING}  # d16's words, not app.state_machine.START_STATUSES under test
MOVES = [(c, t) for c, targets in TASK_TRANSITIONS.items() for t in sorted(targets)]


def _created_task(client, pid):
    return client.post(f"/projects/{pid}/tasks", json={"title": "T"}).json()


def _owner_pauses(session_factory, pid):
    with session_factory() as s:  # the owner's pause request, in its own session
        set_paused(pid, ProjectPauseUpdate(paused=True), session=s)


# ---------- the rule (pure) ----------


@pytest.mark.parametrize("current, target", MOVES, ids=[f"{c.value}->{t.value}" for c, t in MOVES])
def test_the_rule_refuses_every_move_to_ready_or_running_while_paused(current, target):
    check_task_transition(current, target, **ALLOWED, paused=False)
    if target in STARTS:
        with pytest.raises(ProjectPaused, match="Project is paused"):
            check_task_transition(current, target, **ALLOWED, paused=True)
    else:  # a move that starts nothing (cancel, complete, fail, ...) still goes through while paused
        check_task_transition(current, target, **ALLOWED, paused=True)


def test_the_rule_checks_the_pause_first_and_cannot_be_called_without_it():
    """First, as at the entry points: a paused project's move is refused as paused even when it couldn't
    happen anyway. Required: no caller moves a task without saying whether its project is paused."""
    with pytest.raises(ProjectPaused):
        check_task_transition(TaskStatus.REVIEWED, TaskStatus.READY, **ALLOWED, paused=True)
    with pytest.raises(ProjectPaused):
        check_task_transition(TaskStatus.CREATED, TaskStatus.READY, dependency_statuses=[TaskStatus.RUNNING],
                              retries=0, max_retries=2, paused=True)
    with pytest.raises(TypeError):
        check_task_transition(TaskStatus.CREATED, TaskStatus.READY, **ALLOWED)


def test_a_pause_is_not_skipped_like_a_task_that_cannot_move():
    """Batch loops skip a task that can't move (`except TransitionError: continue`); a paused project must
    stop the batch instead, so its refusal is not a TransitionError."""
    with pytest.raises(ProjectPaused):
        try:
            check_task_transition(TaskStatus.CREATED, TaskStatus.READY, **ALLOWED, paused=True)
        except TransitionError:
            pass


# ---------- the backstop, applied ----------


def test_promote_ready_on_a_paused_project_raises_instead_of_skipping(client, session, project):
    """The reported gap: _promote_ready, called on a paused project, returned the task as READY. Now the move
    is refused and recorded, and the refusal stops the batch instead of being skipped like a task that can't
    move (which would look like nothing to promote)."""
    pid = project["id"]
    t = _created_task(client, pid)
    task = session.get(Task, t["id"])  # loaded before the owner pauses in another session
    _pause(client, pid)

    with pytest.raises(ProjectPaused):
        manager._promote_ready(session, pid, [task], "plan approved")
    assert _status(client, pid, t["id"]) == "CREATED"
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"], last["payload"]) == (
        "pause.blocked", t["id"], {"action": "transition", "to": "READY", "reason": "plan approved"},
    )


@pytest.mark.parametrize("path, setup, body, to", [
    ("plan/approve", _created_task, None, "READY"),
    ("tasks/{tid}/decide", _ready_task, {"option": 0}, "RUNNING"),
    ("tasks/{tid}/transition", _created_task, {"status": "READY"}, "READY"),
    ("tasks/{tid}/transition", _ready_task, {"status": "RUNNING"}, "RUNNING"),
], ids=["approve", "decide", "transition_ready", "transition_running"])
def test_a_task_move_that_skips_ensure_active_is_still_refused(client, project, monkeypatch, path, setup, body, to):
    """A path that moves a task without a model call and forgets ensure_active (e.g. a bulk approve): the
    move's own check refuses it, the route answers 409 and nothing moves. (The transition route has no other
    check: its only work is the move.)"""
    pid = project["id"]
    t = setup(client, pid)
    monkeypatch.setattr("app.manager.ensure_active", lambda *a, **kw: None)
    tasks = client.get(f"/projects/{pid}/tasks").json()
    _pause(client, pid)

    r = client.post(f"/projects/{pid}/{path.format(tid=t['id'])}", json=body)
    _assert_refused(client, pid, r, "transition", t["id"])
    assert _events(client, pid)[-1]["payload"]["to"] == to
    assert client.get(f"/projects/{pid}/tasks").json() == tasks
    assert not {"decision.plan_approved", "decision.step"} & {e["type"] for e in _events(client, pid)}


@pytest.mark.parametrize("path, setup, body", [
    ("plan/approve", _created_task, None),
    ("tasks/{tid}/decide", _ready_task, {"option": 0}),
], ids=["approve", "decide"])
def test_a_pause_landing_after_the_entry_check_is_refused_by_the_moves_check(
    client, session_factory, project, monkeypatch, path, setup, body
):
    """The entry check passed, then the owner paused: the move's own check reads `paused` fresh, not from the
    Project the route loaded."""
    pid = project["id"]
    t = setup(client, pid)
    ensure_active = manager.ensure_active

    def check_then_the_owner_pauses(*args, **kwargs):
        ensure_active(*args, **kwargs)
        _owner_pauses(session_factory, pid)

    monkeypatch.setattr("app.manager.ensure_active", check_then_the_owner_pauses)
    tasks = client.get(f"/projects/{pid}/tasks").json()

    r = client.post(f"/projects/{pid}/{path.format(tid=t['id'])}", json=body)
    _assert_refused(client, pid, r, "transition", t["id"])
    assert client.get(f"/projects/{pid}/tasks").json() == tasks


@pytest.mark.parametrize("path, setup, body, module, action, detail", [
    ("plan/approve", _created_task, None, "app.manager", "plan.approve", {}),
    ("tasks/{tid}/decide", _ready_task, {"option": 0}, "app.manager", "decide", {}),
    ("tasks/{tid}/transition", _created_task, {"status": "READY"}, "app.routers.tasks", "transition", {"to": "READY"}),
    ("tasks/{tid}/transition", _ready_task, {"status": "RUNNING"}, "app.routers.tasks", "transition",
     {"to": "RUNNING"}),
], ids=["approve", "decide", "transition_ready", "transition_running"])
def test_a_pause_landing_after_the_moves_check_is_caught_as_it_commits(
    client, session_factory, project, monkeypatch, path, setup, body, module, action, detail
):
    """Every check passed, then the owner paused before the request committed: the commit's locked re-read
    refuses it, so the move never lands just after a pause."""
    pid = project["id"]
    t = setup(client, pid)
    check_task_move = manager.check_task_move

    def check_then_the_owner_pauses(*args, **kwargs):
        check_task_move(*args, **kwargs)
        _owner_pauses(session_factory, pid)

    monkeypatch.setattr(f"{module}.check_task_move", check_then_the_owner_pauses)
    tasks = client.get(f"/projects/{pid}/tasks").json()

    r = client.post(f"/projects/{pid}/{path.format(tid=t['id'])}", json=body)
    _assert_refused(client, pid, r, action, None if action == "plan.approve" else t["id"])
    assert _events(client, pid)[-1]["payload"] == {"action": action, **detail}
    assert client.get(f"/projects/{pid}/tasks").json() == tasks
    assert not {"decision.plan_approved", "decision.step"} & {e["type"] for e in _events(client, pid)}


def test_a_pause_landing_mid_batch_moves_none_of_it(client, session_factory, project, monkeypatch):
    """Two dependency-free tasks approved together; the owner pauses after the first is promoted (not yet
    committed): the refusal rolls that promotion back too, so a batch moves whole or not at all."""
    pid = project["id"]
    first, second = _created_task(client, pid), _created_task(client, pid)
    dependency_statuses = manager._dependency_statuses

    def the_owner_pauses_before_the_second(session, task):
        if task.id == second["id"]:
            _owner_pauses(session_factory, pid)
        return dependency_statuses(session, task)

    monkeypatch.setattr("app.manager._dependency_statuses", the_owner_pauses_before_the_second)
    r = client.post(f"/projects/{pid}/plan/approve")
    _assert_refused(client, pid, r, "transition", second["id"])
    assert [_status(client, pid, t["id"]) for t in (first, second)] == ["CREATED", "CREATED"]
    assert "task.status_changed" not in {e["type"] for e in _events(client, pid)}


def test_only_the_paused_project_is_refused(client, project):
    """Every check reads the moving task's own project: another project's moves go through."""
    other = client.post("/projects", json={"owner_id": project["owner_id"], "title": "Other", "goal": "g"}).json()
    t = _created_task(client, other["id"])
    _pause(client, project["id"])

    r = client.post(f"/projects/{other['id']}/plan/approve")
    assert r.status_code == 200 and r.json()["ready_task_ids"] == [t["id"]], r.text
    assert "pause.blocked" not in {e["type"] for p in (project, other) for e in _events(client, p["id"])}


def test_the_commit_holds_off_a_pause_until_the_moves_have_landed(file_client, monkeypatch):
    """On separate connections (file SQLite): once the commit's locked read has seen the project active, the
    owner's pause can't commit before the moves do — it waits for the write lock (here it gives up after
    0.1 s), so the moves land before the pause, never after it."""
    client, factory = file_client
    user = client.post("/users", json={"email": "l@example.com"}).json()
    pid = client.post("/projects", json={"owner_id": user["id"], "title": "P", "goal": "g"}).json()["id"]
    t = _created_task(client, pid)
    owner_engine = create_engine(factory.kw["bind"].url, connect_args={"timeout": 0.1})
    owner = sessionmaker(bind=owner_engine, autoflush=False, expire_on_commit=False)
    held_off = []
    paused = pause._paused

    def read_then_the_owner_pauses(session, project_id, *, lock=False):
        result = paused(session, project_id, lock=lock)
        if lock:
            with owner() as s:
                try:
                    set_paused(project_id, ProjectPauseUpdate(paused=True), session=s)
                except OperationalError as e:
                    held_off.append(str(e))
        return result

    monkeypatch.setattr("app.pause._paused", read_then_the_owner_pauses)
    r = client.post(f"/projects/{pid}/plan/approve")
    owner_engine.dispose()
    assert r.status_code == 200 and r.json()["ready_task_ids"] == [t["id"]], r.text
    assert held_off and "database is locked" in held_off[0]
    assert client.get(f"/projects/{pid}").json()["paused"] is False


def test_the_commits_lock_lets_two_moves_queue_on_postgresql(session, project, monkeypatch):
    """PostgreSQL: the commit's lock is FOR NO KEY UPDATE. It still conflicts with the pause's UPDATE, but not
    with the KEY SHARE that each move's flushed rows take on the project row through their foreign keys; FOR
    UPDATE would conflict with those, and two moves committing in one project could deadlock."""
    statements = []
    scalar = session.scalar

    def capture(statement, *args, **kwargs):
        statements.append(statement)
        return scalar(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", capture)

    assert pause._paused(session, project["id"], lock=True) is False
    assert str(statements[-1].compile(dialect=postgresql.dialect())).endswith("FOR NO KEY UPDATE")


def test_every_status_write_in_app_is_behind_check_task_move():
    """The backstop is a convention a direct write could skip, so keep it one: any function in app/ that sets
    `.status` (only Task has it), builds a row with `status=` or bulk-updates it must call check_task_move."""

    def writes_status(fn):
        for node in ast.walk(fn):
            targets = node.targets if isinstance(node, ast.Assign) else [getattr(node, "target", None)]
            if any(isinstance(t, ast.Attribute) and t.attr == "status" for t in targets):
                return True
            if isinstance(node, ast.Call) and any(k.arg == "status" for k in node.keywords):
                return True
        return False

    def calls_check_task_move(fn):
        return any(isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "check_task_move"
                   for n in ast.walk(fn))

    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    unchecked = [
        f"{path.relative_to(app_dir.parent).as_posix()}::{fn.name}"
        for path in app_dir.rglob("*.py")
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and writes_status(fn) and not calls_check_task_move(fn)
    ]
    assert unchecked == []
