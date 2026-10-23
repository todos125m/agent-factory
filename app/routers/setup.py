"""The Setup page's data (app/setup.py): what this server has for reaching a model, and the admin's model test."""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import setup
from app.auth import current_user
from app.db import get_session
from app.gateway.service import Gateway
from app.models import User
from app.routers.manager import get_gateway

router = APIRouter(prefix="/setup", tags=["setup"])


@router.get("/status")
def status(probe: str | None = None, db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    return setup.status(db, me, probe_ollama=probe == "ollama")


@router.post("/test-model")
def test_model(db: Session = Depends(get_session), me: User = Depends(current_user),
               gateway: Gateway = Depends(get_gateway)) -> dict[str, Any]:
    """One tiny model call (admin only, on purpose): does the model answer with the settings as they are?"""
    return setup.test_model(db, gateway, me)
