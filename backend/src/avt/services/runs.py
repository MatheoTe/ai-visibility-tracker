"""Run lifecycle: create -> fan out -> dispatch -> finalize (and retry-failed).

A run is the cross product of a workspace's active prompts and its models. Tasks
are materialised in a single ``INSERT ... SELECT`` (thousands of rows, one round
trip) guarded by ``UNIQUE(run_id, prompt_id, model_key)``, then enqueued in
batches. Every step is idempotent so the scheduler's sweeper can safely replay it.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from avt.db.models import Analysis, Response, Run, RunTask, Workspace
from avt.domain import ModelKey, RunStatus, RunTrigger, TaskStatus
from avt.logging import get_logger
from avt.metrics import RUNS_FINALIZED

log = get_logger(__name__)

EnqueueFn = Callable[[str, int | str], Awaitable[None]]
"""``enqueue(job_name, task_id)`` - abstracts the queue so services stay testable."""


@dataclass(frozen=True, slots=True)
class RunProgress:
    total: int
    counts: Mapping[str, int]
    analyzed: int

    @property
    def done(self) -> int:
        return self.counts.get(TaskStatus.SUCCEEDED, 0) + self.counts.get(TaskStatus.DEAD, 0)

    @property
    def succeeded(self) -> int:
        return self.counts.get(TaskStatus.SUCCEEDED, 0)

    @property
    def dead(self) -> int:
        return self.counts.get(TaskStatus.DEAD, 0)

    @property
    def percent(self) -> float:
        return round(100 * self.done / self.total, 1) if self.total else 100.0


def derive_run_status(progress: RunProgress) -> RunStatus:
    """Aggregate task states into a run status.

    pending   - nothing has started
    running   - work outstanding, or responses still waiting for analysis
    completed - every task succeeded and was analysed
    partial   - finished, but some tasks were dead-lettered
    failed    - finished and nothing succeeded
    """
    if progress.total == 0:
        return RunStatus.COMPLETED
    if progress.done < progress.total or progress.analyzed < progress.succeeded:
        started = progress.total - progress.counts.get(TaskStatus.PENDING, 0)
        return RunStatus.RUNNING if started or progress.done else RunStatus.PENDING
    if progress.dead == 0:
        return RunStatus.COMPLETED
    if progress.succeeded == 0:
        return RunStatus.FAILED
    return RunStatus.PARTIAL


# --------------------------------------------------------------------------- create

_FAN_OUT = text(
    """
    INSERT INTO run_tasks (run_id, workspace_id, prompt_id, model_key, provider, status, attempts)
    SELECT :run_id, :workspace_id, p.id, m.model_key, m.provider, 'pending', 0
    FROM prompts p
    CROSS JOIN unnest(CAST(:model_keys AS text[]), CAST(:providers AS text[])) AS m(model_key, provider)
    WHERE p.workspace_id = :workspace_id AND p.is_active
    ON CONFLICT ON CONSTRAINT uq_run_tasks_idempotency DO NOTHING
    """
)


async def create_run(
    session: AsyncSession,
    workspace: Workspace,
    *,
    trigger: RunTrigger,
    scheduled_for: dt.date | None = None,
    models: list[str] | None = None,
) -> Run | None:
    """Create a run and its tasks in one transaction.

    Returns ``None`` when a scheduled run for that day already exists - the partial
    unique index makes the scheduler safe to run on several replicas at once.
    """
    keys = [ModelKey.parse(k) for k in (models or workspace.models)]
    stmt = insert(Run).values(
        workspace_id=workspace.id,
        trigger=trigger,
        status=RunStatus.PENDING,
        scheduled_for=scheduled_for,
        models=[str(k) for k in keys],
    )
    if trigger is RunTrigger.SCHEDULED:
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["workspace_id", "scheduled_for"],
            index_where=text("trigger = 'scheduled'"),
        )
    run_id = (await session.execute(stmt.returning(Run.id))).scalar_one_or_none()
    if run_id is None:
        await session.rollback()
        return None

    result = await session.execute(
        _FAN_OUT,
        {
            "run_id": run_id,
            "workspace_id": workspace.id,
            "model_keys": [str(k) for k in keys],
            "providers": [k.provider for k in keys],
        },
    )
    total = int(getattr(result, "rowcount", 0) or 0)
    values: dict[str, object] = {"total_tasks": total}
    if total == 0:
        values |= {"status": RunStatus.COMPLETED, "finished_at": func.now()}
    await session.execute(update(Run).where(Run.id == run_id).values(**values))
    await session.commit()
    run = await session.get(Run, run_id)
    assert run is not None
    log.info("run.created", run_id=str(run_id), trigger=str(trigger), tasks=total)
    return run


# --------------------------------------------------------------------------- dispatch


async def dispatch_run(
    session: AsyncSession, run_id: uuid.UUID, enqueue: EnqueueFn, *, batch_size: int = 500
) -> int:
    """Enqueue every pending task of a run in keyset-paginated batches.

    Safe to call repeatedly: only ``pending`` rows are picked up, and the queue
    de-duplicates on the per-task job id.
    """
    await session.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == RunStatus.PENDING)
        .values(status=RunStatus.RUNNING, started_at=func.now())
    )
    await session.commit()

    dispatched, last_id = 0, 0
    while True:
        ids = list(
            (
                await session.execute(
                    select(RunTask.id)
                    .where(
                        RunTask.run_id == run_id,
                        RunTask.status == TaskStatus.PENDING,
                        RunTask.id > last_id,
                    )
                    .order_by(RunTask.id)
                    .limit(batch_size)
                )
            ).scalars()
        )
        if not ids:
            break
        await session.execute(
            update(RunTask)
            .where(RunTask.id.in_(ids), RunTask.status == TaskStatus.PENDING)
            .values(status=TaskStatus.QUEUED)
        )
        await session.commit()
        await asyncio.gather(*(enqueue("execute_task", task_id) for task_id in ids))
        dispatched += len(ids)
        last_id = ids[-1]
    log.info("run.dispatched", run_id=str(run_id), tasks=dispatched)
    return dispatched


async def retry_failed(session: AsyncSession, run_id: uuid.UUID) -> int:
    """Move dead-lettered tasks back to pending and re-open the run."""
    result = await session.execute(
        update(RunTask)
        .where(RunTask.run_id == run_id, RunTask.status == TaskStatus.DEAD)
        .values(status=TaskStatus.PENDING, attempts=0, last_error=None, finished_at=None)
    )
    count = int(getattr(result, "rowcount", 0) or 0)
    if count:
        await session.execute(
            update(Run).where(Run.id == run_id).values(status=RunStatus.PENDING, finished_at=None)
        )
    await session.commit()
    log.info("run.retry_failed", run_id=str(run_id), tasks=count)
    return count


# --------------------------------------------------------------------------- progress


async def run_progress(session: AsyncSession, run_id: uuid.UUID, analysis_version: int) -> RunProgress:
    run_total = (await session.execute(select(Run.total_tasks).where(Run.id == run_id))).scalar_one()
    rows = await session.execute(
        select(RunTask.status, func.count()).where(RunTask.run_id == run_id).group_by(RunTask.status)
    )
    counts = {status: int(n) for status, n in rows.all()}
    analyzed = (
        await session.execute(
            select(func.count())
            .select_from(Analysis)
            .join(Response, Response.id == Analysis.response_id)
            .where(Response.run_id == run_id, Analysis.version == analysis_version)
        )
    ).scalar_one()
    return RunProgress(total=int(run_total), counts=counts, analyzed=int(analyzed))


async def maybe_finalize_run(
    session: AsyncSession, run_id: uuid.UUID, analysis_version: int
) -> tuple[RunStatus, list[dt.date]] | None:
    """Close the run if all work is done. Returns (status, days touched) when it did.

    Called after every task reaches a terminal state and after every analysis, so
    the common case must be cheap: one indexed COUNT, no locks. Only when nothing
    is outstanding do we take the row lock that serialises the actual transition.
    """
    outstanding = (
        await session.execute(
            select(func.count())
            .select_from(RunTask)
            .where(
                RunTask.run_id == run_id,
                RunTask.status.notin_([TaskStatus.SUCCEEDED, TaskStatus.DEAD]),
            )
        )
    ).scalar_one()
    if outstanding:
        return None

    run = (await session.execute(select(Run).where(Run.id == run_id).with_for_update())).scalar_one()
    if RunStatus(run.status).is_terminal:
        await session.rollback()
        return None
    progress = await run_progress(session, run_id, analysis_version)
    status = derive_run_status(progress)
    if not status.is_terminal:
        await session.rollback()
        return None

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(Response.input_tokens), 0),
                func.coalesce(func.sum(Response.output_tokens), 0),
                func.coalesce(func.sum(Response.cost_usd), Decimal(0)),
            ).where(Response.run_id == run_id)
        )
    ).one()
    days = list(
        (await session.execute(select(Response.day).where(Response.run_id == run_id).distinct())).scalars()
    )
    run.status = status
    run.input_tokens, run.output_tokens, run.cost_usd = int(totals[0]), int(totals[1]), Decimal(totals[2])
    run.finished_at = dt.datetime.now(dt.UTC)
    await session.commit()
    RUNS_FINALIZED.labels(status=status.value).inc()
    log.info("run.finalized", run_id=str(run_id), status=status.value, cost_usd=str(run.cost_usd))
    return status, days
