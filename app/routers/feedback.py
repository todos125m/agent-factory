from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Feedback, Project, Task
from app.schemas import FeedbackIn, FeedbackOut

router = APIRouter(tags=["feedback"])


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def create_feedback(body: FeedbackIn, session: Session = Depends(get_session)):
    if not session.get(Project, body.project_id):
        raise HTTPException(404, "Project not found")
    if body.task_id is not None and not session.get(Task, body.task_id):
        raise HTTPException(404, "Task not found")
    feedback = Feedback(**body.model_dump())
    session.add(feedback)
    session.commit()
    return feedback
