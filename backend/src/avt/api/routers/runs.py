from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from avt.analysis.pipeline import ANALYSIS_VERSION
from avt.api.deps import EnqueueDep, SessionDep, WorkspaceDep
from avt.db.models import Prompt, Response, Run, RunTask
from avt.domain import RunTrigger, TaskStatus
from avt.schemas.api import (
    CursorPage,
    Page,
    ProviderCost,
    ReanalyzeIn,
    RetryFailedOut,
    RunCreate,
    RunDetailOut,
    RunOut,
    TaskOut,
)
from avt.services.runs import create_run, retry_failed, run_progress
from avt.worker.main import ANALYSIS_QUEUE

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["runs"])


@router.get("/runs")
async def list_runs(
    workspace: WorkspaceDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[RunOut]:
    where = Run.workspace_id == workspace.id
    total = (await session.execute(select(func.count()).select_from(Run).where(where))).scalar_one()
    runs = (
        await session.execute(
            select(Run).where(where).order_by(Run.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars()
    return Page(items=[RunOut.model_validate(r) for r in runs], total=total, limit=limit, offset=offset)


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def trigger_run(
    workspace: WorkspaceDep, session: SessionDep, enqueue: EnqueueDep, body: RunCreate | None = None
) -> RunOut:
    """Start a manual run over all active prompts x the workspace's models (or the
    subset given). Returns immediately; poll ``GET /runs/{id}`` for progress."""
    active = (
        await session.execute(
            select(func.count())
            .select_from(Prompt)
            .where(Prompt.workspace_id == workspace.id, Prompt.is_active)
        )
    ).scalar_one()
    if not active:
        raise HTTPException(status.HTTP_409_CONFLICT, "add at least one active prompt first")
    run = await create_run(
        session, workspace, trigger=RunTrigger.MANUAL, models=body.models if body else None
    )
    assert run is not None  # manual runs never conflict
    await enqueue("dispatch_run", str(run.id))
    return RunOut.model_validate(run)


async def _get_run(session: SessionDep, workspace_id: uuid.UUID, run_id: uuid.UUID) -> Run:
    run = (
        await session.execute(select(Run).where(Run.id == run_id, Run.workspace_id == workspace_id))
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, workspace: WorkspaceDep, session: SessionDep) -> RunDetailOut:
    """Run status with live progress and running cost per model."""
    run = await _get_run(session, workspace.id, run_id)
    progress = await run_progress(session, run_id, ANALYSIS_VERSION)
    by_model = [
        ProviderCost(
            model_key=model_key,
            responses=int(n),
            input_tokens=int(tin or 0),
            output_tokens=int(tout or 0),
            cost_usd=Decimal(cost or 0),
        )
        for model_key, n, tin, tout, cost in (
            await session.execute(
                select(
                    Response.model_key,
                    func.count(),
                    func.sum(Response.input_tokens),
                    func.sum(Response.output_tokens),
                    func.sum(Response.cost_usd),
                )
                .where(Response.run_id == run_id)
                .group_by(Response.model_key)
                .order_by(Response.model_key)
            )
        )
    ]
    base = RunOut.model_validate(run).model_dump()
    if not run.finished_at:  # live totals while the run is in flight
        base["cost_usd"] = sum((m.cost_usd for m in by_model), Decimal(0))
        base["input_tokens"] = sum(m.input_tokens for m in by_model)
        base["output_tokens"] = sum(m.output_tokens for m in by_model)
    return RunDetailOut(
        **base,
        counts=dict(progress.counts),
        analyzed=progress.analyzed,
        percent=progress.percent,
        by_model=by_model,
    )


@router.get("/runs/{run_id}/tasks")
async def list_tasks(
    run_id: uuid.UUID,
    workspace: WorkspaceDep,
    session: SessionDep,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    cursor: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CursorPage[TaskOut]:
    """Tasks of a run (keyset on id). ``status=dead`` is the dead-letter view."""
    await _get_run(session, workspace.id, run_id)
    query = (
        select(RunTask, Prompt.text)
        .join(Prompt, Prompt.id == RunTask.prompt_id)
        .where(RunTask.run_id == run_id)
        .order_by(RunTask.id)
        .limit(limit + 1)
    )
    if status_filter:
        query = query.where(RunTask.status == status_filter)
    if cursor:
        query = query.where(RunTask.id > cursor)
    rows = (await session.execute(query)).all()
    items = [
        TaskOut(
            id=t.id,
            prompt_id=t.prompt_id,
            prompt_text=text,
            model_key=t.model_key,
            status=t.status,
            attempts=t.attempts,
            last_error=t.last_error,
            updated_at=t.updated_at,
        )
        for t, text in rows[:limit]
    ]
    return CursorPage(items=items, next_cursor=str(items[-1].id) if len(rows) > limit else None)


@router.post("/runs/{run_id}/retry-failed")
async def retry_failed_tasks(
    run_id: uuid.UUID, workspace: WorkspaceDep, session: SessionDep, enqueue: EnqueueDep, request: Request
) -> RetryFailedOut:
    """Re-queue every dead-lettered task of the run with a fresh retry budget."""
    await _get_run(session, workspace.id, run_id)
    count = await retry_failed(session, run_id)
    if count:
        await request.app.state.budget.reset(str(run_id))
        await enqueue("dispatch_run", str(run_id))
    return RetryFailedOut(requeued=count)


@router.post("/reanalyze", status_code=status.HTTP_202_ACCEPTED)
async def reanalyze(body: ReanalyzeIn, workspace: WorkspaceDep, request: Request) -> dict[str, str]:
    """Re-run the analysis stage (current ``ANALYSIS_VERSION``) over stored raw
    responses in a date range and rebuild the rollups. No provider calls."""
    if body.date_to < body.date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "date_to must be >= date_from")
    await request.app.state.pool.enqueue_job(
        "reanalyze",
        str(workspace.id),
        body.date_from.isoformat(),
        body.date_to.isoformat(),
        _queue_name=ANALYSIS_QUEUE,
    )
    return {"status": "queued", "analysis_version": str(ANALYSIS_VERSION)}
