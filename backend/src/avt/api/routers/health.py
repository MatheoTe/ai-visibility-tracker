from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text

from avt.db.base import Database
from avt.db.models import Run
from avt.domain import RunStatus
from avt.metrics import RUNS_IN_FLIGHT

router = APIRouter(tags=["ops"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness: Postgres and Redis are reachable."""
    checks: dict[str, str] = {}
    db: Database = request.app.state.db
    try:
        async with db.session() as session:
            await session.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
        checks["postgres"] = f"error: {type(exc).__name__}"
    try:
        await request.app.state.pool.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
    ok = all(v == "ok" for v in checks.values())
    response.status_code = status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ok" if ok else "degraded", **checks}


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    db: Database = request.app.state.db
    try:
        async with db.session() as session:
            in_flight = (
                await session.execute(
                    select(func.count())
                    .select_from(Run)
                    .where(Run.status.in_([RunStatus.PENDING, RunStatus.RUNNING]))
                )
            ).scalar_one()
        RUNS_IN_FLIGHT.set(in_flight)
    except Exception:  # noqa: BLE001, S110 - metrics must render even if the DB is down
        pass
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
