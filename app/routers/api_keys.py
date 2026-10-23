"""The owner's side of the external API (app/external.py): make a key (its secret is shown once), list, revoke."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app import external
from app.auth import current_user
from app.db import get_session
from app.models import ApiKey, User
from app.storable import Int32, StorableIn, limit

router = APIRouter(prefix="/api-keys", tags=["api keys"])


class KeyCreate(StorableIn):
    name: str = Field(min_length=1, max_length=limit(ApiKey, "name"))
    agent: str = Field(max_length=limit(ApiKey, "agent_name"))
    agent_version: Int32
    project_id: Int32
    scopes: list[str] = Field(default_factory=lambda: list(external.SCOPES), max_length=len(external.SCOPES))
    max_runs_per_day: Int32 = external.DEFAULT_RUNS_PER_DAY
    budget_per_run_usd: float = Field(default=external.DEFAULT_BUDGET_PER_RUN, allow_inf_nan=False)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("")
def list_keys(db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    return external.listing(db, me)


@router.post("", status_code=201)
def create_key(body: KeyCreate, db: Session = Depends(get_session), me: User = Depends(current_user)):
    """The secret is in this answer and nowhere after it: copy it now."""
    key, secret = external.create(db, me, name=body.name.strip(), agent=body.agent, agent_version=body.agent_version,
                                  project_id=body.project_id, scopes=body.scopes,
                                  max_runs_per_day=body.max_runs_per_day, budget_per_run_usd=body.budget_per_run_usd,
                                  request_id=body.request_id)
    return {**external.view(key, 0), "secret": secret, "duplicate": secret is None}


@router.delete("/{key_id}")
def revoke_key(key_id: int, db: Session = Depends(get_session), me: User = Depends(current_user)):
    key = external.revoke(db, me, key_id)
    return external.view(key, external._used(db, key))
