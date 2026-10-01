"""Model gateway: role → provider/model routing, budget checks, token and cost logging (§29–§31).

Nothing stays open while a model runs: Gateway.call commits before the provider call, so no transaction, and on
SQLite no database-wide write lock, spans it; the owner's pause and every other write go through meanwhile.
"""

import math
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import settings_layers
from app.events import record_event
from app.gateway.pricing import cost_usd
from app.gateway.providers import (
    ModelRequest,
    ModelResponse,
    Provider,
    ProviderError,
    WebSearchConfig,
    default_providers,
)
from app.models import BudgetHold, ModelCall, Project, Task, utcnow
from app.pause import refuse_if_paused

# Hard ceiling regardless of settings (owner's directive): a misconfigured or malicious settings
# layer must never grant an agent more than this many searches per call.
WEB_SEARCH_HARD_CEILING = 10

# A budget hold older than this belongs to a call that never finished (its process stopped mid-call) and no
# longer counts: longer than any provider's own timeout (the Anthropic and OpenAI SDKs give up after 10 minutes).
HOLD_TTL = timedelta(minutes=30)


class BudgetExceeded(RuntimeError):
    """Raised before a call that could push spend over budget; the caller pauses for approval (§31)."""


def spent_usd(session: Session, *, project_id: int | None = None, task_id: int | None = None) -> float:
    """Cost of the calls logged so far."""
    query = select(func.coalesce(func.sum(ModelCall.cost_usd), 0.0))
    if task_id is not None:
        query = query.where(ModelCall.task_id == task_id)
    elif project_id is not None:
        query = query.where(ModelCall.project_id == project_id)
    return float(session.scalar(query) or 0.0)


def held_usd(
    session: Session, *, project_id: int | None = None, task_id: int | None = None, excluding: int | None = None
) -> float:
    """Worst-case cost of the paid calls still in flight (their BudgetHold rows)."""
    query = select(func.coalesce(func.sum(BudgetHold.usd), 0.0)).where(BudgetHold.created_at > utcnow() - HOLD_TTL)
    if task_id is not None:
        query = query.where(BudgetHold.task_id == task_id)
    elif project_id is not None:
        query = query.where(BudgetHold.project_id == project_id)
    if excluding is not None:
        query = query.where(BudgetHold.id != excluding)
    return float(session.scalar(query) or 0.0)


class Gateway:
    def __init__(self, session: Session, providers: dict[str, Provider] | None = None) -> None:
        self.session = session
        self.providers = providers if providers is not None else default_providers()

    def call(
        self,
        role: str,
        *,
        system: str,
        user: str,
        project_id: int | None = None,
        task_id: int | None = None,
        agent: str | None = None,
        tools: list[str] | None = None,
        permissions: dict[str, Any] | None = None,
        json_schema: dict[str, Any] | None = None,
        route: dict[str, Any] | None = None,
    ) -> ModelResponse:
        if project_id is None and task_id is not None:
            # A task's call belongs to its project: pause check, settings, budget and ModelCall alike.
            task = self.session.get(Task, task_id)
            project_id = task.project_id if task is not None else None
        if project_id is not None:
            # Backstop for a path that skipped app/manager.py::ensure_active: no model call while paused.
            refuse_if_paused(self.session, project_id, "model_call", task_id, role=role, agent=agent)
        settings = settings_layers.resolve(self.session, project_id=project_id, task_id=task_id)
        route = route or settings["models"].get(role)
        if route is None:
            raise ProviderError(f"no model configured for role '{role}'")
        provider = self.providers.get(route["provider"])
        if provider is None:
            raise ProviderError(f"unknown provider '{route['provider']}'")
        budget = settings["budget"]
        max_out = int(budget["max_output_tokens"])

        web_search = None
        if tools and "web_search" in tools:
            if not (permissions or {}).get("network"):
                raise ProviderError(
                    f"agent '{agent}' declares tool 'web_search' but its permissions.network is not "
                    "true; grant network permission or remove the tool"
                )
            requested = int(budget.get("max_web_searches", 5))
            # requested <= 0 disables search for this call (an owner-facing budget knob, distinct
            # from the tool/permission check above) rather than being floored up to a minimum of 1.
            if requested > 0:
                web_search = WebSearchConfig(max_uses=min(requested, WEB_SEARCH_HARD_CEILING))

        request = ModelRequest(
            model=route["model"],
            system=system,
            user=user,
            max_output_tokens=max_out,
            json_schema=json_schema,
            effort=route.get("effort"),
            web_search=web_search,
        )
        hold_id = None
        if not getattr(provider, "free", False):
            # Worst case for this call: all input uncached, all output tokens used.
            estimate = cost_usd(route["model"], (len(system) + len(user)) // 3, max_out)
            hold_id = self._hold_budget(project_id, task_id, budget, estimate)
        # The hold, and anything the caller flushed, is committed now: nothing stays open while the model runs.
        self.session.commit()
        started = time.monotonic()
        call = ModelCall(
            project_id=project_id, task_id=task_id, agent=agent, role=role,
            provider=provider.name, model=route["model"],
        )
        try:
            response = provider.complete(request)
        except Exception as e:
            call.ok, call.error = False, str(e)[:500]
            call.duration_ms = int((time.monotonic() - started) * 1000)
            self._log(call, hold_id)
            raise
        u = response.usage
        call.input_tokens, call.output_tokens = u.input_tokens, u.output_tokens
        call.cache_read_tokens, call.cache_write_tokens = u.cache_read_tokens, u.cache_write_tokens
        call.web_searches = response.web_searches
        call.cost_usd = (
            response.cost_usd if response.cost_usd is not None
            else cost_usd(route["model"], u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens)
        )
        call.duration_ms = int((time.monotonic() - started) * 1000)
        self._log(call, hold_id)
        # Backfill what the caller can't otherwise get without re-querying ModelCall (racy under
        # concurrent calls, e.g. the benchmark tab comparing routes).
        response.cost_usd = call.cost_usd
        response.duration_ms = call.duration_ms
        return response

    def _log(self, call: ModelCall, hold_id: int | None) -> None:
        """Log the call; its ModelCall row replaces its budget hold in the same commit."""
        if hold_id is not None:
            self.session.execute(delete(BudgetHold).where(BudgetHold.id == hold_id))
        self.session.add(call)
        self.session.commit()

    def _hold_budget(
        self, project_id: int | None, task_id: int | None, budget: dict[str, Any], estimate: float
    ) -> int | None:
        """Check this call's worst case against its budgets, counting the paid calls still in flight, and hold it
        while the call runs so the next check counts it too (§31). Checks in one project queue: the hold is
        written before anything is read (SQLite: that takes the write lock), and the project row is locked FOR NO
        KEY UPDATE (PostgreSQL; like app/pause.py's lock, it doesn't conflict with foreign keys' KEY SHARE). On
        a refusal nothing is held: `budget.exceeded` is recorded and BudgetExceeded raised."""
        if project_id is None and task_id is None:
            return None  # no budget to check against: the benchmark caps its own spend
        hold = BudgetHold(project_id=project_id, task_id=task_id, usd=estimate)
        self.session.add(hold)
        self.session.flush()
        if project_id is not None:
            self.session.execute(select(Project.id).where(Project.id == project_id).with_for_update(key_share=True))
        checks = []
        if task_id is not None:
            checks.append(("task", {"task_id": task_id}, float(budget["task_usd"])))
        if project_id is not None:
            checks.append(("project", {"project_id": project_id}, float(budget["project_usd"])))
        for scope, key, limit in checks:
            spent = spent_usd(self.session, **key)
            held = held_usd(self.session, **key, excluding=hold.id)
            # Fail closed, like WEB_SEARCH_HARD_CEILING: NaN/inf make `... > limit` never true. PUT /settings
            # and ProjectCreate reject such limits; this guards rows stored before that.
            finite = math.isfinite(limit)
            if not finite or spent + held + estimate > limit:
                self.session.delete(hold)
                if project_id is not None:
                    record_event(self.session, project_id, "budget.exceeded", task_id, scope=scope,
                                 spent_usd=round(spent, 4), in_flight_usd=round(held, 4),
                                 limit_usd=limit if finite else str(limit))
                self.session.commit()
                in_flight = f", ${held:.4f} more in flight" if held else ""
                raise BudgetExceeded(
                    f"{scope} budget ${limit} would be exceeded (spent ${spent:.4f}{in_flight})" if finite
                    else f"{scope} budget is not a finite number ({limit}); fix it in the settings"
                )
        return hold.id
