"""Manager (Phase 2, docs/ARCHITECTURE.md §7-§9): plan a goal into a task graph, gate it on
approval, run per-task decision checkpoints. Deterministic logic (DAG validation, agent
resolution, state transitions) stays in code; the model only proposes plans and checkpoints.
"""

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import context, settings_layers
from app.events import record_event
from app.gateway.service import Gateway
from app.models import Agent, Project, RiskLevel, RunEvent, Task, TaskDependency, TaskStatus
from app.state_machine import TransitionError, check_task_transition


class ManagerError(ValueError):
    """A plan or checkpoint response failed a deterministic, code-side check."""


class PlanStepIn(BaseModel):
    title: str
    agent: str
    goal: str
    depends_on: list[int] = Field(default_factory=list)
    risk: RiskLevel = RiskLevel.LOW


class PlanIn(BaseModel):
    understanding: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlanStepIn]


class CheckpointOption(BaseModel):
    title: str
    tradeoff: str


class CheckpointOut(BaseModel):
    challenge: str
    options: list[CheckpointOption] = Field(min_length=2, max_length=4)
    recommended: int
    why: str


def _manager_agent(session: Session) -> Agent:
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
    return "\n".join(lines)


def create_plan(session: Session, gateway: Gateway, project: Project, *, feedback: str | None = None) -> dict[str, Any]:
    settings = settings_layers.resolve(session, project_id=project.id)
    system = context.system_prompt(session, _manager_agent(session), ["plan-goal", "delegate", "evidence"])
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

    task_ids: list[int | None] = []
    specialists_requested: list[str] = []
    for step in plan.steps:
        agent = None if step.agent == "manager" else session.scalar(
            select(Agent).where(Agent.name == step.agent, Agent.active)
        )
        if agent is None:
            record_event(
                session, project.id, "specialist.requested", None,
                title=step.title, agent=step.agent, goal=step.goal, risk=step.risk.value,
            )
            specialists_requested.append(step.agent)
            task_ids.append(None)
            continue
        task = Task(project_id=project.id, title=step.title, owner=step.agent, input={"goal": step.goal}, risk=step.risk)
        session.add(task)
        session.flush()
        task_ids.append(task.id)

    for step, tid in zip(plan.steps, task_ids):
        if tid is None:
            continue
        deps = sorted({task_ids[d] for d in step.depends_on if task_ids[d] is not None})
        session.get(Task, tid).dependencies = [TaskDependency(depends_on_id=d) for d in deps]
    session.flush()

    created_ids = [tid for tid in task_ids if tid is not None]
    record_event(session, project.id, "plan.proposed", None,
                 understanding=plan.understanding, assumptions=plan.assumptions, task_ids=created_ids)
    record_event(session, project.id, "approval.requested", None, task_ids=created_ids)
    session.commit()
    return {
        "understanding": plan.understanding,
        "assumptions": plan.assumptions,
        "task_ids": created_ids,
        "specialists_requested": specialists_requested,
    }


def approve_plan(session: Session, project: Project) -> dict[str, Any]:
    ready_ids: list[int] = []
    tasks = session.scalars(select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.CREATED)).all()
    for task in tasks:
        deps = _dependency_statuses(session, task)
        try:
            check_task_transition(task.status, TaskStatus.READY, dependency_statuses=deps,
                                   retries=task.retries, max_retries=task.max_retries)
        except TransitionError:
            continue
        task.status = TaskStatus.READY
        record_event(session, project.id, "task.status_changed", task.id,
                      **{"from": "CREATED", "to": "READY", "reason": "plan approved"})
        ready_ids.append(task.id)
    record_event(session, project.id, "decision.plan_approved", None, ready_task_ids=ready_ids)
    session.commit()
    return {"ready_task_ids": ready_ids}


def reject_plan(session: Session, project: Project, feedback: str) -> list[int]:
    last_plan = session.scalar(
        select(RunEvent)
        .where(RunEvent.project_id == project.id, RunEvent.type == "plan.proposed")
        .order_by(RunEvent.id.desc())
    )
    proposed_ids = last_plan.payload.get("task_ids", []) if last_plan else []
    tasks = (
        session.scalars(select(Task).where(Task.id.in_(proposed_ids), Task.status == TaskStatus.CREATED)).all()
        if proposed_ids else []
    )
    deleted_ids = [t.id for t in tasks]
    for t in tasks:
        session.delete(t)
    record_event(session, project.id, "decision.plan_rejected", None, feedback=feedback, deleted_task_ids=deleted_ids)
    session.commit()
    return deleted_ids


def create_checkpoint(session: Session, gateway: Gateway, project: Project, task: Task) -> dict[str, Any]:
    system = context.system_prompt(session, _manager_agent(session), ["decision-checkpoint", "evidence"])
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
    rule = settings["approvals"][task.risk.value]
    auto = settings["mode"] == "automatic" and rule != "user"
    if auto:
        _decide(session, task, checkpoint.recommended, None, auto=True)
    session.commit()
    return {"checkpoint": checkpoint.model_dump(), "auto_decided": auto}


def _decide(session: Session, task: Task, option: int, note: str | None, *, auto: bool) -> None:
    check_task_transition(task.status, TaskStatus.RUNNING, dependency_statuses=[],
                           retries=task.retries, max_retries=task.max_retries)
    record_event(session, task.project_id, "decision.step", task.id, option=option, note=note, auto=auto)
    task.status = TaskStatus.RUNNING


def decide(session: Session, task: Task, option: int, note: str | None) -> None:
    _decide(session, task, option, note, auto=False)
    session.commit()
