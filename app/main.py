from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import Base, SessionLocal, engine
from app.registry import sync_from_files
from app.routers import agents, manager, projects, settings, tasks, users


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Phase 1 convenience; switch to Alembic migrations once the schema settles.
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        sync_from_files(session)
    yield


app = FastAPI(title="Agent Factory", version="0.2.0", lifespan=lifespan)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(manager.router)
app.include_router(settings.router)
app.include_router(agents.router)


@app.get("/health")
def health():
    return {"status": "ok"}
