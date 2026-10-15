from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.inbox import list_inbox
from app.schemas import InboxItem

router = APIRouter(tags=["inbox"])


@router.get("/inbox", response_model=list[InboxItem])
def get_inbox(session: Session = Depends(get_session)):
    """Everything waiting on the owner across all projects: plans awaiting approval,
    checkpoints awaiting a decision — derived from existing events, nothing new stored."""
    return list_inbox(session)
