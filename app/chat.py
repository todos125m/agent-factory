"""Owner <-> manager chat, per project. One model call per message: a short fixed
skill plus only the project goal, a compact task-status list and the last 8 messages
(token discipline — no full histories, no skills beyond the one this needs).
"""

import re
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import context
from app.gateway.service import Gateway
from app.manager import ManagerError, ensure_active, manager_agent
from app.models import ChatMessage, Project, Task

_ACTION_RE = re.compile(r"^(none|plan|approve|checkpoint:\d+)$")
HISTORY_LIMIT = 8


class ChatReplyOut(BaseModel):
    reply: str
    suggested_action: str = "none"

    @field_validator("suggested_action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if not _ACTION_RE.match(v):
            raise ValueError("suggested_action must be none, plan, approve or checkpoint:<task_id>")
        return v


def _chat_user_content(session: Session, project: Project) -> str:
    tasks = session.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id)).all()
    task_lines = "\n".join(f"- #{t.id} {t.title} [{t.status.value}]" for t in tasks) or "(no tasks yet)"
    history = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.project_id == project.id)
        .order_by(ChatMessage.id.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    history_lines = "\n".join(f"{m.role}: {m.text}" for m in reversed(history))
    return f"Project goal: {project.goal}\nTasks:\n{task_lines}\nRecent chat:\n{history_lines}"


def send_message(session: Session, gateway: Gateway, project: Project, text: str) -> dict[str, Any]:
    ensure_active(session, project, "chat")
    user_msg = ChatMessage(project_id=project.id, role="user", text=text)
    session.add(user_msg)
    session.flush()  # so the user's own message counts toward the last-8 window

    system = context.system_prompt(session, manager_agent(session), ["chat-reply"])
    user = _chat_user_content(session, project)
    response = gateway.call(
        "manager", system=system, user=user, project_id=project.id, agent="manager",
        json_schema=ChatReplyOut.model_json_schema(),
    )
    try:
        reply = ChatReplyOut.model_validate(response.data)
    except ValidationError as e:
        raise ManagerError(f"invalid chat response: {e}") from e

    manager_msg = ChatMessage(
        project_id=project.id, role="manager", text=reply.reply, suggested_action=reply.suggested_action,
    )
    session.add(manager_msg)
    session.commit()
    return {"user": user_msg, "manager": manager_msg}


def list_messages(session: Session, project: Project) -> list[ChatMessage]:
    return list(session.scalars(
        select(ChatMessage).where(ChatMessage.project_id == project.id).order_by(ChatMessage.id)
    ))
