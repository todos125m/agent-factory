import math
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import sources
from app.db import Base, SessionLocal, engine
from app.registry import sync_from_files
from app.routers import agents, benchmarks, blueprints, feedback, inbox, interview, manager, projects, settings, tasks, users
from app.state_machine import TransitionError

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Phase 1 convenience; switch to Alembic migrations once the schema settles.
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        sync_from_files(session)
    sources.load_rules()  # fail fast on a broken registry/source_tiers.yaml, not mid task run
    yield


app = FastAPI(title="Agent Factory", version="0.2.0", lifespan=lifespan)


_NON_FINITE_AS_TEXT = {float: lambda f: f if math.isfinite(f) else str(f)}


@app.exception_handler(RequestValidationError)
async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """FastAPI's default 422, except that a NaN/Infinity input (Python's JSON parser accepts both) is
    echoed as text: the default handler can't serialize it and answered 500 instead."""
    detail = jsonable_encoder(exc.errors(), custom_encoder=_NON_FINITE_AS_TEXT)
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(TransitionError)
async def transition_error(_: Request, exc: TransitionError) -> JSONResponse:
    """409 on any route, as the routers that map it themselves answer: incl. the gateway's ProjectPaused
    (app/pause.py) on a path that neither checked the pause nor maps the refusal, instead of a 500."""
    return JSONResponse(status_code=409, content={"detail": str(exc)})


app.include_router(users.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(manager.router)
app.include_router(settings.router)
app.include_router(agents.router)
app.include_router(feedback.router)
app.include_router(inbox.router)
app.include_router(blueprints.router)
app.include_router(interview.router)
app.include_router(benchmarks.router)


@app.get("/health")
def health():
    return {"status": "ok"}


# The app shell (dashboard, projects, agents, ... — see web/index.html) is a static
# single-page UI; it calls the API above from the browser once each section is wired up.
app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="web")
