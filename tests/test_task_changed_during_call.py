"""A task's status read before a model call is stale after it. The call takes seconds to a minute, and in that time the
owner (or the owner's other device) can cancel, fail, decide, complete or re-run the task. Every move made after a call
is checked on the task read again, under the project lock (app/pause.py::lock_task), not on the Task the request loaded
first:

* an automatic checkpoint's auto-decision is withheld when the task is no longer as the call found it: the paid-for
  checkpoint stays, `task.changed_meanwhile` says why, and the owner decides (like `pause.withheld` for a pause);
* a run takes its result only for a task still as the call found it (RUNNING): otherwise 409, nothing of the result is
  applied, and only the paid call stays logged (its ModelCall row).

No extra model call is made for any of it. FakeProvider only; ActsDuringCall is the owner acting while the call runs.
"""

import threading

import pytest

from app import manager, pause
from app.gateway.providers import FakeProvider
from app.gateway.service import Gateway
from app.models import Project, Task, TaskStatus
from app.routers.projects import set_paused
from app.routers.tasks import transition_task
from app.schemas import ProjectPauseUpdate, TaskTransition
from app.state_machine import TransitionError
from tests.conftest import owner_of
from tests.test_manager import CHECKPOINT, _ready_task
from tests.test_project_pause import (
    ActsDuringCall, _events, _model_calls, _route_to_fake, _running_task, _status, use_real_gateway,
)
from tests.test_task_run import RUN_RESULT

S = TaskStatus
OWNERS_OUTPUT = {"summary": "marked done by hand"}
OTHER_DEVICE_RESULT = {**RUN_RESULT, "summary": "The other device's result."}

# Both databases: in memory every session shares one connection; a file gives each its own, as in production.
BOTH_DBS = pytest.mark.parametrize("db_engine", ["memory", "file"], indirect=True)


def _moves(session_factory, pid, tid, *steps):
    """The owner's transitions from another tab (POST .../transition, each in its own session). A step is a status, or
    a (status, output) pair for a task completed by hand."""
    for step in steps:
        status, output = step if isinstance(step, tuple) else (step, None)
        with session_factory() as s:
            transition_task(pid, tid, TaskTransition(status=status, output=output), session=s, me=owner_of(s, pid))


def _decides_elsewhere(session_factory, pid, tid):
    with session_factory() as s:  # the decision from another tab or device
        manager.decide(s, s.get(Project, pid), s.get(Task, tid), 0, "from the other tab")


def _runs_elsewhere(session_factory, pid, tid, reply=OTHER_DEVICE_RESULT):
    with session_factory() as s:  # the same task run from another device: its own call, then its completion
        gateway = Gateway(s, providers={"fake": FakeProvider(replies=[reply])})
        return manager.run_task(s, gateway, s.get(Project, pid), s.get(Task, tid))


def _dependent(client, pid, tid):
    return client.post(f"/projects/{pid}/tasks", json={"title": "Dependent", "depends_on": [tid]}).json()


def _types(client, pid):
    return [e["type"] for e in _events(client, pid)]


# ---------- the checkpoint's auto-decision ----------


@BOTH_DBS
@pytest.mark.parametrize("meanwhile, now, decisions", [
    (lambda sf, pid, tid: _moves(sf, pid, tid, S.CANCELLED), "CANCELLED", 0),
    (_decides_elsewhere, "RUNNING", 1),
    (lambda sf, pid, tid: _moves(sf, pid, tid, S.RUNNING, S.WAITING), "WAITING", 0),
], ids=["cancelled", "decided_elsewhere", "started_and_put_on_hold"])
def test_a_task_that_moved_during_the_checkpoint_call_is_not_auto_decided(
    client, session_factory, project, monkeypatch, meanwhile, now, decisions
):
    """The reported bug: _decide checked READY -> RUNNING on the status the request loaded before the call, so a
    task the owner cancelled meanwhile (CANCELLED has no outgoing moves) was started anyway, and a task another tab
    had decided was decided twice. Now the paid-for checkpoint stays and the owner decides, as after a pause."""
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")  # automatic mode (the API default): a low-risk step auto-decides
    provider = ActsDuringCall([CHECKPOINT], lambda: meanwhile(session_factory, pid, t["id"]))
    use_real_gateway(client, monkeypatch, provider)

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200, r.text
    assert r.json()["auto_decided"] is False
    assert r.json()["checkpoint"]["challenge"] == CHECKPOINT["challenge"]  # the paid-for checkpoint is kept
    assert _status(client, pid, t["id"]) == now
    events = _events(client, pid)
    assert [e["type"] for e in events][-2:] == ["checkpoint.created", "task.changed_meanwhile"]
    assert _types(client, pid).count("decision.step") == decisions  # only the other tab's, never a stale auto one
    assert events[-1]["task_id"] == t["id"]
    assert events[-1]["payload"] == {"action": "auto_decide", "was": "READY", "now": now}
    assert len(provider.requests) == 1 and _model_calls(client, pid) == 1  # no extra call


def test_a_pause_still_wins_over_a_move_during_the_checkpoint_call(client, session_factory, project, monkeypatch):
    """Both landed while the call ran: the pause's withheld event is the one on the record (d16 is unchanged)."""
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")

    def the_owner_pauses_and_cancels():
        with session_factory() as s:
            set_paused(pid, ProjectPauseUpdate(paused=True), session=s, me=owner_of(s, pid))
        _moves(session_factory, pid, t["id"], S.CANCELLED)

    use_real_gateway(client, monkeypatch, ActsDuringCall([CHECKPOINT], the_owner_pauses_and_cancels))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is False, r.text
    assert _types(client, pid)[-2:] == ["checkpoint.created", "pause.withheld"]
    assert _status(client, pid, t["id"]) == "CANCELLED"


@BOTH_DBS
def test_a_task_that_did_not_move_is_still_auto_decided_after_the_call(client, session_factory, project, monkeypatch):
    """The other side of the guard: nothing moved while the call ran, so the auto-decision is taken as before."""
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_real_gateway(client, monkeypatch, ActsDuringCall([CHECKPOINT], lambda: None))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is True, r.text
    assert _status(client, pid, t["id"]) == "RUNNING"
    assert "task.changed_meanwhile" not in _types(client, pid)


@BOTH_DBS
def test_the_same_checkpoint_asked_from_two_devices_decides_once(client, session_factory, project, monkeypatch):
    """Both requests would auto-decide (both calls are paid): the first to finish starts the task, the other finds it
    RUNNING and withholds its own decision instead of deciding the task a second time."""
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")

    def the_other_device_asks_too():
        with session_factory() as s:
            gateway = Gateway(s, providers={"fake": FakeProvider(replies=[CHECKPOINT])})
            done = manager.create_checkpoint(s, gateway, s.get(Project, pid), s.get(Task, t["id"]))
            assert done["auto_decided"] is True

    use_real_gateway(client, monkeypatch, ActsDuringCall([CHECKPOINT], the_other_device_asks_too))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is False, r.text
    assert _status(client, pid, t["id"]) == "RUNNING"
    types = _types(client, pid)
    assert (types.count("checkpoint.created"), types.count("decision.step")) == (2, 1)
    assert _events(client, pid)[-1]["payload"] == {"action": "auto_decide", "was": "READY", "now": "RUNNING"}
    assert _model_calls(client, pid) == 2


@BOTH_DBS
def test_a_waiting_task_is_still_resumed_by_an_auto_decision(client, session_factory, project, monkeypatch):
    """The guard compares with what the call found, not with READY: a task that was WAITING when its checkpoint was
    asked for, and still is when the call returns, is resumed as it was before the guard."""
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    _moves(session_factory, pid, t["id"], S.RUNNING, S.WAITING)
    use_real_gateway(client, monkeypatch, ActsDuringCall([CHECKPOINT], lambda: None))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is True, r.text
    assert _status(client, pid, t["id"]) == "RUNNING"


def _deleted(session_factory, tid):
    with session_factory() as s:  # no route deletes a started task today; a future one, or a script, could
        s.delete(s.get(Task, tid))
        s.commit()


def test_a_run_whose_task_was_deleted_during_the_call_is_refused(client, session_factory, project, monkeypatch):
    """A task that is gone has moved on: refused like any other move, the record kept on the project (the task's own
    row can't be referenced). SQLite doesn't enforce the foreign keys the staged rows point through, so this pins the
    guard, not what PostgreSQL's flush does first."""
    pid = project["id"]
    t = _running_task(client, pid)
    use_real_gateway(client, monkeypatch, ActsDuringCall([RUN_RESULT], lambda: _deleted(session_factory, t["id"])))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 409 and "it is now deleted" in r.json()["detail"], r.text
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"]) == ("task.changed_meanwhile", None)
    assert last["payload"] == {"action": "run", "was": "RUNNING", "now": "deleted"}
    assert client.get(f"/projects/{pid}/memory").json() == [] and _model_calls(client, pid) == 1


def test_a_checkpoint_whose_task_was_deleted_during_the_call_is_not_auto_decided(
    client, session_factory, project, monkeypatch
):
    pid = project["id"]
    t = _ready_task(client, pid, risk="low")
    use_real_gateway(client, monkeypatch, ActsDuringCall([CHECKPOINT], lambda: _deleted(session_factory, t["id"])))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/checkpoint")
    assert r.status_code == 200 and r.json()["auto_decided"] is False, r.text
    last = _events(client, pid)[-1]
    assert (last["type"], last["task_id"]) == ("task.changed_meanwhile", None)
    assert last["payload"] == {"action": "auto_decide", "was": "READY", "now": "deleted"}
    assert "decision.step" not in _types(client, pid)


# ---------- the run ----------


@BOTH_DBS
@pytest.mark.parametrize("steps, now, output", [
    ((S.FAILED,), "FAILED", None),
    ((S.WAITING,), "WAITING", None),
    (((S.COMPLETED, OWNERS_OUTPUT),), "COMPLETED", OWNERS_OUTPUT),
    ((S.FAILED, S.READY, S.RUNNING), "RUNNING", None),
], ids=["failed", "put_on_hold", "completed_by_hand", "failed_and_retried"])
def test_a_task_that_moved_during_its_run_call_takes_no_result(
    client, session_factory, project, monkeypatch, steps, now, output
):
    """The reported bug: run_task set COMPLETED on the status it had loaded before the call, over the owner's FAILED
    (or hold, or hand-made result), and logged the move as 'from RUNNING'. A retried task is RUNNING again at the
    end, but not the attempt this call belongs to. Now the run is refused (409) and only the paid call stays."""
    pid = project["id"]
    t = _running_task(client, pid)
    dependent = _dependent(client, pid, t["id"])
    provider = ActsDuringCall([RUN_RESULT], lambda: _moves(session_factory, pid, t["id"], *steps))
    use_real_gateway(client, monkeypatch, provider)

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 409 and "changed while its model call ran" in r.json()["detail"], r.text
    task = client.get(f"/projects/{pid}/tasks/{t['id']}").json()
    assert (task["status"], task["output"]) == (now, output)  # the owner's move stands, the result is not stored
    assert _model_calls(client, pid) == 1 and len(provider.requests) == 1  # paid and logged, not repeated
    events = _events(client, pid)
    assert events[-1]["task_id"] == t["id"]
    assert events[-1]["payload"] == {"action": "run", "was": "RUNNING", "now": now}
    assert not {"task.completed", "memory.captured", "learning.captured"} & set(_types(client, pid))
    assert client.get(f"/projects/{pid}/memory").json() == []
    assert _status(client, pid, dependent["id"]) == "CREATED"  # nothing was promoted


@BOTH_DBS
def test_the_same_task_run_from_two_devices_completes_once(client, session_factory, project, monkeypatch):
    """The reported bug: both runs passed the RUNNING check before their calls, both paid, both completed the task
    (2 ModelCalls, 2 memory items, 2 task.completed). Both calls are still paid; the first to finish stands and the
    other is refused."""
    pid = project["id"]
    t = _running_task(client, pid)
    dependent = _dependent(client, pid, t["id"])
    provider = ActsDuringCall([RUN_RESULT], lambda: _runs_elsewhere(session_factory, pid, t["id"]))
    use_real_gateway(client, monkeypatch, provider)

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")  # this device's call is in flight while the other finishes
    assert r.status_code == 409, r.text
    task = client.get(f"/projects/{pid}/tasks/{t['id']}").json()
    assert task["status"] == "COMPLETED" and task["output"]["summary"] == OTHER_DEVICE_RESULT["summary"]
    assert _model_calls(client, pid) == 2
    types = _types(client, pid)
    assert (types.count("task.completed"), types.count("memory.captured")) == (1, 1)
    assert [m["content"] for m in client.get(f"/projects/{pid}/memory").json()] == [OTHER_DEVICE_RESULT["summary"]]
    done = [e for e in _events(client, pid) if e["type"] == "task.status_changed" and e["payload"]["to"] == "COMPLETED"]
    assert len(done) == 1
    assert _status(client, pid, dependent["id"]) == "READY"  # promoted once, by the run that stood
    assert _events(client, pid)[-1]["payload"] == {"action": "run", "was": "RUNNING", "now": "COMPLETED"}


@BOTH_DBS
def test_a_run_of_a_task_that_did_not_move_completes_as_before(client, session_factory, project, monkeypatch):
    pid = project["id"]
    t = _running_task(client, pid)
    dependent = _dependent(client, pid, t["id"])
    use_real_gateway(client, monkeypatch, ActsDuringCall([RUN_RESULT], lambda: None))

    r = client.post(f"/projects/{pid}/tasks/{t['id']}/run")
    assert r.status_code == 200, r.text
    assert r.json()["task"]["status"] == "COMPLETED" and r.json()["ready_task_ids"] == [dependent["id"]]
    assert "task.changed_meanwhile" not in _types(client, pid)
    assert len(client.get(f"/projects/{pid}/memory").json()) == 1


class MeetsTheOtherCall(FakeProvider):
    """Replies like FakeProvider once the other call is in flight too, so both calls are open at the same moment."""

    def __init__(self, barrier, replies):
        super().__init__(replies=list(replies))
        self.barrier = barrier

    def complete(self, request):
        self.barrier.wait(timeout=20)
        return super().complete(request)


@pytest.mark.parametrize("db_engine", ["file"], indirect=True)
def test_two_runs_finishing_together_complete_the_task_once(client, session_factory, project):
    """Real concurrency on separate connections: both calls are in flight at once, then both finish and race for the
    project lock. Whichever gets it first completes the task; the other re-reads it, finds it COMPLETED, and is
    refused: one result, two paid calls."""
    pid = project["id"]
    t = _running_task(client, pid)
    _route_to_fake(client)  # the devices' gateways below know only the fake provider
    barrier = threading.Barrier(2)
    outcomes = []

    def a_device(reply):
        with session_factory() as s:
            gateway = Gateway(s, providers={"fake": MeetsTheOtherCall(barrier, [reply])})
            try:
                outcomes.append(manager.run_task(s, gateway, s.get(Project, pid), s.get(Task, t["id"])))
            except Exception as e:  # anything but the refusal fails the assertions below, with the outcome named
                outcomes.append(e)

    results = [{**RUN_RESULT, "summary": f"device {n}"} for n in (1, 2)]
    threads = [threading.Thread(target=a_device, args=(reply,)) for reply in results]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)

    completed = [o for o in outcomes if isinstance(o, dict)]
    refused = [o for o in outcomes if isinstance(o, TransitionError)]
    assert (len(completed), len(refused)) == (1, 1), outcomes
    task = client.get(f"/projects/{pid}/tasks/{t['id']}").json()
    assert task["status"] == "COMPLETED" and task["output"]["summary"] == completed[0]["task"].output["summary"]
    assert _model_calls(client, pid) == 2
    types = _types(client, pid)
    assert (types.count("task.completed"), types.count("memory.captured")) == (1, 1)


# ---------- the re-read itself ----------


def test_lock_task_reads_the_row_again_and_none_for_a_task_that_is_gone(client, session_factory, session, project):
    """The session never expires what it loaded (expire_on_commit is off), so this is the only way past a call to
    see what happened to the task; the instance the request holds is the one refreshed."""
    pid = project["id"]
    t = _ready_task(client, pid)
    task = session.get(Task, t["id"])  # loaded by the request, before its model call
    _moves(session_factory, pid, t["id"], S.CANCELLED)
    assert task.status is S.READY  # this session's copy is stale

    assert pause.lock_task(session, t["id"]) is task
    assert task.status is S.CANCELLED
    assert pause.lock_task(session, 0) is None
