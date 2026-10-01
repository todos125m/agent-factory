"""Model gateway: role → provider/model routing, budget checks, token and cost logging (§29–§31).

Nothing stays open while a model runs: Gateway.call commits before the provider call, so no transaction, and on
SQLite no database-wide write lock, spans it; the owner's pause and every other write go through meanwhile.
"""

import math
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import settings_layers, storable
from app.events import record_event
from app.gateway.pricing import cost_usd
from app.gateway.providers import (
    MAX_CALL_S,
    ModelRequest,
    ModelResponse,
    Provider,
    ProviderError,
    WebSearchConfig,
    default_providers,
)
from app.models import BudgetHold, ModelCall, Task, utcnow
from app.pause import refuse_if_paused

# Hard ceiling regardless of settings (owner's directive): a misconfigured or malicious settings
# layer must never grant an agent more than this many searches per call.
WEB_SEARCH_HARD_CEILING = 10

# A budget hold older than this belongs to a call that never finished (its process stopped mid-call) and no
# longer counts: it outlasts the longest a paid call can run (the SDK clients' pinned limits, MAX_CALL_S) by 10 min.
HOLD_TTL = timedelta(seconds=MAX_CALL_S) + timedelta(minutes=10)

# Every call is logged with its model id (ModelCall.model) once it has been paid for. PUT /settings refuses a longer
# one, but a layer stored before it did can still hold one: such a route is refused before anything is held or paid.
MAX_MODEL_ID_CHARS = storable.limit(ModelCall, "model")


class BudgetExceeded(RuntimeError):
    """Raised before a call that could push spend over budget; the caller pauses for approval (§31)."""


def _sum_in_scope(session: Session, column, task_column, project_column, *where, project_id, task_id) -> float:
    """Sum of `column` for one task, else for one project (the narrower scope wins), else for all."""
    query = select(func.coalesce(func.sum(column), 0.0)).where(*where)
    if task_id is not None:
        query = query.where(task_column == task_id)
    elif project_id is not None:
        query = query.where(project_column == project_id)
    return float(session.scalar(query) or 0.0)


def spent_usd(session: Session, *, project_id: int | None = None, task_id: int | None = None) -> float:
    """Cost of the calls logged so far."""
    return _sum_in_scope(
        session, ModelCall.cost_usd, ModelCall.task_id, ModelCall.project_id, project_id=project_id, task_id=task_id,
    )


def held_usd(session: Session, *, project_id: int | None = None, task_id: int | None = None) -> float:
    """Estimated worst-case cost of the paid calls in flight (their live BudgetHold rows)."""
    return _sum_in_scope(
        session, BudgetHold.usd, BudgetHold.task_id, BudgetHold.project_id,
        BudgetHold.created_at > utcnow() - HOLD_TTL, project_id=project_id, task_id=task_id,
    )


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
        if len(str(route["model"])) > MAX_MODEL_ID_CHARS:
            raise ProviderError(f"the model id of role '{role}' is longer than {MAX_MODEL_ID_CHARS} characters; fix it in the settings")
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
            # Estimated worst case for this call: all input uncached, all output tokens used. Web-search fees and
            # the tokens of search results are in neither this nor cost_usd, so a search call can cost more.
            estimate = cost_usd(route["model"], (len(system) + len(user)) // 3, max_out)
            hold_id = self._hold_budget(project_id, task_id, budget, estimate, role=role, agent=agent)
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
            call.ok, call.error = False, storable.clean(str(e))[:500]  # a provider may quote a model's text
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
        """Log the call; its ModelCall row replaces its budget hold in one commit.

        The ModelCall goes in first: its foreign keys take their shared locks on the task before the hold row is
        touched, the order a delete of that task (a rejected plan's) takes them in (the task, then the hold its
        SET NULL updates), so the two can't deadlock on PostgreSQL. A task deleted while the call ran is no reason
        to lose the record of what it cost: it is logged on the project instead. If the commit fails, the hold is
        released on its own, so a call that finished stops counting at once, not after HOLD_TTL."""
        try:
            try:
                self._insert(call)
            except IntegrityError:  # its task was deleted meanwhile (the project's own deletion fails again, below)
                self.session.rollback()
                call.task_id = None
                self._insert(call)
            self._drop_hold(hold_id)
            self.session.commit()
        except Exception:
            self.session.rollback()
            try:
                self._drop_hold(hold_id)
                self.session.commit()
            except Exception:  # the failure being raised is the one that matters; HOLD_TTL covers this one
                self.session.rollback()
            raise

    def _insert(self, call: ModelCall) -> None:
        self.session.add(call)
        self.session.flush()

    def _drop_hold(self, hold_id: int | None) -> None:
        if hold_id is not None:
            self.session.execute(delete(BudgetHold).where(BudgetHold.id == hold_id))

    def _hold_budget(
        self, project_id: int | None, task_id: int | None, budget: dict[str, Any], estimate: float, *,
        role: str, agent: str | None,
    ) -> int | None:
        """Check this call's estimated worst case against its budgets, counting the paid calls still in flight,
        and hold it while the call runs so the next check counts it too (§31). Checks in one project queue: the
        project row is locked first and then the hold is written (app/pause.py::refuse_if_paused with lock: on
        PostgreSQL FOR NO KEY UPDATE, on SQLite the hold's write takes the write lock), and `paused` is read under
        both, so a pause that queued ahead of this check has landed by now and refuses the call. On a refusal
        nothing is held (a pause refusal rolls the hold back, a budget refusal deletes it) and it is recorded."""
        if project_id is None and task_id is None:
            return None  # no budget to check against: the benchmark caps its own spend
        hold = BudgetHold(project_id=project_id, task_id=task_id, usd=estimate)
        self.session.add(hold)
        if project_id is not None:
            refuse_if_paused(self.session, project_id, "model_call", task_id, lock=True, role=role, agent=agent)
        else:
            self.session.flush()
        checks = []
        if task_id is not None:
            checks.append(("task", {"task_id": task_id}, float(budget["task_usd"])))
        if project_id is not None:
            checks.append(("project", {"project_id": project_id}, float(budget["project_usd"])))
        for scope, key, limit in checks:
            # The holds (this call's own among them) are read before the logged spend: PostgreSQL (READ
            # COMMITTED) gives each statement its own snapshot, and a call finishing between the two reads (its
            # hold becomes a ModelCall in one commit, not queued behind this check's lock) is then counted twice,
            # never not at all.
            held = held_usd(self.session, **key)
            spent = spent_usd(self.session, **key)
            in_flight = max(held - estimate, 0.0)  # the other calls'
            # Fail closed, like WEB_SEARCH_HARD_CEILING: NaN/inf make `... > limit` never true. PUT /settings
            # and ProjectCreate reject such limits; this guards rows stored before that.
            finite = math.isfinite(limit)
            if not finite or spent + held > limit:
                self.session.delete(hold)
                if project_id is not None:
                    record_event(self.session, project_id, "budget.exceeded", task_id, scope=scope,
                                 spent_usd=round(spent, 4), in_flight_usd=round(in_flight, 4),
                                 limit_usd=limit if finite else str(limit))
                self.session.commit()
                more = f", ${in_flight:.4f} more in flight" if in_flight else ""
                raise BudgetExceeded(
                    f"{scope} budget ${limit} would be exceeded (spent ${spent:.4f}{more})" if finite
                    else f"{scope} budget is not a finite number ({limit}); fix it in the settings"
                )
        return hold.id
