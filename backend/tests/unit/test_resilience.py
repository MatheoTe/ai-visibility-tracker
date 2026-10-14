from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator

import fakeredis
import pytest
from redis.asyncio import Redis

from avt.resilience.backoff import RetryBudget, backoff_delay
from avt.resilience.circuit_breaker import BreakerState, CircuitBreaker
from avt.resilience.concurrency import ConcurrencyLimiter
from avt.resilience.token_bucket import TokenBucketLimiter


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = fakeredis.FakeAsyncRedis()
    yield client
    await client.aclose()


# ----------------------------------------------------------------------- backoff


def test_backoff_grows_exponentially_and_is_capped() -> None:
    rng = random.Random(7)
    ceilings = [min(10.0, 2 ** (n - 1)) for n in range(1, 8)]
    for attempt, ceiling in enumerate(ceilings, start=1):
        for _ in range(200):
            assert 0 <= backoff_delay(attempt, base=1, cap=10, rng=rng) <= ceiling


def test_backoff_has_jitter() -> None:
    rng = random.Random(1)
    delays = {round(backoff_delay(5, base=1, cap=60, rng=rng), 6) for _ in range(50)}
    assert len(delays) > 40  # not synchronised


def test_retry_after_is_a_floor_but_still_capped() -> None:
    rng = random.Random(3)
    assert backoff_delay(1, base=1, cap=60, retry_after=12, rng=rng) >= 12
    assert backoff_delay(1, base=1, cap=5, retry_after=120, rng=rng) <= 5


def test_backoff_attempt_is_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        backoff_delay(0)


async def test_retry_budget(redis: Redis) -> None:
    budget = RetryBudget(redis, ratio=0.5, floor=2)
    assert budget.limit_for(1) == 2
    assert budget.limit_for(10) == 5
    results = [await budget.try_spend("run-1", 10) for _ in range(7)]
    assert results == [True] * 5 + [False] * 2
    await budget.reset("run-1")
    assert await budget.try_spend("run-1", 10)


# ----------------------------------------------------------------------- token bucket


async def test_token_bucket_limits_requests(redis: Redis) -> None:
    tb = TokenBucketLimiter(redis)
    kw = {"requests_per_minute": 2, "tokens_per_minute": 1_000_000, "estimated_tokens": 10}
    assert (await tb.acquire("p", **kw)).admitted
    assert (await tb.acquire("p", **kw)).admitted
    denied = await tb.acquire("p", **kw)
    assert not denied.admitted
    assert 25 <= denied.retry_after_seconds <= 30  # one request refills every 30s


async def test_token_bucket_limits_tokens_and_reconciles(redis: Redis) -> None:
    tb = TokenBucketLimiter(redis)
    kw = {"requests_per_minute": 1000, "tokens_per_minute": 1000}
    assert (await tb.acquire("p", estimated_tokens=600, **kw)).admitted
    denied = await tb.acquire("p", estimated_tokens=600, **kw)
    assert not denied.admitted
    assert 11 <= denied.retry_after_seconds <= 12.5  # ~200 tokens short at 1000/min
    # The first call actually used only 100 tokens: give 500 back.
    await tb.reconcile("p", estimated_tokens=600, actual_tokens=100)
    assert (await tb.acquire("p", estimated_tokens=600, **kw)).admitted


async def test_token_bucket_admits_oversized_call_when_full(redis: Redis) -> None:
    tb = TokenBucketLimiter(redis)
    res = await tb.acquire("p", requests_per_minute=10, tokens_per_minute=100, estimated_tokens=5000)
    assert res.admitted


async def test_token_bucket_scopes_are_independent(redis: Redis) -> None:
    tb = TokenBucketLimiter(redis)
    kw = {"requests_per_minute": 1, "tokens_per_minute": 1000, "estimated_tokens": 1}
    assert (await tb.acquire("openai", **kw)).admitted
    assert (await tb.acquire("anthropic", **kw)).admitted
    assert not (await tb.acquire("openai", **kw)).admitted


# ----------------------------------------------------------------------- concurrency


async def test_concurrency_cap(redis: Redis) -> None:
    sem = ConcurrencyLimiter(redis, lease_seconds=60)
    a = await sem.try_acquire("p", 2)
    b = await sem.try_acquire("p", 2)
    assert a
    assert b
    assert await sem.try_acquire("p", 2) is None
    await sem.release("p", a)
    assert await sem.try_acquire("p", 2) is not None
    assert await sem.in_flight("p") == 2


async def test_concurrency_lease_expires(redis: Redis) -> None:
    sem = ConcurrencyLimiter(redis, lease_seconds=0.3)
    assert await sem.try_acquire("p", 1)
    assert await sem.try_acquire("p", 1) is None
    await asyncio.sleep(0.35)
    assert await sem.try_acquire("p", 1)  # the crashed holder's lease was reclaimed


# ----------------------------------------------------------------------- circuit breaker


async def test_breaker_opens_after_threshold_and_half_opens(redis: Redis) -> None:
    cb = CircuitBreaker(redis, failure_threshold=3, window_seconds=10, cooldown_seconds=0.3)
    assert (await cb.allow("p")).allowed
    assert not await cb.record_failure("p")
    assert not await cb.record_failure("p")
    assert await cb.record_failure("p")  # third failure trips it

    decision = await cb.allow("p")
    assert not decision.allowed
    assert decision.state is BreakerState.OPEN
    assert 0 < decision.retry_after_seconds <= 0.3

    await asyncio.sleep(0.35)
    probe = await cb.allow("p")
    assert probe.allowed
    assert probe.state is BreakerState.HALF_OPEN
    assert not (await cb.allow("p")).allowed  # only one probe at a time

    await cb.record_success("p")
    assert (await cb.allow("p")).state is BreakerState.CLOSED


async def test_failed_probe_reopens(redis: Redis) -> None:
    cb = CircuitBreaker(redis, failure_threshold=1, window_seconds=10, cooldown_seconds=0.3)
    await cb.record_failure("p")
    await asyncio.sleep(0.35)
    assert (await cb.allow("p")).allowed  # probe
    assert await cb.record_failure("p")  # probe failed -> open again
    assert not (await cb.allow("p")).allowed


async def test_success_while_open_does_not_close(redis: Redis) -> None:
    cb = CircuitBreaker(redis, failure_threshold=1, window_seconds=10, cooldown_seconds=5)
    await cb.record_failure("p")
    await cb.record_success("p")  # a slow call that started before the trip
    assert await cb.state("p") is BreakerState.OPEN
