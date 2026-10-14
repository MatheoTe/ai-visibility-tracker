"""Daily scheduler: ``python -m avt.scheduler.main``.

Every tick it creates today's scheduled run for each workspace whose run hour has
passed. Double-firing is prevented by the database, not by the process: the
partial unique index ``(workspace_id, scheduled_for) WHERE trigger='scheduled'``
means two scheduler replicas (or a restart mid-tick) can race freely and exactly
one run per workspace per day is created.

The same tick runs a sweeper that re-dispatches work that was committed to
Postgres but never made it onto the queue (crash between commit and enqueue, or a
Redis flush). Dispatch is idempotent, so the sweeper can be aggressive.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import signal
import uuid

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import and_, exists, func, select, update

from avt.config import Settings, get_settings
from avt.db.base import Database
from avt.db.models import Run, RunTask, Workspace
from avt.domain import RunStatus, RunTrigger, TaskStatus
from avt.logging import configure_logging, get_logger
from avt.services.executor import finalize_if_done
from avt.services.runs import EnqueueFn, create_run
from avt.worker.main import make_enqueue

log = get_logger(__name__)

SWEEP_PENDING_AFTER = dt.timedelta(minutes=2)
SWEEP_QUEUED_AFTER = dt.timedelta(minutes=30)


async def schedule_due_runs(db: Database, enqueue: EnqueueFn, now: dt.datetime) -> list[uuid.UUID]:
    today = now.date()
    created: list[uuid.UUID] = []
    async with db.session() as session:
        already = exists().where(
            and_(
                Run.workspace_id == Workspace.id,
                Run.trigger == RunTrigger.SCHEDULED,
                Run.scheduled_for == today,
            )
        )
        due = list(
            (
                await session.execute(
                    select(Workspace.id).where(
                        Workspace.schedule_enabled,
                        Workspace.daily_run_hour_utc <= now.hour,
                        ~already,
                    )
                )
            ).scalars()
        )
    # One short transaction per workspace: a conflict (another replica won) only
    # rolls back that workspace's attempt.
    for workspace_id in due:
        async with db.session() as session:
            workspace = await session.get(Workspace, workspace_id)
            if workspace is None:
                continue
            run = await create_run(session, workspace, trigger=RunTrigger.SCHEDULED, scheduled_for=today)
        if run is None:
            log.info("scheduler.already_created", workspace_id=str(workspace_id))
            continue
        created.append(run.id)
        if run.total_tasks:
            await enqueue("dispatch_run", str(run.id))
    return created


async def sweep(db: Database, enqueue: EnqueueFn) -> int:
    async with db.session() as session:
        stale_queued = (
            await session.execute(
                update(RunTask)
                .where(
                    RunTask.status == TaskStatus.QUEUED,
                    RunTask.updated_at < func.now() - SWEEP_QUEUED_AFTER,
                )
                .values(status=TaskStatus.PENDING)
                .returning(RunTask.run_id)
            )
        ).scalars()
        run_ids = set(stale_queued)
        await session.commit()
        run_ids |= set(
            (
                await session.execute(
                    select(Run.id).where(
                        Run.status == RunStatus.PENDING,
                        Run.total_tasks > 0,
                        Run.created_at < func.now() - SWEEP_PENDING_AFTER,
                    )
                )
            ).scalars()
        )
    for run_id in run_ids:
        await enqueue("dispatch_run", str(run_id))

    # Safety net for finalisation: a run whose last finalize job was lost (worker
    # killed mid-job) is closed here within one tick.
    async with db.session() as session:
        open_runs = (
            await session.execute(
                select(Run.id, Run.workspace_id).where(
                    Run.status == RunStatus.RUNNING,
                    Run.started_at < func.now() - SWEEP_PENDING_AFTER,
                )
            )
        ).all()
    for run_id, workspace_id in open_runs:
        await finalize_if_done(db, run_id, workspace_id)
    if run_ids:
        log.info("scheduler.swept", runs=len(run_ids))
    return len(run_ids)


async def main(settings: Settings) -> None:
    configure_logging(settings.log_level, settings.log_json)
    db = Database(settings)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    enqueue = make_enqueue(pool)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("scheduler.started", tick_seconds=settings.scheduler_tick_seconds)
    while not stop.is_set():
        try:
            now = dt.datetime.now(dt.UTC)
            created = await schedule_due_runs(db, enqueue, now)
            if created:
                log.info("scheduler.runs_created", count=len(created))
            await sweep(db, enqueue)
        except Exception:
            log.exception("scheduler.tick_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.scheduler_tick_seconds)
    await pool.aclose()
    await db.dispose()


def run() -> None:
    asyncio.run(main(get_settings()))


if __name__ == "__main__":
    run()
