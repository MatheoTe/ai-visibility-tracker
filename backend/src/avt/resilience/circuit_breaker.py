"""Per-provider circuit breaker shared by every worker (state lives in Redis).

closed     -> calls flow; successes and retryable failures are counted in a sliding
              window of time buckets.
open       -> when the window holds at least ``min_requests`` calls and the failure
              *ratio* reaches ``failure_ratio``, calls are rejected (re-scheduled)
              until ``cooldown`` elapses.
half-open  -> after the cooldown exactly one probe call is let through (``SET NX``).
              Success closes the breaker, failure re-opens it for another cooldown.

The trip condition is a ratio, not a count: at 300 calls/s a healthy provider with a
2% error rate produces 6 failures per second, and a count threshold would trip on
that every few seconds. ``min_requests`` keeps a handful of early failures from
tripping it at low volume.

The point is to stop thousands of queued tasks from hammering a provider that is
down, burning their retry attempts and the run's retry budget in the process.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from redis.asyncio import Redis

# Shared Lua prelude: server clock + tally of the sliding window. The window is a
# hash with fields "<bucket>:s" / "<bucket>:f"; stale buckets are pruned in place.
_PRELUDE = """
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local function tally(win_key, bucket, buckets)
  local ok, fail = 0, 0
  local fields = redis.call('HKEYS', win_key)
  for _, field in ipairs(fields) do
    local b = tonumber(string.match(field, '^(%d+):'))
    if b == nil or b <= bucket - buckets then
      redis.call('HDEL', win_key, field)
    elseif string.sub(field, -1) == 's' then
      ok = ok + tonumber(redis.call('HGET', win_key, field))
    else
      fail = fail + tonumber(redis.call('HGET', win_key, field))
    end
  end
  return ok, fail
end
"""

_ALLOW = (
    _PRELUDE
    + """
local open_key = KEYS[1]
local probe_key = KEYS[2]
local probe_ttl_ms = tonumber(ARGV[1])
local open_until = tonumber(redis.call('GET', open_key))
if open_until == nil then return {1, 0, 0} end
if now < open_until then return {0, open_until - now, 1} end
-- cooldown elapsed: half-open, admit a single probe
if redis.call('SET', probe_key, '1', 'NX', 'PX', probe_ttl_ms) then return {1, 0, 2} end
return {0, 1000, 2}
"""
)

_RECORD = (
    _PRELUDE
    + """
local win_key = KEYS[1]
local open_key = KEYS[2]
local probe_key = KEYS[3]
local outcome = ARGV[1]  -- 's' or 'f'
local ratio = tonumber(ARGV[2])
local min_requests = tonumber(ARGV[3])
local window_ms = tonumber(ARGV[4])
local bucket_ms = tonumber(ARGV[5])
local cooldown_ms = tonumber(ARGV[6])

local open_until = tonumber(redis.call('GET', open_key))
if open_until ~= nil then
  if now < open_until then
    -- Open: late results from calls started before the trip change nothing.
    return 0
  end
  -- Half-open: this is the probe's result.
  if outcome == 's' then
    redis.call('DEL', win_key, open_key, probe_key)
    return 2
  end
  redis.call('SET', open_key, now + cooldown_ms, 'PX', cooldown_ms * 10)
  redis.call('DEL', probe_key)
  return 1
end

local bucket = math.floor(now / bucket_ms)
redis.call('HINCRBY', win_key, bucket .. ':' .. outcome, 1)
redis.call('PEXPIRE', win_key, window_ms * 2)
if outcome == 's' then return 0 end

local ok, fail = tally(win_key, bucket, math.ceil(window_ms / bucket_ms))
local total = ok + fail
if total >= min_requests and fail / total >= ratio then
  redis.call('SET', open_key, now + cooldown_ms, 'PX', cooldown_ms * 10)
  redis.call('DEL', win_key, probe_key)
  return 1
end
return 0
"""
)


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
        failure_ratio: float = 0.5,
        min_requests: int = 20,
        window_seconds: float = 30,
        cooldown_seconds: float = 20,
        probe_ttl_seconds: float = 60,
        bucket_seconds: float = 5,
        prefix: str = "avt:cb",
    ) -> None:
        self._redis = redis
        self._ratio = failure_ratio
        self._min_requests = min_requests
        self._window_ms = int(window_seconds * 1000)
        self._bucket_ms = max(1, int(min(bucket_seconds, window_seconds) * 1000))
        self._cooldown_ms = int(cooldown_seconds * 1000)
        # How long a half-open probe may be in flight before another is allowed.
        self._probe_ttl_ms = int(probe_ttl_seconds * 1000)
        self._prefix = prefix
        self._allow = redis.register_script(_ALLOW)
        self._record = redis.register_script(_RECORD)

    def _keys(self, scope: str) -> list[str]:
        p = f"{self._prefix}:{scope}"
        return [f"{p}:win", f"{p}:open", f"{p}:probe"]

    async def allow(self, scope: str) -> BreakerDecision:
        _, open_key, probe_key = self._keys(scope)
        res = cast("list[Any]", await self._allow(keys=[open_key, probe_key], args=[self._probe_ttl_ms]))
        return BreakerDecision(
            allowed=bool(int(res[0])),
            retry_after_seconds=int(res[1]) / 1000,
            state=_STATES[int(res[2])],
        )

    async def _record_outcome(self, scope: str, outcome: str) -> int:
        result = await self._record(
            keys=self._keys(scope),
            args=[
                outcome,
                self._ratio,
                self._min_requests,
                self._window_ms,
                self._bucket_ms,
                self._cooldown_ms,
            ],
        )
        return int(cast("Any", result))

    async def record_success(self, scope: str) -> None:
        await self._record_outcome(scope, "s")

    async def record_failure(self, scope: str) -> bool:
        """Count a retryable failure. Returns True if this call opened the breaker."""
        return await self._record_outcome(scope, "f") == 1

    async def state(self, scope: str) -> BreakerState:
        open_key = self._keys(scope)[1]
        return BreakerState.OPEN if await self._redis.exists(open_key) else BreakerState.CLOSED
