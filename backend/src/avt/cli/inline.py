"""Run tasks in-process, without the queue - used by the seed script for backfills.

It drives the *same* ``TaskExecutor`` and ``AnalysisService`` the workers use, so
backfilled history goes through identical gating, idempotency and analysis code;
only the transport (arq) is replaced by a bounded ``asyncio.gather``.
"""

from __future__ import annotations

import asyncio
import uuid

from redis.asyncio import Redis
from sqlalchemy import select

from avt.config import ProviderLimits, Settings
from avt.db.base import Database
from avt.db.models import RunTask
from avt.providers.registry import KNOWN_PROVIDERS, ProviderRegistry
from avt.resilience.backoff import RetryBudget
from avt.resilience.circuit_breaker import CircuitBreaker
from avt.resilience.concurrency import ConcurrencyLimiter
from avt.resilience.token_bucket import TokenBucketLimiter
from avt.services.analyzer import AnalysisService, build_sentiment_analyzer
from avt.services.brand_cache import BrandCache
from avt.services.executor import TaskExecutor


class InlineRunner:
    def __init__(self, settings: Settings, db: Database, redis: Redis, *, concurrency: int = 64) -> None:
        # Backfills are offline and fake-provider only: lift the per-provider limits.
        for provider in KNOWN_PROVIDERS:
            settings.override_limits(
                provider,
                ProviderLimits(
                    requests_per_minute=1_000_000, tokens_per_minute=10**10, max_concurrency=concurrency
                ),
            )
        self._gate = asyncio.Semaphore(concurrency)
        self._responses: list[int] = []
        brands = BrandCache()
        self.registry = ProviderRegistry(settings)
        self.analysis = AnalysisService(db, brands, build_sentiment_analyzer(settings))
        self.db = db

        async def collect(name: str, arg: int | str) -> None:
            if name == "analyze_response":
                self._responses.append(int(arg))

        self.executor = TaskExecutor(
            db=db,
            settings=settings,
            registry=self.registry,
            limiter=TokenBucketLimiter(redis, prefix="avt:seed:tb"),
            concurrency=ConcurrencyLimiter(redis, prefix="avt:seed:sem"),
            breaker=CircuitBreaker(
                redis, failure_threshold=10_000, window_seconds=1, cooldown_seconds=1, prefix="avt:seed:cb"
            ),
            budget=RetryBudget(redis, ratio=1.0, floor=1000, prefix="avt:seed:rb"),
            brands=brands,
            enqueue=collect,
        )

    async def _one(self, task_id: int) -> None:
        async with self._gate:
            for _ in range(50):
                outcome = await self.executor.execute(task_id)
                if outcome.kind != "deferred":
                    return
                await asyncio.sleep(min(outcome.delay, 0.5))

    async def run(self, run_id: uuid.UUID) -> None:
        async with self.db.session() as session:
            ids = list((await session.execute(select(RunTask.id).where(RunTask.run_id == run_id))).scalars())
        await asyncio.gather(*(self._one(t) for t in ids))
        responses, self._responses = self._responses, []
        await asyncio.gather(*(self._analyze(r) for r in responses))

    async def _analyze(self, response_id: int) -> None:
        async with self._gate:
            await self.analysis.analyze(response_id)

    async def aclose(self) -> None:
        await self.registry.aclose()
