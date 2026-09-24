"""Model gateway: role → provider/model routing, budget checks, token and cost logging (§29–§31)."""

import time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import settings_layers
from app.events import record_event
from app.gateway.pricing import cost_usd
from app.gateway.providers import ModelRequest, ModelResponse, Provider, ProviderError, default_providers
from app.models import ModelCall


class BudgetExceeded(RuntimeError):
    """Raised before a call that could push spend over budget; the caller pauses for approval (§31)."""


def spent_usd(session: Session, *, project_id: int | None = None, task_id: int | None = None) -> float:
    query = select(func.coalesce(func.sum(ModelCall.cost_usd), 0.0))
    if task_id is not None:
        query = query.where(ModelCall.task_id == task_id)
    elif project_id is not None:
        query = query.where(ModelCall.project_id == project_id)
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
        json_schema: dict[str, Any] | None = None,
    ) -> ModelResponse:
        settings = settings_layers.resolve(self.session, project_id=project_id, task_id=task_id)
        route = settings["models"].get(role)
        if route is None:
            raise ProviderError(f"no model configured for role '{role}'")
        provider = self.providers.get(route["provider"])
        if provider is None:
            raise ProviderError(f"unknown provider '{route['provider']}'")
        budget = settings["budget"]
        max_out = int(budget["max_output_tokens"])

        # Worst case for this call: all input uncached, all output tokens used.
        estimate = cost_usd(route["model"], (len(system) + len(user)) // 3, max_out)
        self._check_budget(project_id, task_id, budget, estimate)

        request = ModelRequest(
            model=route["model"],
            system=system,
            user=user,
            max_output_tokens=max_out,
            json_schema=json_schema,
            effort=route.get("effort"),
        )
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
            self.session.add(call)
            self.session.commit()
            raise
        u = response.usage
        call.input_tokens, call.output_tokens = u.input_tokens, u.output_tokens
        call.cache_read_tokens, call.cache_write_tokens = u.cache_read_tokens, u.cache_write_tokens
        call.cost_usd = cost_usd(route["model"], u.input_tokens, u.output_tokens, u.cache_read_tokens, u.cache_write_tokens)
        call.duration_ms = int((time.monotonic() - started) * 1000)
        self.session.add(call)
        self.session.commit()
        return response

    def _check_budget(self, project_id: int | None, task_id: int | None, budget: dict[str, Any], estimate: float) -> None:
        checks = []
        if task_id is not None:
            checks.append(("task", spent_usd(self.session, task_id=task_id), float(budget["task_usd"])))
        if project_id is not None:
            checks.append(("project", spent_usd(self.session, project_id=project_id), float(budget["project_usd"])))
        for scope, spent, limit in checks:
            if spent + estimate > limit:
                if project_id is not None:
                    record_event(self.session, project_id, "budget.exceeded", task_id,
                                 scope=scope, spent_usd=round(spent, 4), limit_usd=limit)
                    self.session.commit()
                raise BudgetExceeded(f"{scope} budget ${limit} would be exceeded (spent ${spent:.4f})")
