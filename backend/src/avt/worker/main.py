"""arq worker: ``arq avt.worker.main.WorkerSettings``.

Jobs are thin adapters around the services; the only queue-specific logic is
translating ``Outcome("deferred")`` into ``arq.Retry`` so throttled or retried
tasks are re-scheduled instead of sleeping inside a worker slot.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import structlog
from arq import Retry, func
from arq.connections import ArqRedis, RedisSettings
from prometheus_client import start_http_server

from avt.config import get_settings
from avt.db.base import Database
from avt.logging import configure_logging, get_logger
from avt.providers.registry import ProviderRegistry
from avt.resilience.backoff import RetryBudget
from avt.resilience.circuit_breaker import CircuitBreaker
from avt.resilience.concurrency import ConcurrencyLimiter
from avt.resilience.token_bucket import TokenBucketLimiter
from avt.services.analyzer import AnalysisService, build_sentiment_analyzer
from avt.services.brand_cache import BrandCache
from avt.services.executor import TaskExecutor
from avt.services.runs import EnqueueFn
from avt.services.runs import dispatch_run as dispatch_run_service

log = get_logger(__name__)

Ctx = dict[str, Any]


def job_id(name: str, arg: object) -> str:
    """Deterministic job ids make enqueueing idempotent: arq refuses a second job
    with the same id while the first is queued or running."""
    return f"{name}:{arg}"


def make_enqueue(pool: ArqRedis) -> EnqueueFn:
    async def enqueue(name: str, arg: int | str) -> None:
        await pool.enqueue_job(name, arg, _job_id=job_id(name, arg))

    return enqueue


async def startup(ctx: Ctx) -> None:
    settings = ctx.get("settings") or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    pool: ArqRedis = ctx["redis"]
    db = Database(settings)
    brands = BrandCache()
    registry = ProviderRegistry(settings)
    enqueue = make_enqueue(pool)
    ctx.update(
        settings=settings,
        db=db,
        registry=registry,
        enqueue=enqueue,
        executor=TaskExecutor(
            db=db,
            settings=settings,
            registry=registry,
            limiter=TokenBucketLimiter(pool),
            concurrency=ConcurrencyLimiter(pool),
            breaker=CircuitBreaker(
                pool,
                failure_threshold=settings.breaker_failure_threshold,
                window_seconds=settings.breaker_window_seconds,
                cooldown_seconds=settings.breaker_cooldown_seconds,
            ),
            budget=RetryBudget(pool, ratio=settings.retry_budget_ratio, floor=settings.retry_budget_floor),
            brands=brands,
            enqueue=enqueue,
        ),
        analysis=AnalysisService(db, brands, build_sentiment_analyzer(settings)),
    )
    if settings.worker_metrics_port:
        try:
            start_http_server(settings.worker_metrics_port)
        except OSError:  # another worker process on this host already serves metrics
            log.info("metrics.port_in_use", port=settings.worker_metrics_port)
    log.info("worker.started", max_jobs=settings.worker_max_jobs)


async def shutdown(ctx: Ctx) -> None:
    await ctx["registry"].aclose()
    await ctx["db"].dispose()


async def execute_task(ctx: Ctx, task_id: int) -> str:
    with structlog.contextvars.bound_contextvars(task_id=task_id, job_try=ctx.get("job_try")):
        outcome = await ctx["executor"].execute(task_id)
        if outcome.kind == "deferred":
            raise Retry(defer=outcome.delay)
        return str(outcome.kind)


async def analyze_response(ctx: Ctx, response_id: int) -> int | None:
    with structlog.contextvars.bound_contextvars(response_id=response_id):
        try:
            result: int | None = await ctx["analysis"].analyze(response_id)
        except Exception as exc:
            job_try = int(ctx.get("job_try", 1))
            if job_try < 5:
                log.warning("analysis.retry", error=repr(exc), job_try=job_try)
                raise Retry(defer=2**job_try) from exc
            raise
        return result


async def dispatch_run(ctx: Ctx, run_id: str) -> int:
    with structlog.contextvars.bound_contextvars(run_id=run_id):
        async with ctx["db"].session() as session:
            count: int = await dispatch_run_service(
                session,
                uuid.UUID(run_id),
                ctx["enqueue"],
                batch_size=ctx["settings"].dispatch_batch_size,
            )
        return count


async def reanalyze(ctx: Ctx, workspace_id: str, date_from: str, date_to: str) -> int:
    with structlog.contextvars.bound_contextvars(workspace_id=workspace_id):
        done: int = await ctx["analysis"].reanalyze_range(
            uuid.UUID(workspace_id), dt.date.fromisoformat(date_from), dt.date.fromisoformat(date_to)
        )
        return done


_settings = get_settings()


class WorkerSettings:
    functions = [
        # Attempts are counted in Postgres (run_tasks.attempts); arq's own try counter
        # would also count throttling deferrals, so it is set out of the way.
        func(execute_task, keep_result=0, max_tries=100_000),
        func(analyze_response, keep_result=0, max_tries=10),
        func(dispatch_run, keep_result=0, max_tries=5, timeout=1800),
        func(reanalyze, keep_result=0, max_tries=1, timeout=6 * 3600),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_max_jobs
    job_timeout = 300
    keep_result = 0
    health_check_interval = 30
