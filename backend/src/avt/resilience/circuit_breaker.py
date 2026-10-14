"""Per-provider circuit breaker shared by every worker (state lives in Redis).

closed     -> calls flow; retryable failures are counted in a sliding window.
open       -> ``threshold`` failures inside ``window`` trip the breaker; calls are
              rejected (re-scheduled) until ``cooldown`` elapses.
half-open  -> after the cooldown exactly one probe call is let through (``SET NX``).
              Success closes the breaker, failure re-opens it for another cooldown.

The point is to stop thousands of queued tasks from hammering a provider that is
down, burning their retry attempts and our retry budget in the process.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from redis.asyncio import Redis

_ALLOW = """
local open_key = KEYS[1]
local probe_key = KEYS[2]
local probe_ttl_ms = tonumber(ARGV[1])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local open_until = tonumber(redis.call('GET', open_key))
if open_until == nil then return {1, 0, 0} end
if now < open_until then return {0, open_until - now, 1} end
-- cooldown elapsed: half-open, admit a single probe
if redis.call('SET', probe_key, '1', 'NX', 'PX', probe_ttl_ms) then return {1, 0, 2} end
return {0, 1000, 2}
"""

_FAILURE = """
local fail_key = KEYS[1]
local open_key = KEYS[2]
local probe_key = KEYS[3]
local threshold = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local cooldown_ms = tonumber(ARGV[3])
local member = ARGV[4]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local open_until = tonumber(redis.call('GET', open_key))
if open_until ~= nil and now >= open_until then
  -- the half-open probe failed: re-open
  redis.call('SET', open_key, now + cooldown_ms, 'PX', cooldown_ms * 10)
  redis.call('DEL', probe_key)
  return 1
end
redis.call('ZADD', fail_key, now, member)
redis.call('ZREMRANGEBYSCORE', fail_key, '-inf', now - window_ms)
redis.call('PEXPIRE', fail_key, window_ms * 2)
if open_until == nil and redis.call('ZCARD', fail_key) >= threshold then
  redis.call('SET', open_key, now + cooldown_ms, 'PX', cooldown_ms * 10)
  redis.call('DEL', fail_key)
  return 1
end
return 0
"""


_SUCCESS = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local open_until = tonumber(redis.call('GET', KEYS[2]))
-- A success only closes a half-open breaker (cooldown over). A slow call that
-- started before the breaker opened must not close it early.
if open_until ~= nil and now >= open_until then
  redis.call('DEL', KEYS[1], KEYS[2], KEYS[3])
  return 1
end
return 0
"""


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_STATES = {0: BreakerState.CLOSED, 1: BreakerState.OPEN, 2: BreakerState.HALF_OPEN}


@dataclass(frozen=True, slots=True)
class BreakerDecision:
    allowed: bool
    retry_after_seconds: float
    state: BreakerState


class CircuitBreaker:
    def __init__(
        self,
        redis: Redis,
        *,
        failure_threshold: int,
        window_seconds: float,
        cooldown_seconds: float,
        probe_ttl_seconds: float = 60,
        prefix: str = "avt:cb",
    ) -> None:
        self._redis = redis
        self._threshold = failure_threshold
        self._window_ms = int(window_seconds * 1000)
        self._cooldown_ms = int(cooldown_seconds * 1000)
        # How long a half-open probe may be in flight before another is allowed.
        self._probe_ttl_ms = int(probe_ttl_seconds * 1000)
        self._prefix = prefix
        self._allow = redis.register_script(_ALLOW)
        self._failure = redis.register_script(_FAILURE)
        self._success = redis.register_script(_SUCCESS)

    def _keys(self, scope: str) -> tuple[str, str, str]:
        p = f"{self._prefix}:{scope}"
        return f"{p}:fail", f"{p}:open", f"{p}:probe"

    async def allow(self, scope: str) -> BreakerDecision:
        _, open_key, probe_key = self._keys(scope)
        res = cast("list[Any]", await self._allow(keys=[open_key, probe_key], args=[self._probe_ttl_ms]))
        return BreakerDecision(
            allowed=bool(int(res[0])),
            retry_after_seconds=int(res[1]) / 1000,
            state=_STATES[int(res[2])],
        )

    async def record_success(self, scope: str) -> None:
        await self._success(keys=list(self._keys(scope)), args=[])

    async def record_failure(self, scope: str) -> bool:
        """Count a retryable failure. Returns True if this call opened the breaker."""
        fail_key, open_key, probe_key = self._keys(scope)
        member = uuid.uuid4().hex
        opened = await self._failure(
            keys=[fail_key, open_key, probe_key],
            args=[self._threshold, self._window_ms, self._cooldown_ms, member],
        )
        return bool(int(cast("Any", opened)))

    async def state(self, scope: str) -> BreakerState:
        _, open_key, _ = self._keys(scope)
        raw = await self._redis.get(open_key)
        if raw is None:
            return BreakerState.CLOSED
        return BreakerState.OPEN
