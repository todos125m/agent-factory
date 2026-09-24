"""Agent Registry and skills (docs/ARCHITECTURE.md §10, §11, §27).

Definitions live as files under `registry/` (agents/*.json, skills/*.md with a small
front-matter block) and are synced into the database at startup.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Agent, Skill

REGISTRY_DIR = Path(__file__).resolve().parent.parent / "registry"

# Tools an agent may be granted (§27). Anything else is rejected at registration.
KNOWN_TOOLS = {"web_search", "repo_read", "repo_write", "terminal", "deploy"}
MODEL_ROLES = {"manager", "research", "coding", "cheap"}


class RegistryError(ValueError):
    pass


class AgentSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,99}$")
    version: int = 1
    description: str
    model_role: str
    capabilities: list[str] = Field(min_length=1)
    tools: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    instructions: str = ""

    @field_validator("model_role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in MODEL_ROLES:
            raise ValueError(f"model_role must be one of {sorted(MODEL_ROLES)}")
        return v

    @field_validator("tools")
    @classmethod
    def _tools(cls, v: list[str]) -> list[str]:
        if unknown := set(v) - KNOWN_TOOLS:
            raise ValueError(f"unknown tools: {sorted(unknown)}")
        return v


def parse_skill_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise RegistryError(f"{path.name}: missing front matter")
    head, body = text[4:].split("\n---\n", 1)
    meta = dict(line.split(":", 1) for line in head.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    return {"name": meta["name"], "version": int(meta.get("version", 1)), "summary": meta["summary"], "body": body.strip()}


def validate_agent(session: Session, spec: AgentSpec, *, updating: bool = False) -> None:
    """Checks before an agent becomes active (§11): known skills, no duplicate name or capability set."""
    known_skills = set(session.scalars(select(Skill.name)))
    if missing := set(spec.skills) - known_skills:
        raise RegistryError(f"unknown skills: {sorted(missing)}")
    if not updating and session.scalar(select(Agent).where(Agent.name == spec.name)):
        raise RegistryError(f"agent '{spec.name}' already exists")
    for other in session.scalars(select(Agent).where(Agent.active, Agent.name != spec.name)):
        if set(other.capabilities) == set(spec.capabilities):
            raise RegistryError(f"duplicate of agent '{other.name}' (same capabilities)")


def upsert_agent(session: Session, spec: AgentSpec) -> Agent:
    agent = session.scalar(select(Agent).where(Agent.name == spec.name))
    validate_agent(session, spec, updating=agent is not None)
    if agent is None:
        agent = Agent(name=spec.name)
        session.add(agent)
    for key, value in spec.model_dump().items():
        setattr(agent, key, value)
    return agent


def sync_from_files(session: Session, root: Path = REGISTRY_DIR) -> None:
    for path in sorted((root / "skills").glob("*.md")):
        data = parse_skill_file(path)
        skill = session.scalar(select(Skill).where(Skill.name == data["name"])) or Skill(name=data["name"])
        skill.version, skill.summary, skill.body = data["version"], data["summary"], data["body"]
        session.add(skill)
    session.flush()
    for path in sorted((root / "agents").glob("*.json")):
        upsert_agent(session, AgentSpec(**json.loads(path.read_text(encoding="utf-8"))))
    session.commit()


def find_by_capability(session: Session, capability: str) -> list[Agent]:
    agents = session.scalars(select(Agent).where(Agent.active).order_by(Agent.name))
    # Narrowest match first: the most specialised agent that has the capability.
    return sorted((a for a in agents if capability in a.capabilities), key=lambda a: len(a.capabilities))
