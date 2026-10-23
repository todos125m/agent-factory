from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user, require_admin
from app.db import get_session
from app.errors import AppError, not_found
from app.models import Agent, Feedback, Project, Skill, User
from app import registry
from app.registry import AgentSpec, RegistryError, find_by_capability, upsert_agent, visible_agents
from app.schemas import AgentOut, FeedbackOut, SkillOut, SkillSummary

router = APIRouter(tags=["registry"])


@router.get("/agents", response_model=list[AgentOut])
def list_agents(capability: str | None = None, session: Session = Depends(get_session), me: User = Depends(current_user)):
    if capability:
        return find_by_capability(session, capability, me.id)
    return session.scalars(visible_agents(me.id)).all()


@router.get("/agents/{name}", response_model=AgentOut)
def get_agent(name: str, session: Session = Depends(get_session), me: User = Depends(current_user)):
    agent = registry.agent_for(session, name, me.id, active=False)
    if not agent:
        raise not_found("agent")
    return agent


@router.post("/agents", response_model=AgentOut, status_code=201)
def register_agent(body: dict, session: Session = Depends(get_session), me: User = Depends(require_admin)):
    """The registry is shared by every account: only the admin adds to it."""
    try:
        spec = AgentSpec(**body)
        if session.scalar(select(Agent).where(Agent.name == spec.name)):
            raise RegistryError(f"agent '{spec.name}' already exists")
        agent = upsert_agent(session, spec)
    except (ValidationError, RegistryError) as e:
        raise AppError(422, "agent_invalid", str(e)) from e
    session.commit()
    return agent


@router.get("/agents/{name}/feedback", response_model=list[FeedbackOut])
def agent_feedback(name: str, session: Session = Depends(get_session), me: User = Depends(current_user)):
    """The feedback the signed-in account gave this agent (on its own projects)."""
    mine = select(Project.id).where(Project.owner_id == me.id)
    return session.scalars(
        select(Feedback).where(Feedback.agent == name, Feedback.project_id.in_(mine)).order_by(Feedback.id.desc())
    ).all()


@router.get("/skills", response_model=list[SkillSummary])
def list_skills(session: Session = Depends(get_session), me: User = Depends(current_user)):
    return session.scalars(select(Skill).order_by(Skill.name)).all()


@router.get("/skills/{name}", response_model=SkillOut)
def get_skill(name: str, session: Session = Depends(get_session), me: User = Depends(current_user)):
    skill = session.scalar(select(Skill).where(Skill.name == name))
    if not skill:
        raise not_found("skill")
    return skill
