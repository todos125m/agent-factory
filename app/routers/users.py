from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import preferences
from app.db import get_session
from app.errors import AppError, not_found
from app.models import User, UserPreference
from app.schemas import PreferencesOut, PreferencesUpdate, UserCreate, UserOut

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, session: Session = Depends(get_session)):
    if session.scalar(select(User).where(User.email == body.email)):
        raise AppError(409, "email_taken", "Email already registered")
    user = User(email=body.email, name=body.name)
    session.add(user)
    session.commit()
    return user


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, session: Session = Depends(get_session)):
    user = session.get(User, user_id)
    if not user:
        raise not_found("user")
    return user


def _preferences_out(user_id: int, pref: UserPreference | None) -> PreferencesOut:
    return PreferencesOut(
        user_id=user_id,
        **preferences.chosen(pref),
        effective=preferences.effective(pref),
        updated_at=pref.updated_at if pref else None,
    )


@router.get("/{user_id}/preferences", response_model=PreferencesOut)
def get_preferences(user_id: int, session: Session = Depends(get_session)):
    if not session.get(User, user_id):
        raise not_found("user")
    return _preferences_out(user_id, preferences.get(session, user_id))


@router.patch("/{user_id}/preferences", response_model=PreferencesOut)
def update_preferences(user_id: int, body: PreferencesUpdate, session: Session = Depends(get_session)):
    """Sets only the keys sent. Two tabs saving at once both land (per key, the later one wins); a first save racing
    another first save finds the row the other created and updates it, instead of failing on the unique user_id."""
    if not session.get(User, user_id):
        raise not_found("user")
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
