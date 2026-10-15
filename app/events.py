from typing import Any

from sqlalchemy.orm import Session

from app.models import RunEvent


def record_event(
    session: Session, project_id: int, type: str, task_id: int | None = None, **payload: Any
) -> RunEvent:
    event = RunEvent(project_id=project_id, task_id=task_id, type=type, payload=payload)
    session.add(event)
    return event
