"""Decision inbox (owner-facing): everything across all projects waiting on the owner —
plans awaiting approval, checkpoints awaiting a decision, and READY tasks with no checkpoint yet
(waiting to start). A paused project lists nothing until it is resumed (owner decision d17): every
action on its cards is refused while paused, and its own page still shows its plan and tasks.
Derived from existing RunEvents and Task state; nothing new is stored (§ decision inbox).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, RunEvent, Task, TaskStatus


def _pending_plan(events: list[RunEvent]) -> RunEvent | None:
    """The latest 'approval.requested' event not yet followed by an approve/reject decision."""
    last_requested = None
    for e in events:
        if e.type == "approval.requested":
            last_requested = e
        elif e.type in ("decision.plan_approved", "decision.plan_rejected") and last_requested is not None:
            if e.id > last_requested.id:
                last_requested = None
    return last_requested


def _pending_checkpoints(events: list[RunEvent]) -> dict[int, RunEvent]:
    """task_id -> latest 'checkpoint.created' event not yet followed by a 'decision.step' for that task."""
    last_checkpoint: dict[int, RunEvent] = {}
    last_decision: dict[int, RunEvent] = {}
    for e in events:
        if e.task_id is None:
            continue
        if e.type == "checkpoint.created":
            last_checkpoint[e.task_id] = e
        elif e.type == "decision.step":
            last_decision[e.task_id] = e
    return {
        tid: cp for tid, cp in last_checkpoint.items()
        if tid not in last_decision or last_decision[tid].id < cp.id
    }


def _last_ready(events: list[RunEvent]) -> dict[int, RunEvent]:
    """task_id -> the event that last moved it to READY."""
    last: dict[int, RunEvent] = {}
    for e in events:
        if e.type == "task.status_changed" and e.task_id is not None and e.payload.get("to") == "READY":
            last[e.task_id] = e
    return last


def list_inbox(session: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for project in session.scalars(select(Project).order_by(Project.id)).all():
        if project.paused:
            continue
        events = list(session.scalars(
            select(RunEvent).where(RunEvent.project_id == project.id).order_by(RunEvent.id)
        ))

        plan_event = _pending_plan(events)
        if plan_event is not None:
            plan_proposed = next((e for e in reversed(events) if e.type == "plan.proposed" and e.id <= plan_event.id), None)
            task_ids = plan_event.payload.get("task_ids", [])
            tasks = session.scalars(select(Task).where(Task.id.in_(task_ids)).order_by(Task.id)).all() if task_ids else []
            understanding = plan_proposed.payload.get("understanding", "") if plan_proposed else ""
            assumptions = plan_proposed.payload.get("assumptions", []) if plan_proposed else []
            items.append({
                "kind": "plan",
                "project_id": project.id,
                "project_title": project.title,
                "task_id": None,
                "challenge": understanding,
                "options": [{"title": t.title, "owner": t.owner, "risk": t.risk.value} for t in tasks],
                "recommended": None,
                "why": " ".join(assumptions),
                "created_at": plan_event.created_at,
            })

        last_ready = _last_ready(events)
        # A checkpoint opened before its task last became READY (e.g. before a retry) is stale: the task
        # waits to start again, not on that old decision.
        pending = {
            tid: cp for tid, cp in _pending_checkpoints(events).items()
            if tid not in last_ready or cp.id > last_ready[tid].id
        }
        for task_id, cp_event in pending.items():
            task = session.get(Task, task_id)
            if task is None or task.status is not TaskStatus.READY:
                continue
            items.append({
                "kind": "checkpoint",
                "project_id": project.id,
                "project_title": project.title,
                "task_id": task_id,
                "challenge": cp_event.payload.get("challenge", ""),
                "options": cp_event.payload.get("options", []),
                "recommended": cp_event.payload.get("recommended"),
                "why": cp_event.payload.get("why", ""),
                "created_at": cp_event.created_at,
            })

        for task in session.scalars(
            select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.READY).order_by(Task.id)
        ):
            if task.id in pending:  # already listed above, as its checkpoint
                continue
            goal = (task.input or {}).get("goal")
            items.append({
                "kind": "ready",
                "project_id": project.id,
                "project_title": project.title,
                "task_id": task.id,
                "challenge": task.title,
                "options": [{"title": task.title, "owner": task.owner, "risk": task.risk.value}],
                "recommended": None,
                "why": goal if isinstance(goal, str) else "",
                "created_at": last_ready[task.id].created_at if task.id in last_ready else task.updated_at,
            })

    items.sort(key=lambda i: i["created_at"])
    return items
