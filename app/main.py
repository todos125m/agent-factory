from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import Base, engine
from app.routers import projects, tasks, users


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Phase 1 convenience; switch to Alembic migrations once the schema settles.
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Agent Factory", version="0.1.0", lifespan=lifespan)
app.include_router(users.router)
app.include_router(projects.router)
app.include_router(tasks.router)


@app.get("/health")
def health():
    return {"status": "ok"}
