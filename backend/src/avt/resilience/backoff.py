"""Exponential backoff with full jitter, and a per-run retry budget."""

from __future__ import annotations

import random

from redis.asyncio import Redis


def backoff_delay(
    attempt: int,
    *,
    base: float = 1.0,
    cap: float = 60.0,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Delay before retry number ``attempt`` (1-based).

    Full jitter (``uniform(0, min(cap, base * 2**(attempt-1)))``) spreads retries of
    a burst of failed calls instead of re-synchronising them into a second burst.
    A server-provided ``Retry-After`` is a floor: we never retry earlier than asked.
    """
    if attempt < 1:
        raise ValueError("attempt is 1-based")
    rng = rng or random.Random()
    ceiling = min(cap, base * (2 ** (attempt - 1)))
    delay = rng.uniform(0, ceiling)
    if retry_after is not None:
        delay = max(delay, min(retry_after, cap))
    return delay


class RetryBudget:
    """Caps the total retries a run may spend (``max(floor, ratio * tasks)``).

    Per-task attempt limits alone allow a degraded provider to multiply load by
    ``max_attempts``; the budget bounds that amplification for the whole run, after
    which failing tasks are dead-lettered immediately and can be retried manually.
    """

    def __init__(self, redis: Redis, *, ratio: float, floor: int, prefix: str = "avt:rb") -> None:
        self._redis = redis
        self._ratio = ratio
        self._floor = floor
        self._prefix = prefix

    def limit_for(self, total_tasks: int) -> int:
        return max(self._floor, int(total_tasks * self._ratio))

    async def try_spend(self, run_id: str, total_tasks: int) -> bool:
        key = f"{self._prefix}:{run_id}"
        spent = int(await self._redis.incr(key))
        if spent == 1:
            await self._redis.expire(key, 7 * 24 * 3600)
        return spent <= self.limit_for(total_tasks)

    async def reset(self, run_id: str) -> None:
        await self._redis.delete(f"{self._prefix}:{run_id}")
