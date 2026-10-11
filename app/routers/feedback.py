from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import current_user, own_project
from app.db import get_session
from app.errors import not_found
from app.models import Feedback, Task, User
from app.schemas import FeedbackIn, FeedbackOut

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def create_feedback(body: FeedbackIn, session: Session = Depends(get_session), me: User = Depends(current_user)):
    own_project(session, body.project_id, me)
    if body.task_id is not None:
        task = session.get(Task, body.task_id)
        if task is None or task.project_id != body.project_id:  # a task of another project isn't this one's
            raise not_found("task")
    feedback = Feedback(**body.model_dump())
    session.add(feedback)
    session.commit()
    return feedback
