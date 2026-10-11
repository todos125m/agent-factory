from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import benchmark
from app.auth import require_admin
from app.db import get_session
from app.errors import not_found
from app.gateway.service import Gateway
from app.models import BenchmarkRun, User
from app.routers.manager import get_gateway
from app.schemas import BenchmarkRunOut

# Benchmarks spend the server's model access on routes anyone could pick: the admin's alone.
router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


class BenchmarkRequest(BaseModel):
    routes: list[benchmark.RouteIn] = Field(min_length=1, max_length=5)


class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)


@router.post("/run", response_model=list[BenchmarkRunOut])
def run_benchmark(
    body: BenchmarkRequest, session: Session = Depends(get_session), me: User = Depends(require_admin), gateway: Gateway = Depends(get_gateway)
):
    return benchmark.run_benchmark(session, gateway, body.routes)


@router.get("", response_model=list[BenchmarkRunOut])
def list_benchmark_runs(session: Session = Depends(get_session), me: User = Depends(require_admin)):
    return session.scalars(select(BenchmarkRun).order_by(BenchmarkRun.id.desc())).all()


@router.post("/{run_id}/rate", response_model=BenchmarkRunOut)
def rate_benchmark_run(run_id: int, body: RatingIn, session: Session = Depends(get_session), me: User = Depends(require_admin)):
    run = session.get(BenchmarkRun, run_id)
    if run is None:
        raise not_found("benchmark_run", "Benchmark run not found")
    return benchmark.rate(session, run, body.rating)
