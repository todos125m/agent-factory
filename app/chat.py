"""Owner <-> manager chat, per project. One model call per message: a short fixed
skill plus only the project goal, a compact task-status list and the last 8 messages
(token discipline — no full histories, no skills beyond the one this needs).

The owner's message is stored only together with the manager's reply, after the model call (owner
decision d19): a refused or failed call (pause, budget, provider error, invalid reply) adds nothing to
the chat, so a resend leaves no duplicate. Nothing is written before the call either: on SQLite an open
write transaction holds the database-wide lock for the whole call, and the owner's pause (any write)
would fail with "database is locked".
"""

import re
from typing import Any

from pydantic import BaseModel, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import context
from app.gateway.service import Gateway
from app.manager import ManagerError, ensure_active, manager_agent
from app.models import ChatMessage, Project, Task, utcnow

_ACTION_RE = re.compile(r"^(none|plan|approve|checkpoint:\d{1,12})$")  # fits ChatMessage.suggested_action
HISTORY_LIMIT = 8


def _unstorable(text: str) -> str | None:
    """Why the database would refuse `text`, if it would: both halves of a turn are stored only after the paid
    call (d19), so this is checked before the store can fail there. NUL: PostgreSQL text can't hold one; a lone
    surrogate: not encodable as UTF-8."""
    if "\x00" in text:
        return "must not contain NUL characters"
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return "is not valid Unicode (lone surrogate)"
    return None


class ChatReplyOut(BaseModel):
    reply: str
    suggested_action: str = "none"

    @field_validator("reply")
    @classmethod
    def _storable_reply(cls, v: str) -> str:
        if problem := _unstorable(v):
            raise ValueError(f"reply {problem}")
        return v

    @field_validator("suggested_action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if not _ACTION_RE.match(v):
            raise ValueError("suggested_action must be none, plan, approve or checkpoint:<task_id>")
        return v


def _chat_user_content(session: Session, project: Project, new: ChatMessage) -> str:
    tasks = session.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.id)).all()
    task_lines = "\n".join(f"- #{t.id} {t.title} [{t.status.value}]" for t in tasks) or "(no tasks yet)"
    stored = session.scalars(
        select(ChatMessage)
        .where(ChatMessage.project_id == project.id)
        .order_by(ChatMessage.id.desc())
        .limit(HISTORY_LIMIT - 1)  # the owner's new message, not stored yet, is the last of the 8
    ).all()
    history_lines = "\n".join(f"{m.role}: {m.text}" for m in [*reversed(stored), new])
    return f"Project goal: {project.goal}\nTasks:\n{task_lines}\nRecent chat:\n{history_lines}"


def send_message(session: Session, gateway: Gateway, project: Project, text: str) -> dict[str, Any]:
    if problem := _unstorable(text):  # refused before the call, not at the store after it
        raise ManagerError(f"chat text {problem}")
    ensure_active(session, project, "chat")
    # In the prompt now, in the session only with the reply: nothing is written while the model runs (d19).
    user_msg = ChatMessage(project_id=project.id, role="user", text=text, created_at=utcnow())
    system = context.system_prompt(session, manager_agent(session), ["chat-reply"])
    user = _chat_user_content(session, project, user_msg)
    response = gateway.call(
        "manager", system=system, user=user, project_id=project.id, agent="manager",
        json_schema=ChatReplyOut.model_json_schema(),
    )
    try:
        reply = ChatReplyOut.model_validate(response.data)
    except ValidationError as e:
        raise ManagerError(f"invalid chat response: {e}") from e

    # Kept even if the owner paused while the call ran: the reply is paid for and starts nothing (d16).
    manager_msg = ChatMessage(
        project_id=project.id, role="manager", text=reply.reply, suggested_action=reply.suggested_action,
    )
    session.add_all([user_msg, manager_msg])  # inserted in this order: the user's message gets the lower id
    session.commit()
    return {"user": user_msg, "manager": manager_msg}


def list_messages(session: Session, project: Project) -> list[ChatMessage]:
    return list(session.scalars(
        select(ChatMessage).where(ChatMessage.project_id == project.id).order_by(ChatMessage.id)
    ))
