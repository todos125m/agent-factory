from typing import Any

from sqlalchemy.orm import Session

from app.models import RunEvent


def record_event(
    session: Session, project_id: int, type: str, task_id: int | None = None, **payload: Any
) -> RunEvent:
    event = RunEvent(project_id=project_id, task_id=task_id, type=type, payload=payload)
    session.add(event)
    return event


def record_factory_event(
    session: Session, factory_session_id: int, type: str, actor_id: int | None = None, **payload: Any
) -> RunEvent:
    """A change in an agent-building session (app/factory.py), with who made it."""
    event = RunEvent(factory_session_id=factory_session_id, type=type, payload={"actor_id": actor_id, **payload})
    session.add(event)
    return event
