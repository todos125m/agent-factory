"""A paid call holds its estimated worst case against its budgets while it runs (app/gateway/service.py), so calls
in flight at the same time can't together pass a budget each alone fits; nothing stays open while a model runs.
FakeProvider only.
"""

import sys
import threading
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, update
from sqlalchemy.dialects import postgresql

from app import pause
from app.gateway import service
from app.gateway.openai_provider import OpenAIProvider
from app.gateway.pricing import cost_usd
from app.gateway.providers import MAX_CALL_S, MAX_RETRIES, REQUEST_TIMEOUT_S, AnthropicProvider, FakeProvider, ProviderError
from app.gateway.service import HOLD_TTL, BudgetExceeded, Gateway
from app.models import BudgetHold, ModelCall, Project, RunEvent, SettingsLayer, SettingsScope, Task, utcnow
from app.settings_layers import DEFAULTS
from app.state_machine import ProjectPaused
from tests.test_infrastructure import make_project
from tests.test_project_pause import ROUTE, FailingProvider

# What one paid call holds: system "s" + user "u" is no input tokens, and the default settings cap the output.
ESTIMATE = cost_usd(ROUTE["model"], 0, DEFAULTS["budget"]["max_output_tokens"])
FILE = pytest.mark.parametrize("db_engine", ["file"], indirect=True)  # a second session needs its own connection


def _call(session, provider, project_id, task_id=None):
    return Gateway(session, providers={"fake": provider}).call(
        "manager", system="s", user="u", project_id=project_id, task_id=task_id, route=ROUTE,
    )


class Free(FakeProvider):
    free = True


@FILE
@pytest.mark.parametrize("make_provider, held", [(FakeProvider, [ESTIMATE]), (Free, [])], ids=["paid", "free"])
def test_a_paid_call_holds_its_worst_case_while_it_runs(session_factory, make_provider, held):
    with session_factory() as s:
        pid = make_project(s).id
    seen = {}

    class Watching(make_provider):
        def complete(self, request):
            seen["caller_open"] = caller.in_transaction()
            with session_factory() as other:  # what another request sees mid-call
                seen["held"] = [h.usd for h in other.query(BudgetHold).filter_by(project_id=pid)]
            return super().complete(request)

    with session_factory() as caller:
        _call(caller, Watching(), pid)
    assert seen == {"caller_open": False, "held": pytest.approx(held)}
    with session_factory() as s:
        assert s.query(BudgetHold).count() == 0 and s.query(ModelCall).count() == 1


@FILE
def test_a_call_in_flight_counts_against_the_next_ones_budget(session_factory):
    """A budget for one call: while it runs, a second paid call in the same project (another request, on its
    own connection) is refused. Before holds both checks saw nothing spent, and both ran."""
    with session_factory() as s:
        pid = make_project(s, budget=1.5 * ESTIMATE).id
    second = {}

    class Overlapping(FakeProvider):
        def complete(self, request):
            with session_factory() as other:
                try:
                    _call(other, FakeProvider(), pid)
                    second["outcome"] = "called"
                except BudgetExceeded as e:
                    second["outcome"] = str(e)
            return super().complete(request)

    with session_factory() as s:
        _call(s, Overlapping(), pid)
    assert second["outcome"] == (
        f"project budget ${1.5 * ESTIMATE} would be exceeded (spent $0.0000, ${ESTIMATE:.4f} more in flight)"
    )
    with session_factory() as s:
        assert s.query(ModelCall).count() == 1 and s.query(BudgetHold).count() == 0
        refusal = s.query(RunEvent).filter_by(type="budget.exceeded").one().payload
        assert (refusal["spent_usd"], refusal["in_flight_usd"]) == (0.0, round(ESTIMATE, 4))


@FILE
def test_a_task_budget_counts_the_calls_in_flight_on_that_task(session_factory):
    """Every paid run and checkpoint is task-scoped: the task's own budget holds back a second call on the same
    task while the first runs, however much the project has left."""
    with session_factory() as s:
        pid = make_project(s).id
        task = Task(project_id=pid, title="T")
        s.add(task)
        s.flush()
        s.add(SettingsLayer(scope=SettingsScope.TASK, scope_id=task.id, values={"budget": {"task_usd": 1.5 * ESTIMATE}}))
        s.commit()
        tid = task.id
    second = {}

    class Overlapping(FakeProvider):
        def complete(self, request):
            with session_factory() as other:
                try:
                    _call(other, FakeProvider(), pid, tid)
                    second["outcome"] = "called"
                except BudgetExceeded as e:
                    second["outcome"] = str(e)
            return super().complete(request)

    with session_factory() as s:
        _call(s, Overlapping(), pid, tid)
    assert second["outcome"].startswith("task budget") and "more in flight" in second["outcome"]
    with session_factory() as s:
        assert s.query(RunEvent).filter_by(type="budget.exceeded").one().payload["scope"] == "task"


@FILE
def test_simultaneous_calls_never_spend_past_the_budget(session_factory, monkeypatch):
    """Six paid calls checked at the same moment, on six connections, against a budget for two: exactly two run.
    A barrier at the first read of every check forces any check that isn't queued behind the others to overlap
    with them (all six would read nothing spent and all be admitted); a check that holds the queue is alone at
    the barrier, which then times out. Each admitted call is held at the provider until all six were checked."""
    with session_factory() as s:
        pid = make_project(s, budget=2.5 * ESTIMATE).id
    start, at_the_check, outcomes = threading.Barrier(6), threading.Barrier(6, timeout=1.0), []
    checked = threading.Condition()
    held_usd = service.held_usd

    def held_at_the_barrier(*args, **kwargs):
        try:
            at_the_check.wait()
        except threading.BrokenBarrierError:  # alone at it: the checks are queued, as they should be
            pass
        return held_usd(*args, **kwargs)

    monkeypatch.setattr(service, "held_usd", held_at_the_barrier)

    def note(outcome):
        with checked:
            outcomes.append(outcome)
            checked.notify_all()

    class HeldUntilAllChecked(FakeProvider):
        def complete(self, request):
            note("called")
            with checked:
                checked.wait_for(lambda: len(outcomes) == 6, timeout=10)
            return super().complete(request)

    provider = HeldUntilAllChecked()

    def one_request():
        with session_factory() as s:
            start.wait()
            try:
                _call(s, provider, pid)
            except BudgetExceeded:
                note("refused")

    threads = [threading.Thread(target=one_request) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes) == ["called"] * 2 + ["refused"] * 4
    with session_factory() as s:
        assert s.query(ModelCall).count() == 2 and s.query(BudgetHold).count() == 0


def test_a_call_finishing_between_a_checks_two_reads_is_still_counted(session_factory, monkeypatch):
    """PostgreSQL (READ COMMITTED) reads each statement in its own snapshot, and a finishing call (its hold becomes
    a ModelCall in one commit) isn't queued behind the check's lock: whichever sum a check reads second may
    already have lost the call from one and not yet have it in the other. The check reads the holds first, so a
    call finishing in between is counted twice rather than not at all. Simulated on one connection: SQLite's own
    lock can't interleave it."""
    with session_factory() as s:
        pid = make_project(s, budget=1.5 * ESTIMATE).id
        other = BudgetHold(project_id=pid, usd=ESTIMATE)
        s.add(other)
        s.commit()
        other_id = other.id

    def the_other_call_finishes():  # what its log commits: the hold gives way to the ModelCall
        with session_factory() as s:
            s.execute(delete(BudgetHold).where(BudgetHold.id == other_id))
            s.add(ModelCall(project_id=pid, role="manager", provider="fake", model=ROUTE["model"], cost_usd=ESTIMATE))
            s.commit()

    first = []

    def after_the_first_read(real):
        def read(*args, **kwargs):
            result = real(*args, **kwargs)
            if not first:
                first.append(real.__name__)
                the_other_call_finishes()
            return result
        return read

    monkeypatch.setattr(service, "spent_usd", after_the_first_read(service.spent_usd))
    monkeypatch.setattr(service, "held_usd", after_the_first_read(service.held_usd))
    with session_factory() as s, pytest.raises(BudgetExceeded):
        _call(s, FakeProvider(), pid)
    assert first == ["held_usd"]  # the check reads the holds first


def test_a_pause_that_queued_ahead_of_a_reservation_still_refuses_the_call(session, monkeypatch):
    """The gateway's first pause check is an unlocked read. A reservation then waits on the project lock, which a
    pause in progress holds, and carries on once the pause commits: the check under the lock is what sees it."""
    pid = make_project(session).id
    session.execute(update(Project).where(Project.id == pid).values(paused=True))
    session.commit()
    paused = pause._paused
    # The unlocked read still says "running" (it ran before the pause committed); the locked one sees the truth.
    monkeypatch.setattr(pause, "_paused", lambda s, project_id, *, lock=False: paused(s, project_id, lock=lock) and lock)
    fake = FakeProvider()

    with pytest.raises(ProjectPaused):
        _call(session, fake, pid)
    assert fake.requests == [] and session.query(BudgetHold).count() == 0
    last = session.query(RunEvent).order_by(RunEvent.id.desc()).first()
    assert (last.type, last.payload["action"]) == ("pause.blocked", "model_call")


def test_the_reservation_locks_the_project_row_for_no_key_update_on_postgresql(session):
    """On PostgreSQL the project row queues one project's budget checks, and the lock must be FOR NO KEY UPDATE: it
    conflicts with the pause's UPDATE and with the other lockers, but not with the KEY SHARE every foreign key to
    the project takes (the hold's own INSERT among them), where FOR UPDATE could deadlock two calls. SQLite
    renders none of it, so the statement is pinned compiled for PostgreSQL."""
    pid = make_project(session).id
    statements = []
    event.listen(session, "do_orm_execute", lambda state: statements.append(state.statement))

    _call(session, FakeProvider(), pid)
    compiled = [str(st.compile(dialect=postgresql.dialect())) for st in statements]
    locks = [sql for sql in compiled if " FOR " in sql]
    assert len(locks) == 1 and "FROM projects" in locks[0] and locks[0].endswith("FOR NO KEY UPDATE")


def test_a_failed_call_releases_its_hold(session):
    pid = make_project(session).id
    with pytest.raises(ProviderError):
        _call(session, FailingProvider(), pid)
    assert session.query(BudgetHold).count() == 0
    assert [c.ok for c in session.query(ModelCall)] == [False]


def test_a_provider_error_quoting_unstorable_text_is_still_logged(session):
    """A provider's error can quote a model's reply; stored as it came, a lone surrogate or a NUL would fail the
    log itself and mask the provider's error."""

    class Quoting(FakeProvider):
        def complete(self, request):
            raise ProviderError("not JSON: ok \ud83d \x00 end")

    pid = make_project(session).id
    with pytest.raises(ProviderError, match="not JSON"):
        _call(session, Quoting(), pid)
    assert [c.error for c in session.query(ModelCall)] == ["not JSON: ok ?  end"]
    assert session.query(BudgetHold).count() == 0


@FILE
def test_a_call_whose_log_fails_still_releases_its_hold(session_factory):
    """If the commit that logs a finished call fails, its hold must not keep counting for the whole HOLD_TTL:
    it is released on its own, and the failure still propagates."""
    with session_factory() as s:
        pid = make_project(s).id
    failing = {"next_commit": False}

    class FinishesThenTheLogFails(FakeProvider):
        def complete(self, request):
            failing["next_commit"] = True  # the next commit is the log's
            return super().complete(request)

    with session_factory() as s:
        commit = s.commit

        def commit_that_fails_once():
            if failing["next_commit"]:
                failing["next_commit"] = False
                raise RuntimeError("database is locked")
            return commit()

        s.commit = commit_that_fails_once
        with pytest.raises(RuntimeError, match="database is locked"):
            _call(s, FinishesThenTheLogFails(), pid)
    with session_factory() as s:  # another connection: what was really committed
        assert s.query(BudgetHold).count() == 0 and s.query(ModelCall).count() == 0


def test_deleting_a_task_does_not_take_its_hold_with_it():
    """A task deleted mid-call (a rejected plan's) must not take the hold, and the project's in-flight spend with
    it. SQLite doesn't enforce foreign keys, so the rule is pinned in the schema: like ModelCall.task_id."""
    (fk,) = BudgetHold.__table__.c.task_id.foreign_keys
    assert fk.ondelete == "SET NULL"


def test_a_hold_left_by_a_stopped_process_stops_counting(session):
    pid = make_project(session, budget=1.5 * ESTIMATE).id
    session.add(BudgetHold(project_id=pid, usd=ESTIMATE, created_at=utcnow() - HOLD_TTL - timedelta(minutes=1)))
    session.commit()
    _call(session, FakeProvider(), pid)  # the stale hold no longer counts

    session.add(BudgetHold(project_id=pid, usd=ESTIMATE))  # a live one does
    session.commit()
    with pytest.raises(BudgetExceeded):
        _call(session, FakeProvider(), pid)


def test_the_paid_sdk_clients_are_built_with_the_limits_the_hold_ttl_assumes(monkeypatch):
    """HOLD_TTL must outlast the longest a paid call can run, which the SDKs' own timeout and retries decide; both
    clients are built with those limits pinned (stand-ins here: the SDKs are optional installs)."""
    made = {}
    for module, client in (("anthropic", "Anthropic"), ("openai", "OpenAI")):
        monkeypatch.setitem(
            sys.modules, module, SimpleNamespace(**{client: lambda module=module, **kwargs: made.setdefault(module, kwargs)}),
        )
    AnthropicProvider()._get_client()
    OpenAIProvider()._get_client()

    for module in ("anthropic", "openai"):
        timeout = made[module]["timeout"]
        assert (timeout.connect, timeout.read, made[module]["max_retries"]) == (5.0, REQUEST_TIMEOUT_S, MAX_RETRIES)
    assert HOLD_TTL > timedelta(seconds=MAX_CALL_S)
