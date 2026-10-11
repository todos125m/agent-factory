from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app import auth
from app.db import get_session
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    name: str | None
    is_admin: bool


class StateOut(BaseModel):
    signed_in: bool
    accounts_exist: bool  # false: the first visitor creates the server's first (admin) account


class LoginIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)


class CodeIn(BaseModel):
    user_id: int | None = None  # another account's: the admin handing a new account over


class CodeOut(BaseModel):
    code: str
    expires_at: datetime
    user_id: int


class ClaimIn(BaseModel):
    user_id: int
    owner_token: str | None = Field(default=None, max_length=500)


@router.get("/state", response_model=StateOut)
def state(request: Request, session: Session = Depends(get_session)):
    """What the UI shows first: the app, a sign-in screen, or "create the first account". Never a 401."""
    signed_in = auth._active_session(session, request.cookies.get(auth.COOKIE)) is not None
    return StateOut(signed_in=signed_in, accounts_exist=auth.accounts_exist(session))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(auth.current_user)):
    return user


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, session: Session = Depends(get_session)):
    auth.end_session(session, request)
    auth.clear_cookie(response)
    response.status_code = 204
    return response


@router.post("/login-codes", response_model=CodeOut, status_code=201)
def make_login_code(body: CodeIn, user: User = Depends(auth.current_user), session: Session = Depends(get_session)):
    """A one-time code to sign in another device: your own account, or (admin) an account you created."""
    target = user
    if body.user_id is not None and body.user_id != user.id:
        if not user.is_admin:
            raise auth.forbidden("admin_only")
        target = session.get(User, body.user_id)
        if target is None:
            raise auth.not_found("user")
    code, expires = auth.new_login_code(session, target)
    session.commit()
    return CodeOut(code=code, expires_at=expires, user_id=target.id)


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, session: Session = Depends(get_session)):
    user = auth.redeem_login_code(session, body.code)
    token = auth.start_session(session, user)
    session.commit()
    auth.set_cookie(response, request, token)
    return user


@router.post("/claim", response_model=MeOut)
def claim(body: ClaimIn, request: Request, response: Response, session: Session = Depends(get_session)):
    """Sign in to an account made before login existed (its browser still knows its id). Once per account."""
    user = auth.claim(session, request, body.user_id, body.owner_token)
    token = auth.start_session(session, user)
    session.commit()
    auth.set_cookie(response, request, token)
    return user
