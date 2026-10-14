"""Executes one task: one prompt against one model.

Order of gates before a provider is called (cheapest and most global first):

1. circuit breaker      - is the provider believed to be healthy?
2. concurrency cap      - are fewer than N calls in flight across all workers?
3. token bucket         - is there request *and* token budget this minute?
4. claim                - atomically move the task to ``running`` (idempotency)

A gate that says "not now" returns ``Deferred(delay)``; the worker re-schedules
the job for later rather than sleeping, so throttled tasks never hold a worker
slot or a database connection. The DB session is also released for the duration
of the provider call - a 30 s LLM call must not pin a pooled connection.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import random
import uuid
from dataclasses import dataclass
from typing import Literal

import structlog
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from avt.analysis.pipeline import ANALYSIS_VERSION
from avt.config import Settings
from avt.db.base import Database
from avt.db.models import Prompt, Response, Run, RunTask
from avt.domain import ModelKey, TaskStatus
from avt.logging import get_logger
from avt.metrics import (
    BREAKER_TRIPS,
    COST_USD,
    PROVIDER_LATENCY,
    PROVIDER_REQUESTS,
    TASKS,
    THROTTLED,
    TOKENS,
)
from avt.providers.base import (
    GenerationRequest,
    GenerationResult,
    ProviderError,
    RateLimitedError,
    TransientProviderError,
)
from avt.providers.pricing import estimate_cost
from avt.providers.registry import ProviderRegistry
from avt.resilience.backoff import RetryBudget, backoff_delay
from avt.resilience.circuit_breaker import CircuitBreaker
from avt.resilience.concurrency import ConcurrencyLimiter
from avt.resilience.token_bucket import TokenBucketLimiter
from avt.services.aggregates import rebuild_daily_stats
from avt.services.brand_cache import BrandCache
from avt.services.runs import EnqueueFn, maybe_finalize_run

log = get_logger(__name__)

STALE_RUNNING_AFTER = dt.timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: Literal["succeeded", "deferred", "dead", "skipped"]
    delay: float = 0.0
    response_id: int | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class _Loaded:
    task: RunTask
    prompt: str
    run_total: int
    day: dt.date


async def finalize_if_done(db: Database, run_id: uuid.UUID, workspace_id: uuid.UUID) -> None:
    """Shared tail of every terminal transition: close the run, refresh rollups."""
    async with db.session() as session:
        finalized = await maybe_finalize_run(session, run_id, ANALYSIS_VERSION)
        if finalized:
            _, days = finalized
            await rebuild_daily_stats(session, workspace_id, days, ANALYSIS_VERSION)


class TaskExecutor:
    def __init__(
        self,
        *,
        db: Database,
        settings: Settings,
        registry: ProviderRegistry,
        limiter: TokenBucketLimiter,
        concurrency: ConcurrencyLimiter,
        breaker: CircuitBreaker,
        budget: RetryBudget,
        brands: BrandCache,
        enqueue: EnqueueFn,
        rng: random.Random | None = None,
    ) -> None:
        self.db = db
        self.settings = settings
        self.registry = registry
        self.limiter = limiter
        self.concurrency = concurrency
        self.breaker = breaker
        self.budget = budget
        self.brands = brands
        self.enqueue = enqueue
        self.rng = rng or random.Random()

    def _jitter(self, delay: float) -> float:
        return delay + self.rng.uniform(0.05, 0.25 + delay * 0.25)

    async def _load(self, task_id: int) -> _Loaded | None:
        async with self.db.session() as session:
            row = (
                await session.execute(
                    select(RunTask, Prompt.text, Run.total_tasks, Run.scheduled_for, Run.created_at)
                    .join(Prompt, Prompt.id == RunTask.prompt_id)
                    .join(Run, Run.id == RunTask.run_id)
                    .where(RunTask.id == task_id)
                )
            ).one_or_none()
        if row is None:
            return None
        task, prompt, total, scheduled_for, created_at = row
        return _Loaded(task, prompt, int(total), scheduled_for or created_at.date())

    async def _claim(self, task_id: int) -> int | None:
        """Atomically take ownership. Returns the new attempt number, or None."""
        async with self.db.session() as session:
            attempt = (
                await session.execute(
                    update(RunTask)
                    .where(
                        RunTask.id == task_id,
                        or_(
                            RunTask.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED]),
                            # A worker died mid-call: the lease went stale, take over.
                            (RunTask.status == TaskStatus.RUNNING)
                            & (RunTask.updated_at < func.now() - STALE_RUNNING_AFTER),
                        ),
                    )
                    .values(status=TaskStatus.RUNNING, attempts=RunTask.attempts + 1)
                    .returning(RunTask.attempts)
                )
            ).scalar_one_or_none()
            await session.commit()
        return attempt

    async def execute(self, task_id: int) -> Outcome:
        loaded = await self._load(task_id)
        if loaded is None or TaskStatus(loaded.task.status).is_terminal:
            return Outcome("skipped", reason="missing or terminal")
        task = loaded.task
        provider_name = task.provider
        structlog.contextvars.bind_contextvars(run_id=str(task.run_id), provider=provider_name)
        limits = self.settings.limits_for(provider_name)

        decision = await self.breaker.allow(provider_name)
        if not decision.allowed:
            THROTTLED.labels(provider_name, "circuit_open").inc()
            return Outcome("deferred", self._jitter(decision.retry_after_seconds), reason="circuit_open")

        lease = await self.concurrency.try_acquire(provider_name, limits.max_concurrency)
        if lease is None:
            THROTTLED.labels(provider_name, "concurrency").inc()
            return Outcome("deferred", self._jitter(0.2), reason="concurrency")
        try:
            estimate = len(loaded.prompt) // 4 + self.settings.max_output_tokens
            admission = await self.limiter.acquire(
                provider_name,
                requests_per_minute=limits.requests_per_minute,
                tokens_per_minute=limits.tokens_per_minute,
                estimated_tokens=estimate,
            )
            if not admission.admitted:
                THROTTLED.labels(provider_name, "rate_limit").inc()
                return Outcome("deferred", self._jitter(admission.retry_after_seconds), reason="rate_limit")

            attempt = await self._claim(task_id)
            if attempt is None:
                await self.limiter.reconcile(provider_name, estimated_tokens=estimate, actual_tokens=0)
                return Outcome("skipped", reason="claimed elsewhere")
            return await self._call(loaded, attempt, estimate)
        finally:
            await self.concurrency.release(provider_name, lease)

    async def _call(self, loaded: _Loaded, attempt: int, estimate: int) -> Outcome:
        task = loaded.task
        key = ModelKey.parse(task.model_key)
        provider = self.registry.get(key.provider)
        simulation: tuple[str, ...] = ()
        if self.registry.is_fake(key.provider):
            async with self.db.session() as session:
                simulation = (await self.brands.get(session, task.workspace_id)).names
        request = GenerationRequest(
            prompt=loaded.prompt,
            model=key.model,
            max_output_tokens=self.settings.max_output_tokens,
            day=loaded.day,
            attempt=attempt,
            simulation_brands=simulation,
        )
        try:
            result = await asyncio.wait_for(
                provider.generate(request), timeout=self.settings.provider_timeout_seconds + 5
            )
        except ProviderError as exc:
            return await self._on_error(loaded, attempt, estimate, exc)
        except TimeoutError:
            return await self._on_error(loaded, attempt, estimate, TransientProviderError("timeout"))
        except Exception as exc:  # noqa: BLE001 - unknown errors are treated as transient
            return await self._on_error(loaded, attempt, estimate, TransientProviderError(repr(exc)))
        return await self._on_success(loaded, key, result, estimate)

    async def _on_success(
        self, loaded: _Loaded, key: ModelKey, result: GenerationResult, estimate: int
    ) -> Outcome:
        task = loaded.task
        cost = estimate_cost(key.model, result.input_tokens, result.output_tokens)
        async with self.db.session() as session:
            response_id = (
                await session.execute(
                    insert(Response)
                    .values(
                        task_id=task.id,
                        run_id=task.run_id,
                        workspace_id=task.workspace_id,
                        prompt_id=task.prompt_id,
                        model_key=task.model_key,
                        provider=key.provider,
                        model_name=result.model_name,
                        text=result.text,
                        provider_citations=result.citations,
                        input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens,
                        cost_usd=cost,
                        latency_ms=result.latency_ms,
                        day=loaded.day,
                    )
                    # Second line of idempotency: one response per task, ever.
                    .on_conflict_do_nothing(index_elements=["task_id"])
                    .returning(Response.id)
                )
            ).scalar_one_or_none()
            if response_id is None:
                response_id = (
                    await session.execute(select(Response.id).where(Response.task_id == task.id))
                ).scalar_one()
            await session.execute(
                update(RunTask)
                .where(RunTask.id == task.id)
                .values(status=TaskStatus.SUCCEEDED, last_error=None, finished_at=dt.datetime.now(dt.UTC))
            )
            await session.commit()

        await self.breaker.record_success(key.provider)
        await self.limiter.reconcile(
            key.provider,
            estimated_tokens=estimate,
            actual_tokens=result.input_tokens + result.output_tokens,
        )
        PROVIDER_REQUESTS.labels(key.provider, "ok").inc()
        PROVIDER_LATENCY.labels(key.provider).observe(result.latency_ms / 1000)
        TOKENS.labels(key.provider, "input").inc(result.input_tokens)
        TOKENS.labels(key.provider, "output").inc(result.output_tokens)
        COST_USD.labels(key.provider).inc(float(cost))
        TASKS.labels(TaskStatus.SUCCEEDED.value).inc()
        await self.enqueue("analyze_response", response_id)
        return Outcome("succeeded", response_id=response_id)

    async def _on_error(self, loaded: _Loaded, attempt: int, estimate: int, exc: ProviderError) -> Outcome:
        task = loaded.task
        provider = task.provider
        await self.limiter.reconcile(provider, estimated_tokens=estimate, actual_tokens=0)
        outcome_label = (
            "rate_limited"
            if isinstance(exc, RateLimitedError)
            else "transient"
            if exc.retryable
            else "permanent"
        )
        PROVIDER_REQUESTS.labels(provider, outcome_label).inc()
        if exc.retryable and await self.breaker.record_failure(provider):
            BREAKER_TRIPS.labels(provider).inc()
            log.warning("breaker.opened", provider=provider)

        error = f"{type(exc).__name__}: {exc}"[:1000]
        can_retry = (
            exc.retryable
            and attempt < self.settings.max_task_attempts
            and await self.budget.try_spend(str(task.run_id), loaded.run_total)
        )
        async with self.db.session() as session:
            if can_retry:
                await session.execute(
                    update(RunTask)
                    .where(RunTask.id == task.id)
                    .values(status=TaskStatus.QUEUED, last_error=error)
                )
                await session.commit()
                delay = backoff_delay(
                    attempt,
                    base=self.settings.backoff_base_seconds,
                    cap=self.settings.backoff_max_seconds,
                    retry_after=exc.retry_after,
                    rng=self.rng,
                )
                log.info("task.retry", task_id=task.id, attempt=attempt, delay=round(delay, 2), error=error)
                TASKS.labels("retried").inc()
                return Outcome("deferred", delay, reason=outcome_label)

            await session.execute(
                update(RunTask)
                .where(RunTask.id == task.id)
                .values(status=TaskStatus.DEAD, last_error=error, finished_at=dt.datetime.now(dt.UTC))
            )
            await session.commit()
        log.warning("task.dead_lettered", task_id=task.id, attempt=attempt, error=error)
        TASKS.labels(TaskStatus.DEAD.value).inc()
        await finalize_if_done(self.db, task.run_id, task.workspace_id)
        return Outcome("dead", reason=outcome_label)
