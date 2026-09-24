from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import Base, SessionLocal, engine
from app.registry import sync_from_files
from app.routers import agents, manager, projects, settings, tasks, users

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


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


# The app shell (dashboard, projects, agents, ... — see web/index.html) is a static
# single-page UI; it calls the API above from the browser once each section is wired up.
app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")
