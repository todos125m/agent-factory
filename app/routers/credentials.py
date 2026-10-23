"""Connections (app/credentials.py): the owner's provider keys — added once, shown masked, tested on request, revoked."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app import credentials
from app.auth import current_user
from app.db import get_session
from app.models import Credential, User
from app.storable import StorableIn, limit

router = APIRouter(prefix="/credentials", tags=["credentials"])


class CredentialCreate(StorableIn):
    name: str = Field(min_length=1, max_length=limit(Credential, "name"))
    provider: str = Field(max_length=limit(Credential, "provider"))
    # Not bounded here: a too-long key must not be echoed back in a validation error (app/credentials.py::add checks it).
    key: str = Field(max_length=2000, repr=False)
    request_id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


@router.get("")
def list_credentials(db: Session = Depends(get_session), me: User = Depends(current_user)) -> dict[str, Any]:
    return credentials.listing(db, me)


@router.post("", status_code=201)
def add_credential(body: CredentialCreate, db: Session = Depends(get_session), me: User = Depends(current_user)):
    credential, applied = credentials.add(db, me, name=body.name.strip(), provider=body.provider, key=body.key,
                                          request_id=body.request_id)
    return {**credentials.view(credential), "duplicate": not applied}


@router.post("/{credential_id}/test")
def test_credential(credential_id: int, db: Session = Depends(get_session), me: User = Depends(current_user)):
    """One free request to the provider, made because you asked; the result is what the provider answered."""
    return credentials.view(credentials.test(db, me, credential_id))


@router.delete("/{credential_id}")
def revoke_credential(credential_id: int, db: Session = Depends(get_session), me: User = Depends(current_user)):
    return credentials.view(credentials.revoke(db, me, credential_id))
