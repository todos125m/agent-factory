"""Night queue item 2 — benchmark tab: run the manager's plan step on several route configs
against the fixed goal, store tokens/cost/duration/schema-validity, and let the owner rate a run.
FakeProvider only — no real model calls.
"""

from app.benchmark import BENCHMARK_GOAL
from app.gateway.providers import FakeProvider, ProviderError
from app.gateway.service import Gateway
from app.main import app
from app.routers.manager import get_gateway

GOOD_PLAN = {
    "understanding": "Validate a SaaS idea for solo consultants",
    "assumptions": [],
    "steps": [{"title": "Research", "agent": "researcher", "goal": "Find pains", "depends_on": [], "risk": "low"}],
}

BAD_PLAN = {"understanding": "u"}  # missing required "steps"


def use_fake(client, session_factory, providers):
    def override():
        with session_factory() as s:
            yield Gateway(s, providers=providers)

    app.dependency_overrides[get_gateway] = override


def test_benchmark_runs_multiple_routes_and_stores_metrics(client, session_factory):
    fake = FakeProvider(replies=[GOOD_PLAN, GOOD_PLAN])
    use_fake(client, session_factory, {"fake": fake})

    r = client.post("/benchmarks/run", json={"routes": [
        {"provider": "fake", "model": "claude-opus-5", "effort": "low"},
        {"provider": "fake", "model": "claude-sonnet-5"},
    ]})
    assert r.status_code == 200, r.text
    runs = r.json()
    assert len(runs) == 2
    assert all(run["goal"] == BENCHMARK_GOAL for run in runs)
    assert runs[0]["model"] == "claude-opus-5" and runs[1]["model"] == "claude-sonnet-5"
    assert all(run["schema_valid"] is True and run["error"] is None for run in runs)
    assert all(run["input_tokens"] > 0 for run in runs)  # FakeProvider still counts tokens

    listed = client.get("/benchmarks").json()
    assert len(listed) == 2
    app.dependency_overrides.pop(get_gateway, None)


def test_benchmark_records_malformed_output_without_failing_the_request(client, session_factory):
    fake = FakeProvider(replies=[BAD_PLAN])
    use_fake(client, session_factory, {"fake": fake})
    r = client.post("/benchmarks/run", json={"routes": [{"provider": "fake", "model": "claude-opus-5"}]})
    assert r.status_code == 200
    run = r.json()[0]
    assert run["schema_valid"] is False
    assert run["error"] is not None
    app.dependency_overrides.pop(get_gateway, None)


def test_benchmark_records_provider_error_and_keeps_other_routes_independent(client, session_factory):
    class BrokenProvider(FakeProvider):
        def complete(self, request):
            raise ProviderError("down for maintenance")

    use_fake(client, session_factory, {"broken": BrokenProvider(), "fake": FakeProvider(replies=[GOOD_PLAN])})
    r = client.post("/benchmarks/run", json={"routes": [
        {"provider": "broken", "model": "x"},
        {"provider": "fake", "model": "claude-opus-5"},
    ]})
    assert r.status_code == 200
    runs = r.json()
    assert runs[0]["error"] == "down for maintenance" and runs[0]["schema_valid"] is False
    assert runs[0]["input_tokens"] == 0  # a failed call's usage is never copied into the benchmark row
    assert runs[1]["schema_valid"] is True
    app.dependency_overrides.pop(get_gateway, None)


def test_benchmark_rate_validates_range_and_run_existence(client, session_factory):
    fake = FakeProvider(replies=[GOOD_PLAN])
    use_fake(client, session_factory, {"fake": fake})
    run = client.post("/benchmarks/run", json={"routes": [{"provider": "fake", "model": "claude-opus-5"}]}).json()[0]

    r = client.post(f"/benchmarks/{run['id']}/rate", json={"rating": 4})
    assert r.status_code == 200 and r.json()["rating"] == 4

    assert client.post(f"/benchmarks/{run['id']}/rate", json={"rating": 0}).status_code == 422
    assert client.post(f"/benchmarks/{run['id']}/rate", json={"rating": 6}).status_code == 422
    assert client.post("/benchmarks/999999/rate", json={"rating": 3}).status_code == 404
    app.dependency_overrides.pop(get_gateway, None)


def test_benchmark_rejects_empty_or_too_many_routes(client):
    assert client.post("/benchmarks/run", json={"routes": []}).status_code == 422
    too_many = [{"provider": "fake", "model": "m"} for _ in range(6)]
    assert client.post("/benchmarks/run", json={"routes": too_many}).status_code == 422


def test_benchmark_isolates_a_raw_sdk_style_exception_from_other_routes(client, session_factory):
    """gateway.call() re-raises whatever exception type the provider raised, not just ProviderError —
    run_one must still isolate it so one route's crash doesn't take down the others."""
    class CrashingProvider(FakeProvider):
        def complete(self, request):
            raise RuntimeError("connection reset")

    use_fake(client, session_factory, {"crash": CrashingProvider(), "fake": FakeProvider(replies=[GOOD_PLAN])})
    r = client.post("/benchmarks/run", json={"routes": [
        {"provider": "crash", "model": "x"},
        {"provider": "fake", "model": "claude-opus-5"},
    ]})
    assert r.status_code == 200
    runs = r.json()
    assert runs[0]["error"] == "connection reset" and runs[0]["schema_valid"] is False
    assert runs[1]["schema_valid"] is True
    app.dependency_overrides.pop(get_gateway, None)


def test_benchmark_stops_calling_the_model_once_its_own_budget_is_spent(client, session_factory):
    from app.models import BenchmarkRun

    with session_factory() as s:
        s.add(BenchmarkRun(goal=BENCHMARK_GOAL, provider="anthropic", model="claude-opus-5", cost_usd=5.0))
        s.commit()

    fake = FakeProvider(replies=[GOOD_PLAN])
    use_fake(client, session_factory, {"fake": fake})
    r = client.post("/benchmarks/run", json={"routes": [{"provider": "fake", "model": "claude-opus-5"}]})
    assert r.status_code == 200
    run = r.json()[0]
    assert "budget" in run["error"]
    assert run["schema_valid"] is False
    assert fake.requests == []  # the model was never actually called
    app.dependency_overrides.pop(get_gateway, None)
