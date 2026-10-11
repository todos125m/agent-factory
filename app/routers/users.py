from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auth, preferences
from app.db import get_session
from app.errors import AppError, not_found
from app.models import User, UserPreference
from app.schemas import PreferencesOut, PreferencesUpdate, UserCreate, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    body: UserCreate, request: Request, response: Response, session: Session = Depends(get_session),
    caller: User | None = Depends(auth.optional_user),
):
    """The server's first account (anyone: it becomes the admin and this browser is signed in as it), or an account
    the admin makes for someone else (the admin stays signed in and hands it over with POST /auth/login-codes)."""
    if auth.accounts_exist(session):
        if caller is None:
            raise auth.unauthenticated()
        if not caller.is_admin:
            raise auth.forbidden("admin_only")
    if session.scalar(select(User).where(User.email == body.email)):
        raise AppError(409, "email_taken", "Email already registered")
    user = auth.create_account(session, body.email, body.name)
    token = auth.start_session(session, user) if caller is None else None
    session.commit()
    if token is not None:
        auth.set_cookie(response, request, token)
    return user


@router.get("", response_model=list[UserOut])
def list_users(session: Session = Depends(get_session), _: User = Depends(auth.require_admin)):
    """Every account: the admin's view, to hand new ones over."""
    return session.scalars(select(User).order_by(User.id)).all()


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, session: Session = Depends(get_session), me: User = Depends(auth.current_user)):
    user = session.get(User, user_id)
    if not user or (user.id != me.id and not me.is_admin):
        raise not_found("user")
    return user


def _own(user_id: int, me: User) -> None:
    """Preferences are the account's own: anyone else's (the admin's view included) is the same 404 as a missing one."""
    if user_id != me.id:
        raise not_found("user")


def _preferences_out(user_id: int, pref: UserPreference | None) -> PreferencesOut:
    return PreferencesOut(
        user_id=user_id,
        **preferences.chosen(pref),
        effective=preferences.effective(pref),
        updated_at=pref.updated_at if pref else None,
    )


@router.get("/{user_id}/preferences", response_model=PreferencesOut)
def get_preferences(user_id: int, session: Session = Depends(get_session), me: User = Depends(auth.current_user)):
    _own(user_id, me)
    return _preferences_out(user_id, preferences.get(session, user_id))


@router.patch("/{user_id}/preferences", response_model=PreferencesOut)
def update_preferences(
    user_id: int, body: PreferencesUpdate, session: Session = Depends(get_session),
    me: User = Depends(auth.current_user),
):
    """Sets only the keys sent. Two tabs saving at once both land (per key, the later one wins); a first save racing
    another first save finds the row the other created and updates it, instead of failing on the unique user_id."""
    _own(user_id, me)
    changes = body.model_dump(include=body.model_fields_set)
    for attempt in range(2):
        pref = preferences.get(session, user_id) or UserPreference(user_id=user_id)
        for key, value in changes.items():
            setattr(pref, key, value)
        session.add(pref)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if attempt:
                raise
            continue
        return _preferences_out(user_id, pref)
    raise AssertionError("unreachable")
