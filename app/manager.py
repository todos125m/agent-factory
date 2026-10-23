"""Manager (Phase 2, docs/ARCHITECTURE.md §7-§9): plan a goal into a task graph, gate it on
approval, run per-task decision checkpoints. Deterministic logic (DAG validation, agent
resolution, state transitions) stays in code; the model only proposes plans and checkpoints.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import context, memory, preferences, settings_layers, sources, storable
from app.errors import AppError
from app.events import record_event
from app.gateway.service import Gateway
from app.models import Agent, Project, RiskLevel, RunEvent, Task, TaskDependency, TaskStatus
from app.pause import check_task_move, commit_unless_paused, lock_project, lock_task, refuse_if_paused
from app.state_machine import TransitionError


class ManagerError(ValueError):
    """A plan or checkpoint response failed a deterministic, code-side check."""


def ensure_active(session: Session, project: Project, action: str, task_id: int | None = None, **detail: Any) -> None:
    """The pause policy (app/pause.py) at every entry point that would spend a model call or move a task to
    READY/RUNNING, called before changing anything (the transition route, whose only work is the move, relies
    on the move's own check); routers answer the refusal with 409."""
    refuse_if_paused(session, project.id, action, task_id, **detail)


def _paused_now(session: Session, project: Project) -> bool:
    """Fresh read of `paused` for work already past `ensure_active`: the owner may have paused while its
    model call ran, and loaded objects are never expired here. Locking the project row (app/pause.py::lock_project),
    then flushing and reading, keeps a pause from landing between this check and the caller's commit; the lock
    mode and order are what let two completions in one project queue instead of deadlocking."""
    return lock_project(session, project.id)


# The columns a plan step's text lands in (a step becomes a Task): the step's own limits, from the columns themselves.
_STEP_LIMITS = {"title": storable.limit(Task, "title"), "agent": storable.limit(Task, "owner")}


def _stamp(task: Task) -> tuple[TaskStatus, int]:
    """What a model call starts from: the task's status and retry count. The task read again once the call is over
    (app/pause.py::lock_task) is still the one the call belongs to only if this is unchanged; the retry count is there
    because a task failed, retried and started again is RUNNING once more, but not for this call."""
    return task.status, task.retries


def _moved_on(fresh: Task | None, started_from: tuple[TaskStatus, int]) -> bool:
    """Whether the task read again after a model call (None: gone) is no longer the one the call started from."""
    return fresh is None or _stamp(fresh) != started_from


def _record_moved_meanwhile(
    session: Session, project_id: int, task_id: int, action: str, was: TaskStatus, now: TaskStatus | None
) -> str:
    """Record that the move `action` was not made because the task is no longer as the model call found it: `was` is
    what the call found, `now` the status of the task read again (None: it is gone, so the event is the project's).
    Returns `now` as the event records it."""
    text = "deleted" if now is None else now.value
    record_event(
        session, project_id, "task.changed_meanwhile", None if now is None else task_id,
        action=action, was=was.value, now=text,
    )
    return text


class PlanStepIn(BaseModel):
    title: str
    agent: str
    goal: str
    depends_on: list[int] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW

    @field_validator("title", "agent")
    @classmethod
    def _fits_its_column(cls, v: str, info: ValidationInfo) -> str:
        """PostgreSQL refuses a longer value than its column holds (SQLite doesn't), after the spend. A validator, not
        Field(max_length), so the JSON schema sent to the provider stays as it was."""
        limit = _STEP_LIMITS[info.field_name]
        if len(v) > limit:
            raise ValueError(f"{info.field_name} must be at most {limit} characters")
        return v


class PlanIn(storable.StorableOut):
    understanding: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStepIn]


class CheckpointOption(BaseModel):
    title: str
    tradeoff: str


class CheckpointOut(storable.StorableOut):
    challenge: str
    options: list[CheckpointOption] = Field(min_length=2, max_length=4)
    recommended: int
    why: str


class TaskFinding(BaseModel):
    claim: str
    type: Literal["FACT", "INFERENCE", "HYPOTHESIS"]
    basis: str


class TaskRunOut(storable.StorableOut):
    summary: str
    findings: list[TaskFinding] = Field(default_factory=list)
    lesson: str
    next: str


def _fact_downgrade(basis: str) -> tuple[str, list[str]] | None:
    """Why a FACT with this basis can't stay a FACT (reason, cited hosts), or None if it can."""
    urls = sources.cited_urls(basis)
    if not urls:
        return "no_url", []
    hosts = [sources.url_host(u) for u in urls]
    if all(sources.host_tier(h) == sources.LOWEST_TIER for h in hosts):
        return "low_tier_sources", list(dict.fromkeys(h or u for h, u in zip(hosts, urls)))
    return None


def _apply_evidence_guard(session: Session, project_id: int, task: Task, result: TaskRunOut) -> TaskRunOut:
    """Deterministic guard (owner's directive): a FACT is only as good as its source. A finding typed
    FACT is downgraded to INFERENCE, with a note in its basis, when it cites no http(s) URL or when
    every URL it cites is lowest tier — blog, forum, social or unknown site (app/sources.py, rules in
    registry/source_tiers.yaml). The model's own evidence labels are advisory, this check is not."""
    details: list[dict[str, Any]] = []
    findings = []
    for finding in result.findings:
        if finding.type == "FACT" and (downgrade := _fact_downgrade(finding.basis)):
            reason, hosts = downgrade
            note = ("no source URL cited" if reason == "no_url"
                    else f"only low-tier sources (blog/forum/unknown): {', '.join(hosts[:5])}")
            finding = finding.model_copy(update={
                "type": "INFERENCE",
                "basis": f"{finding.basis} [downgraded from FACT: {note}]",
            })
            details.append({"claim": finding.claim, "reason": reason, "hosts": hosts})
        findings.append(finding)
    if details:
        record_event(session, project_id, "evidence.downgraded", task.id,
                     claims=[d["claim"] for d in details], details=details)
    return result.model_copy(update={"findings": findings})


def manager_agent(session: Session) -> Agent:
    agent = session.scalar(select(Agent).where(Agent.name == "manager"))
    if agent is None:
        raise ManagerError("manager agent is not registered")
    return agent


def _dependency_statuses(session: Session, task: Task) -> list[TaskStatus]:
    if not task.depends_on:
        return []
    return list(session.scalars(select(Task.status).where(Task.id.in_(task.depends_on))))


def _validate_dag(steps: list[PlanStepIn], max_steps: int) -> None:
    n = len(steps)
    if n == 0:
        raise ManagerError("plan has no steps")
    if n > max_steps:
        raise ManagerError(f"plan has {n} steps; max_steps is {max_steps}")
    for i, step in enumerate(steps):
        for d in step.depends_on:
            if not (0 <= d < n) or d == i:
                raise ManagerError(f"step {i} has an invalid dependency index {d}")

    state = [0] * n  # 0 = unvisited, 1 = in progress, 2 = done

    def visit(i: int) -> None:
        state[i] = 1
        for d in steps[i].depends_on:
            if state[d] == 1:
                raise ManagerError("plan has a dependency cycle")
            if state[d] == 0:
                visit(d)
        state[i] = 2

    for i in range(n):
        if state[i] == 0:
            visit(i)


def _plan_user_content(session: Session, project: Project, feedback: str | None) -> str:
    settings = settings_layers.resolve(session, project_id=project.id)
    lines = [
        f"Goal: {project.goal}",
        f"Mode: {settings['mode']}",
        f"Max steps: {settings['max_steps']}",
        f"Budget: ${settings['budget']['project_usd']}",
        "Available agents:",
        context.agent_directory(session),
    ]
    if feedback:
        lines.append(f"Feedback on the previous plan — revise accordingly: {feedback}")
    if language := preferences.reply_language_line(session, project, settings["output_locale"]):
        lines.append(language)
    return "\n".join(lines)


def _propose_plan(session: Session, gateway: Gateway, project: Project, feedback: str | None) -> PlanIn:
    """The model call and every check on its plan; stages nothing."""
    settings = settings_layers.resolve(session, project_id=project.id)
    system = context.system_prompt(session, manager_agent(session), ["plan-goal", "delegate", "evidence"])
    user = _plan_user_content(session, project, feedback)
    response = gateway.call(
        "manager", system=system, user=user, project_id=project.id, agent="manager",
        json_schema=PlanIn.model_json_schema(),
    )
    try:
        plan = PlanIn.model_validate(response.data)
    except ValidationError as e:
        raise ManagerError(f"invalid plan response: {e}") from e
    _validate_dag(plan.steps, int(settings["max_steps"]))
    return plan


def stage_plan(session: Session, project: Project, plan: PlanIn, max_steps: int) -> dict[str, Any]:
    """A plan made by something other than the manager's model (a workflow, app/workflow.py): checked as a DAG, staged
    exactly like the manager's own, awaiting the owner's approval. The caller commits."""
    _validate_dag(plan.steps, max_steps)
    return _add_plan(session, project, plan)


def create_plan(session: Session, gateway: Gateway, project: Project) -> dict[str, Any]:
    ensure_active(session, project, "plan")
    result = _add_plan(session, project, _propose_plan(session, gateway, project, None))
    session.commit()
    return result


def _add_plan(session: Session, project: Project, plan: PlanIn) -> dict[str, Any]:
    """Stage a checked plan: its tasks (CREATED) and dependencies, and its events. The caller commits."""
    task_ids: list[int] = []
    specialists_requested: list[str] = []
    for step in plan.steps:
        agent = None if step.agent == "manager" else session.scalar(
            select(Agent).where(Agent.name == step.agent, Agent.active)
        )
        task = Task(project_id=project.id, title=step.title, owner=step.agent, input={"goal": step.goal}, risk=step.risk)
        if agent is None:
            # Kept in the graph with every dependency, BLOCKED: what depends on it can't start without it (issue #2
            # G7: the step and its edges used to be dropped, so a dependent ran without its prerequisite).
            task.status, task.reason = TaskStatus.BLOCKED, "missing_specialist"
            specialists_requested.append(step.agent)
        session.add(task)
        session.flush()
        task_ids.append(task.id)
        if agent is None:
            record_event(
                session, project.id, "specialist.requested", task.id,
                title=step.title, agent=step.agent, goal=step.goal, risk=step.risk.value,
            )

    for step, tid in zip(plan.steps, task_ids):
        deps = sorted({task_ids[d] for d in step.depends_on})
        session.get(Task, tid).dependencies = [TaskDependency(depends_on_id=d) for d in deps]
    session.flush()

    created_ids = list(task_ids)
    record_event(session, project.id, "plan.proposed", None,
                 understanding=plan.understanding, assumptions=plan.assumptions, task_ids=created_ids)
    record_event(session, project.id, "approval.requested", None, task_ids=created_ids)
    return {
        "understanding": plan.understanding,
        "assumptions": plan.assumptions,
        "task_ids": created_ids,
        "specialists_requested": specialists_requested,
    }


def _promote_ready(session: Session, project_id: int, tasks: list[Task], reason: str) -> list[int]:
    """CREATED `tasks` whose dependencies are all done become READY (§33). On a paused project none does:
    check_task_move's refusal rolls back the whole batch, and ProjectPaused is not a TransitionError to skip."""
    ready_ids: list[int] = []
    for task in tasks:
        deps = _dependency_statuses(session, task)
        try:
            check_task_move(session, task, TaskStatus.READY, dependency_statuses=deps, reason=reason)
        except TransitionError:
            continue
        task.status = TaskStatus.READY
        record_event(session, project_id, "task.status_changed", task.id,
                      **{"from": "CREATED", "to": "READY", "reason": reason})
        ready_ids.append(task.id)
    return ready_ids


def approve_plan(session: Session, project: Project) -> dict[str, Any]:
    ensure_active(session, project, "plan.approve")
    tasks = session.scalars(select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.CREATED)).all()
    ready_ids = _promote_ready(session, project.id, tasks, "plan approved")
    record_event(session, project.id, "decision.plan_approved", None, ready_task_ids=ready_ids)
    commit_unless_paused(session, project.id, "plan.approve")
    return {"ready_task_ids": ready_ids}


# What decides a plan's fate: one of these after the plan a reject was made against means it is no longer pending.
_PLAN_EVENTS = ("plan.proposed", "decision.plan_approved", "decision.plan_rejected")


def reject_plan(session: Session, gateway: Gateway, project: Project, feedback: str) -> dict[str, Any]:
    """Reject the pending plan and re-plan with the owner's feedback, as one unit.

    The plan rejected is the one pending when the request arrived. Nothing is staged until the re-plan's model
    call returned and passed every check, so a refused or failed re-plan (pause, budget, provider error, invalid
    plan) leaves the old plan as it was, and the gateway's own commits carry none of the rejection. If the plan
    was decided while the call ran (approved, re-planned or rejected from another tab or device) the rejection
    no longer applies: 409, with the paid re-plan only logged. Otherwise the rejection and the new plan land in
    one commit, checked under the project lock so no plan decision can land in between."""
    ensure_active(session, project, "plan.reject")
    last_plan = session.scalar(
        select(RunEvent)
        .where(RunEvent.project_id == project.id, RunEvent.type == "plan.proposed")
        .order_by(RunEvent.id.desc())
    )
    proposed_ids = last_plan.payload.get("task_ids", []) if last_plan else []
    seen_event_id = session.scalar(  # a plan decision after this one, not an earlier one, is "meanwhile"
        select(func.max(RunEvent.id)).where(RunEvent.project_id == project.id, RunEvent.type.in_(_PLAN_EVENTS))
    ) or 0
    plan = _propose_plan(session, gateway, project, feedback)

    tasks = (  # what is still undecided of the plan the owner rejected
        session.scalars(select(Task).where(Task.id.in_(proposed_ids),
                                           Task.status.in_([TaskStatus.CREATED, TaskStatus.BLOCKED]))).all()
        if proposed_ids else []
    )
    deleted_ids = [t.id for t in tasks]
    for t in tasks:
        session.delete(t)
    rejected = record_event(
        session, project.id, "decision.plan_rejected", None, feedback=feedback, deleted_task_ids=deleted_ids
    )
    lock_project(session, project.id)  # flushes the rejection; from here nothing else decides this plan
    decided_meanwhile = session.scalar(
        select(RunEvent.id).where(
            RunEvent.project_id == project.id, RunEvent.type.in_(_PLAN_EVENTS),
            RunEvent.id > seen_event_id, RunEvent.id != rejected.id,
        ).limit(1)
    )
    if decided_meanwhile is not None:
        session.rollback()
        raise TransitionError("The plan was decided while it was being re-planned; look at the current plan")
    result = _add_plan(session, project, plan)
    session.commit()
    return result


def create_checkpoint(session: Session, gateway: Gateway, project: Project, task: Task) -> dict[str, Any]:
    """Ask the manager for the task's decision checkpoint and, in automatic mode, take it when the risk's rule allows.

    The model call takes seconds to a minute, and the Task loaded before it is stale after it: the auto-decision is
    taken only if the task, read again under the project lock, is still as the call found it. Otherwise (paused, or
    cancelled, decided or put on hold meanwhile) the paid-for checkpoint is kept and the decision is withheld."""
    ensure_active(session, project, "checkpoint", task.id)
    started_from = _stamp(task)
    system = context.system_prompt(session, manager_agent(session), ["decision-checkpoint", "evidence"])
    user = context.task_context(session, task)
    response = gateway.call(
        "manager", system=system, user=user, project_id=project.id, task_id=task.id, agent="manager",
        json_schema=CheckpointOut.model_json_schema(),
    )
    try:
        checkpoint = CheckpointOut.model_validate(response.data)
    except ValidationError as e:
        raise ManagerError(f"invalid checkpoint response: {e}") from e
    if not (0 <= checkpoint.recommended < len(checkpoint.options)):
        raise ManagerError("recommended option is out of range")
    record_event(session, project.id, "checkpoint.created", task.id, **checkpoint.model_dump())

    settings = settings_layers.resolve(session, task_id=task.id)
    approvals = settings.get("approvals")
    rule = approvals.get(task.risk.value) if isinstance(approvals, dict) else None
    # Allowlist, not `!= "user"`: an unknown or malformed rule (e.g. a stored typo) waits for the owner. A step_by_step
    # interaction policy (resolved, so clamped by every layer above and the mode) waits for the owner at every step.
    auto = (settings["mode"] == "automatic" and settings["interaction_policy"] != "step_by_step"
            and rule in ("auto", "manager"))
    if auto:
        # The project row is locked before the checkpoint above is flushed, the task row after it (app/pause.py).
        if _paused_now(session, project):
            # Paused while the model call ran: keep the paid-for checkpoint, leave the decision to the owner.
            record_event(session, project.id, "pause.withheld", task.id, action="auto_decide")
            auto = False
        else:
            fresh = lock_task(session, task.id)
            if _moved_on(fresh, started_from):
                # Moved on while the call ran (cancelled, decided from another tab, put on hold): the decision was
                # meant for the task the call found, so it is withheld too, and the owner decides.
                now = None if fresh is None else fresh.status
                _record_moved_meanwhile(session, project.id, task.id, "auto_decide", started_from[0], now)
                auto = False
    if auto:
        _decide(session, task, checkpoint.recommended, None, auto=True)
    session.commit()
    return {"checkpoint": checkpoint.model_dump(), "auto_decided": auto}


def _decide(session: Session, task: Task, option: int, note: str | None, *, auto: bool) -> None:
    check_task_move(session, task, TaskStatus.RUNNING, dependency_statuses=[])
    record_event(session, task.project_id, "decision.step", task.id, option=option, note=note, auto=auto)
    task.status = TaskStatus.RUNNING


def decide(session: Session, project: Project, task: Task, option: int, note: str | None) -> None:
    ensure_active(session, project, "decide", task.id)
    _decide(session, task, option, note, auto=False)
    commit_unless_paused(session, project.id, "decide", task.id)


def _created_dependents(session: Session, project: Project, task: Task) -> list[Task]:
    dependent_ids = select(TaskDependency.task_id).where(TaskDependency.depends_on_id == task.id)
    return list(session.scalars(
        select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.CREATED, Task.id.in_(dependent_ids))
    ))


def _promote_ready_dependents(
    session: Session, project: Project, task: Task, reason: str = "dependency completed"
) -> list[int]:
    """CREATED tasks depending on `task` whose dependencies are now all done become READY (§33)."""
    return _promote_ready(session, project.id, _created_dependents(session, project, task), reason)


def release_withheld(session: Session, project: Project) -> list[int]:
    """On resume: promote the dependents that runs finishing during the pause withheld (see run_task).
    A withheld auto-decision is not taken here — its checkpoint waits for the owner like any other."""
    # The resume first: a run finishing concurrently then either sees it or is seen below, and the
    # promotions' pause check (check_task_move, a fresh read) sees the project active.
    session.flush()
    last_pause = session.scalar(
        select(func.max(RunEvent.id)).where(RunEvent.project_id == project.id, RunEvent.type == "project.paused")
    )
    withheld = session.scalars(
        select(RunEvent)
        .where(RunEvent.project_id == project.id, RunEvent.type == "pause.withheld", RunEvent.id > (last_pause or 0))
        .order_by(RunEvent.id)
    ).all()
    ready_ids: list[int] = []
    for event in withheld:
        if event.payload.get("action") != "promote_dependents" or event.task_id is None:
            continue
        task = session.get(Task, event.task_id)
        if task is not None:
            ready_ids += _promote_ready_dependents(session, project, task, reason="project resumed")
    return ready_ids


def run_task(session: Session, gateway: Gateway, project: Project, task: Task) -> dict[str, Any]:
    """Execute a RUNNING task with its owner agent: one Gateway.call, deterministic result handling.

    The call takes seconds to a minute, and the Task loaded before it is stale after it: the owner may have failed or
    held the task meanwhile, or the same task may have been run from another device and completed. The result is taken
    only if the task, read again under the project lock, is still as the call found it (RUNNING). Otherwise the run is
    refused (409) and nothing of its result is kept: the paid call stays logged (its ModelCall row), nothing else."""
    ensure_active(session, project, "run", task.id)
    if task.status is not TaskStatus.RUNNING:
        raise TransitionError(f"Task must be RUNNING to execute it (currently {task.status.value})")
    from app.runtime import active_run  # local: app/runtime.py imports this module

    if active_run(session, task) is not None:
        raise AppError(409, "run_exists", "This task has an open step-by-step run; it finishes the task")
    check_task_move(session, task, TaskStatus.COMPLETED, dependency_statuses=[])
    started_from = _stamp(task)

    if not task.owner:
        raise ManagerError("task has no owner agent")
    agent = session.scalar(select(Agent).where(Agent.name == task.owner, Agent.active))
    if agent is None:
        raise ManagerError(f"agent '{task.owner}' is not registered")

    task_settings = settings_layers.resolve(session, task_id=task.id)
    system = context.system_prompt(session, agent, agent.skills)
    user = context.task_context(session, task, settings=task_settings)
    response = gateway.call(
        agent.model_role, system=system, user=user, project_id=project.id, task_id=task.id, agent=agent.name,
        tools=agent.tools, permissions=agent.permissions,
        json_schema=TaskRunOut.model_json_schema(),
    )
    try:
        result = TaskRunOut.model_validate(response.data)
    except ValidationError as e:
        raise ManagerError(f"invalid task output: {e}") from e
    result = _apply_evidence_guard(session, project.id, task, result)

    # The result is staged first and the task itself is not touched yet: the lock below flushes what is staged (the
    # write lock on SQLite) and the task is read again under it, so what happened to it during the call is seen before
    # it is changed. If it moved on, rolling back drops the staged result with the locks.
    record_event(session, project.id, "task.status_changed", task.id,
                 **{"from": started_from[0].value, "to": "COMPLETED", "reason": "task executed"})
    record_event(session, project.id, "task.completed", task.id, **result.model_dump())
    memory.capture_from_task_output(session, project, task, agent.name, result, mode=task_settings["mode"])

    # Locks the project row before flushing the result (see app/pause.py::_paused), then locks the task row.
    paused = _paused_now(session, project)
    fresh = lock_task(session, task.id)
    if _moved_on(fresh, started_from):
        project_id, task_id = project.id, task.id  # read before the rollback expires them
        now = None if fresh is None else fresh.status
        session.rollback()
        text = _record_moved_meanwhile(session, project_id, task_id, "run", started_from[0], now)
        session.commit()
        raise TransitionError(
            f"The task changed while its model call ran (it is now {text}); this run's result was not applied"
        )
    check_task_move(session, task, TaskStatus.COMPLETED, dependency_statuses=[])  # the rules, on the fresh status
    task.output = result.model_dump()
    task.status = TaskStatus.COMPLETED
    session.flush()  # what lets the dependents' status query below see this task's new COMPLETED status
    if paused:
        # Paused while the model call ran: keep the paid-for result; release_withheld promotes on resume.
        if _created_dependents(session, project, task):
            record_event(session, project.id, "pause.withheld", task.id, action="promote_dependents")
        ready_ids = []
    else:
        ready_ids = _promote_ready_dependents(session, project, task)
    session.commit()
    return {"task": task, "ready_task_ids": ready_ids}


def unblock(session: Session, project: Project, task: Task) -> dict[str, Any]:
    """A BLOCKED task whose specialist exists now is CREATED again, and READY if its dependencies are done and its plan
    was approved (a plan still pending decides that itself). 409 while the specialist is still missing."""
    if task.status is not TaskStatus.BLOCKED:
        raise TransitionError(f"Task is {task.status.value}, not BLOCKED")
    agent = None if task.owner == "manager" else session.scalar(
        select(Agent).where(Agent.name == task.owner, Agent.active))
    if agent is None:
        raise AppError(409, "missing_specialist", f"Specialist '{task.owner}' is still not registered", agent=task.owner)
    check_task_move(session, task, TaskStatus.CREATED, dependency_statuses=[])
    task.status, task.reason = TaskStatus.CREATED, None
    record_event(session, project.id, "task.status_changed", task.id,
                 **{"from": "BLOCKED", "to": "CREATED", "reason": "specialist available"})
    ready: list[int] = []
    proposed = session.scalars(select(RunEvent).where(RunEvent.project_id == project.id,
                                                      RunEvent.type == "plan.proposed").order_by(RunEvent.id.desc()))
    plan = next((e for e in proposed if task.id in (e.payload.get("task_ids") or [])), None)
    approved = plan is not None and session.scalar(select(RunEvent.id).where(
        RunEvent.project_id == project.id, RunEvent.type == "decision.plan_approved", RunEvent.id > plan.id).limit(1))
    if approved:
        ready = _promote_ready(session, project.id, [task], "specialist available")
    commit_unless_paused(session, project.id, "unblock", task.id)
    return {"task": task, "ready_task_ids": ready}
