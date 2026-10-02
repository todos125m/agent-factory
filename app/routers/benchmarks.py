from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import benchmark
from app.db import get_session
from app.gateway.service import Gateway
from app.models import BenchmarkRun
from app.routers.manager import get_gateway
from app.schemas import BenchmarkRunOut

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


class BenchmarkRequest(BaseModel):
    routes: list[benchmark.RouteIn] = Field(min_length=1, max_length=5)


class RatingIn(BaseModel):
    rating: int = Field(ge=1, le=5)


@router.post("/run", response_model=list[BenchmarkRunOut])
def run_benchmark(
    body: BenchmarkRequest, session: Session = Depends(get_session), gateway: Gateway = Depends(get_gateway)
):
    return benchmark.run_benchmark(session, gateway, body.routes)


@router.get("", response_model=list[BenchmarkRunOut])
def list_benchmark_runs(session: Session = Depends(get_session)):
    return session.scalars(select(BenchmarkRun).order_by(BenchmarkRun.id.desc())).all()


@router.post("/{run_id}/rate", response_model=BenchmarkRunOut)
def rate_benchmark_run(run_id: int, body: RatingIn, session: Session = Depends(get_session)):
    run = session.get(BenchmarkRun, run_id)
    if run is None:
        raise HTTPException(404, "Benchmark run not found")
    return benchmark.rate(session, run, body.rating)
