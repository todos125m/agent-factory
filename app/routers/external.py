"""The external API: what an outside program may do with its key (app/external.py) — run its agent, read the run, answer
the agent's questions, cancel. Nothing here approves anything."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app import external
from app.db import get_session
from app.models import ApiKey
from app.storable import StorableIn

router = APIRouter(prefix="/api/v1", tags=["external api"])

RequestId = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ExternalRunIn(StorableIn):
    input: str = Field(min_length=1, max_length=external.MAX_INPUT_CHARS)
    request_id: str = RequestId


class ExternalAnswerIn(StorableIn):
    text: str = Field(min_length=1, max_length=external.MAX_INPUT_CHARS)


@router.get("/me")
def whoami(key: ApiKey = Depends(external.current_key), db: Session = Depends(get_session)) -> dict[str, Any]:
    """What this key may do and how much of its day is left (never the secret)."""
    return external.view(key, external._used(db, key))


@router.post("/runs", status_code=201)
def create_run(body: ExternalRunIn, key: ApiKey = Depends(external.current_key), db: Session = Depends(get_session)):
    """Start a run of the key's agent. Poll GET /runs/{run_id}. The same request_id again returns the same run."""
    run, created = external.start_run(db, key, text=body.input, request_id=body.request_id)
    return {**external.run_view(db, run), "duplicate": not created}


@router.get("/runs/{run_id}")
def get_run(run_id: int, key: ApiKey = Depends(external.current_key), db: Session = Depends(get_session)):
    external.need(key, "read")
    return external.run_view(db, external.load_run(db, key, run_id))


@router.post("/runs/{run_id}/answer")
def answer_run(run_id: int, body: ExternalAnswerIn, key: ApiKey = Depends(external.current_key),
               db: Session = Depends(get_session)):
    """Answer the agent's question. A tool or an unknown outcome can only be settled by the owner."""
    return external.run_view(db, external.answer_run(db, key, run_id, body.text))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: int, key: ApiKey = Depends(external.current_key), db: Session = Depends(get_session)):
    return external.run_view(db, external.cancel_run(db, key, run_id))
