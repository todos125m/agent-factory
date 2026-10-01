"""A paid call holds its worst-case cost against its budgets while it runs (app/gateway/service.py), so calls in
flight at the same time can't together spend past a budget each alone fits; nothing stays open while a model
runs. FakeProvider only.
"""

import threading
from datetime import timedelta

import pytest

from app.gateway.pricing import cost_usd
from app.gateway.providers import FakeProvider, ProviderError
from app.gateway.service import HOLD_TTL, BudgetExceeded, Gateway
from app.models import BudgetHold, ModelCall, RunEvent, utcnow
from tests.test_infrastructure import make_project

ROUTE = {"provider": "fake", "model": "claude-opus-5"}
ESTIMATE = cost_usd("claude-opus-5", 0, 2000)  # system "s" + user "u", the default max_output_tokens


def _call(session, provider, project_id):
    return Gateway(session, providers={"fake": provider}).call(
        "manager", system="s", user="u", project_id=project_id, route=ROUTE,
    )


class Free(FakeProvider):
    free = True


@pytest.mark.parametrize("make_provider, held", [(FakeProvider, [ESTIMATE]), (Free, [])], ids=["paid", "free"])
@pytest.mark.parametrize("db_engine", ["memory", "file"], indirect=True)
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


@pytest.mark.parametrize("db_engine", ["memory", "file"], indirect=True)
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


@pytest.mark.parametrize("db_engine", ["file"], indirect=True)
def test_simultaneous_calls_never_spend_past_the_budget(session_factory):
    """Six paid calls checked at the same moment, on six connections, against a budget for two: exactly two
    run. Each admitted call is held at the provider until all six were checked, so every check happens while
    the admitted ones are in flight."""
    with session_factory() as s:
        pid = make_project(s, budget=2.5 * ESTIMATE).id
    start, outcomes = threading.Barrier(6), []
    checked = threading.Condition()

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


class Failing(FakeProvider):
    def complete(self, request):
        self.requests.append(request)
        raise ProviderError("model timed out")


def test_a_failed_call_releases_its_hold(session):
    pid = make_project(session).id
    with pytest.raises(ProviderError):
        _call(session, Failing(), pid)
    assert session.query(BudgetHold).count() == 0
    assert [c.ok for c in session.query(ModelCall)] == [False]


def test_a_refused_call_holds_nothing(session):
    pid = make_project(session, budget=0.000001).id
    fake = FakeProvider()
    with pytest.raises(BudgetExceeded):
        _call(session, fake, pid)
    assert fake.requests == [] and session.query(BudgetHold).count() == 0


def test_a_hold_left_by_a_stopped_process_stops_counting(session):
    pid = make_project(session, budget=1.5 * ESTIMATE).id
    session.add(BudgetHold(project_id=pid, usd=ESTIMATE, created_at=utcnow() - HOLD_TTL - timedelta(minutes=1)))
    session.commit()
    _call(session, FakeProvider(), pid)  # the stale hold no longer counts

    session.add(BudgetHold(project_id=pid, usd=ESTIMATE))  # a live one does
    session.commit()
    with pytest.raises(BudgetExceeded):
        _call(session, FakeProvider(), pid)
