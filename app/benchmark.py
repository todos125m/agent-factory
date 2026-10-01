"""Benchmark tab (owner decision d12): run the manager's plan step on several route configs
(provider/model/effort) against one fixed goal, so runs are comparable over time, and record
tokens/cost/duration/schema-validity plus an owner rating for each (docs/NIGHT_QUEUE.md item 2).

This is exploratory/manual (the owner triggers it, on a phone, one comparison at a time), so routes
run sequentially and a real network/SDK failure on one route must never abort the others — hence the
broad `except Exception` in `run_one`, scoped to a single route's call only.
"""

from pydantic import Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import context, storable
from app.gateway.service import Gateway
from app.manager import PlanIn, manager_agent
from app.models import BenchmarkRun

BENCHMARK_GOAL = "Validate a SaaS idea for solo consultants who bill hourly clients."

# gateway.call() only budget-checks against a project/task, and a benchmark run has neither
# (CLAUDE.md: "every call is budget-checked") — cap total benchmark spend here instead.
BENCHMARK_BUDGET_USD = 2.0


class RouteIn(storable.StorableIn):  # each field is a BenchmarkRun column: PostgreSQL refuses a longer value
    provider: str = Field(max_length=storable.limit(BenchmarkRun, "provider"))
    model: str = Field(max_length=storable.limit(BenchmarkRun, "model"))
    effort: str | None = Field(default=None, max_length=storable.limit(BenchmarkRun, "effort"))


def _benchmark_spent(session: Session) -> float:
    return float(session.scalar(select(func.coalesce(func.sum(BenchmarkRun.cost_usd), 0.0))) or 0.0)


def run_one(session: Session, gateway: Gateway, route: RouteIn) -> BenchmarkRun:
    run = BenchmarkRun(goal=BENCHMARK_GOAL, provider=route.provider, model=route.model, effort=route.effort)
    spent = _benchmark_spent(session)
    if spent >= BENCHMARK_BUDGET_USD:
        run.error = f"benchmark budget ${BENCHMARK_BUDGET_USD} reached (spent ${spent:.4f}); rate/clear old runs to continue"
        session.add(run)
        session.commit()
        return run

    system = context.system_prompt(session, manager_agent(session), ["plan-goal", "delegate", "evidence"])
    try:
        response = gateway.call(
            "manager", system=system, user=BENCHMARK_GOAL, agent="benchmark",
            json_schema=PlanIn.model_json_schema(),
            route={"provider": route.provider, "model": route.model, "effort": route.effort},
        )
    except Exception as e:  # noqa: BLE001 - one route's failure (any provider/SDK error) must not abort the rest
        run.error = storable.clean(str(e))[:500]  # a provider's error can quote a model's text
        session.add(run)
        session.commit()
        return run

    run.input_tokens, run.output_tokens = response.usage.input_tokens, response.usage.output_tokens
    run.cost_usd = response.cost_usd or 0.0
    run.duration_ms = response.duration_ms or 0
    try:
        PlanIn.model_validate(response.data)
        run.schema_valid = True
    except ValidationError as e:
        run.error = str(e)[:500]
    session.add(run)
    session.commit()
    return run


def run_benchmark(session: Session, gateway: Gateway, routes: list[RouteIn]) -> list[BenchmarkRun]:
    return [run_one(session, gateway, route) for route in routes]


def rate(session: Session, run: BenchmarkRun, rating: int) -> BenchmarkRun:
    run.rating = rating
    session.commit()
    return run
