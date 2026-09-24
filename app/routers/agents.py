from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Agent, Skill
from app.registry import AgentSpec, RegistryError, find_by_capability, upsert_agent
from app.schemas import AgentOut, SkillOut, SkillSummary

router = APIRouter(tags=["registry"])


@router.get("/agents", response_model=list[AgentOut])
def list_agents(capability: str | None = None, session: Session = Depends(get_session)):
    if capability:
        return find_by_capability(session, capability)
    return session.scalars(select(Agent).order_by(Agent.name)).all()


@router.get("/agents/{name}", response_model=AgentOut)
def get_agent(name: str, session: Session = Depends(get_session)):
    agent = session.scalar(select(Agent).where(Agent.name == name))
    if not agent:
        raise HTTPException(404, "Agent not found")
    return agent


@router.post("/agents", response_model=AgentOut, status_code=201)
def register_agent(body: dict, session: Session = Depends(get_session)):
    try:
        spec = AgentSpec(**body)
        if session.scalar(select(Agent).where(Agent.name == spec.name)):
            raise RegistryError(f"agent '{spec.name}' already exists")
        agent = upsert_agent(session, spec)
    except (ValidationError, RegistryError) as e:
        raise HTTPException(422, str(e)) from e
    session.commit()
    return agent


@router.get("/skills", response_model=list[SkillSummary])
def list_skills(session: Session = Depends(get_session)):
    return session.scalars(select(Skill).order_by(Skill.name)).all()


@router.get("/skills/{name}", response_model=SkillOut)
def get_skill(name: str, session: Session = Depends(get_session)):
    skill = session.scalar(select(Skill).where(Skill.name == name))
    if not skill:
        raise HTTPException(404, "Skill not found")
    return skill
