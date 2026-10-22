"""Building an agent (app/factory.py): one owner's sessions, the adaptive intake and the brief approval."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy.orm import Session

from app import design, factory, preferences
from app.auth import current_user
from app.db import get_session
from app.gateway.service import Gateway
from app.models import FactorySession, User
from app.preferences import AUDIENCE_LEVELS, DEFAULT_LOCALE, LOCALES
from app.routers.manager import get_gateway
from app.storable import Int32, StorableIn, limit

router = APIRouter(prefix="/factory", tags=["factory"])

RequestId = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class SessionCreate(StorableIn):
    title: str = Field(default="", max_length=limit(FactorySession, "title"))
    request_id: str = RequestId


class Change(StorableIn):
    expected_revision: Int32
    request_id: str = RequestId


class AnswerIn(Change):
    field: str = Field(max_length=60)
    value: str | None = Field(default=None, max_length=factory.MAX_ANSWER_CHARS)
    dont_know: bool = False


class ApproveIn(Change):
    brief_version: Int32


class ChangesIn(Change):
    note: str | None = Field(default=None, max_length=2000)


class PauseIn(Change):
    paused: bool


class DesignEditIn(Change):
    branch: str = Field(max_length=30)
    changes: dict[str, Any]


class DesignConfirmIn(Change):
    branch: str = Field(max_length=30)
    blueprint_version: Int32


class DesignApproveIn(Change):
    blueprint_version: Int32


class DesignRevertIn(Change):
    to_version: Int32


def _view(db: Session, me: User, fs, locale: str | None, duplicate: bool = False) -> dict[str, Any]:
    """The session as the owner sees it, in their language and at their explanation level (display only)."""
    shown = preferences.effective(preferences.get(db, me.id))
    return factory.state(db, fs, locale=locale if locale in LOCALES else shown["ui_locale"],
                         level=shown["audience_level"], duplicate=duplicate)


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_session), me: User = Depends(current_user)) -> list[dict[str, Any]]:
    return [{"id": s.id, "title": s.title, "stage": s.stage, "revision": s.revision, "updated_at": s.updated_at}
            for s in factory.list_sessions(db, me)]


@router.post("/sessions", status_code=201)
def create_session(body: SessionCreate, locale: str = Query(default=DEFAULT_LOCALE),
                   db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    fs = factory.create_session(db, me, title=body.title.strip(), locale=locale, request_id=body.request_id)
    return _view(db, me, fs, locale)


@router.get("/sessions/{session_id}")
def get_session_state(session_id: int, locale: str | None = None, db: Session = Depends(get_session),
                      me: User = Depends(current_user)) -> dict[str, Any]:
    return _view(db, me, factory.load_session(db, session_id, me), locale)


@router.post("/sessions/{session_id}/answer")
def answer(session_id: int, body: AnswerIn, locale: str | None = None, db: Session = Depends(get_session),
           me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = factory.answer(db, me, session_id, field=body.field, value=body.value, dont_know=body.dont_know,
                                 expected_revision=body.expected_revision, request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/approve")
def approve(session_id: int, body: ApproveIn, locale: str | None = None, db: Session = Depends(get_session),
            me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = factory.approve(db, me, session_id, brief_version=body.brief_version,
                                  expected_revision=body.expected_revision, request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/request-changes")
def request_changes(session_id: int, body: ChangesIn, locale: str | None = None, db: Session = Depends(get_session),
                    me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = factory.request_changes(db, me, session_id, note=body.note, expected_revision=body.expected_revision,
                                          request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/pause")
def pause(session_id: int, body: PauseIn, locale: str | None = None, db: Session = Depends(get_session),
          me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = factory.set_paused(db, me, session_id, paused=body.paused, expected_revision=body.expected_revision,
                                     request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/cancel")
def cancel(session_id: int, body: Change, locale: str | None = None, db: Session = Depends(get_session),
           me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = factory.cancel(db, me, session_id, expected_revision=body.expected_revision,
                                 request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


# ---------- the design (app/design.py) ----------


@router.post("/sessions/{session_id}/design/edit")
def design_edit(session_id: int, body: DesignEditIn, locale: str | None = None, db: Session = Depends(get_session),
                me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = design.edit(db, me, session_id, branch=body.branch, changes=body.changes,
                              expected_revision=body.expected_revision, request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/design/confirm")
def design_confirm(session_id: int, body: DesignConfirmIn, locale: str | None = None,
                   db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = design.confirm_branch(db, me, session_id, branch=body.branch, blueprint_version=body.blueprint_version,
                                        expected_revision=body.expected_revision, request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/design/revert")
def design_revert(session_id: int, body: DesignRevertIn, locale: str | None = None,
                  db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = design.revert(db, me, session_id, to_version=body.to_version,
                                expected_revision=body.expected_revision, request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/design/approve")
def design_approve(session_id: int, body: DesignApproveIn, locale: str | None = None,
                   db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    fs, applied = design.approve(db, me, session_id, blueprint_version=body.blueprint_version,
                                 expected_revision=body.expected_revision, request_id=body.request_id)
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)


@router.post("/sessions/{session_id}/design/assist")
def design_assist(session_id: int, body: Change, locale: str | None = None, db: Session = Depends(get_session),
                  me: User = Depends(current_user), gateway: Gateway = Depends(get_gateway)) -> dict[str, Any]:
    """The only paid call of a build, asked for by the owner, held against the build's budget (402 when over)."""
    from app.errors import AppError
    from app.gateway.providers import ProviderError
    from app.gateway.service import BudgetExceeded

    try:
        fs, applied = design.assist(db, gateway, me, session_id, expected_revision=body.expected_revision,
                                    request_id=body.request_id)
    except BudgetExceeded as e:
        raise AppError(402, "budget_exceeded", str(e)) from e
    except ProviderError as e:
        raise AppError(502, "provider_unavailable", str(e)) from e
    return _view(db, me, factory.load_session(db, fs.id, me), locale, duplicate=not applied)
