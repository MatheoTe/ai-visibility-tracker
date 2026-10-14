"""Distributed dual token bucket (requests/min + tokens/min) on Redis.

One Lua script refills and debits *both* buckets atomically, using the Redis server
clock so workers with skewed clocks still share one consistent budget. When the
request cannot be admitted the script returns how long until it could be - the
caller re-schedules the job for then instead of holding a worker slot while
sleeping.

Token usage is only known after the call, so admission debits an *estimate* and
:meth:`reconcile` settles the difference. The bucket may go briefly negative
("debt"), which naturally throttles the next callers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, cast

from redis.asyncio import Redis

_ACQUIRE = """
local key = KEYS[1]
local req_cap = tonumber(ARGV[1])
local tok_cap = tonumber(ARGV[2])
local req_cost = tonumber(ARGV[3])
local tok_cost = tonumber(ARGV[4])
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
local req_rate = req_cap / 60000.0
local tok_rate = tok_cap / 60000.0

local state = redis.call('HMGET', key, 'r', 't', 'ts')
local r = tonumber(state[1])
local tk = tonumber(state[2])
local ts = tonumber(state[3])
if r == nil then r = req_cap; tk = tok_cap; ts = now end

local elapsed = math.max(0, now - ts)
r = math.min(req_cap, r + elapsed * req_rate)
tk = math.min(tok_cap, tk + elapsed * tok_rate)

-- A single call larger than the whole token capacity may still pass once the
-- bucket is full, otherwise it could never run.
local tok_need = math.min(tok_cost, tok_cap)
local admitted = 0
local wait_ms = 0
if r >= req_cost and tk >= tok_need then
  r = r - req_cost
  tk = tk - tok_need
  admitted = 1
else
  local wait_r = 0
  local wait_t = 0
  if r < req_cost then wait_r = (req_cost - r) / req_rate end
  if tk < tok_need then wait_t = (tok_need - tk) / tok_rate end
  wait_ms = math.ceil(math.max(wait_r, wait_t))
end
redis.call('HSET', key, 'r', tostring(r), 't', tostring(tk), 'ts', tostring(now))
redis.call('PEXPIRE', key, 120000)
return {admitted, wait_ms}
"""

_RECONCILE = """
local key = KEYS[1]
if redis.call('EXISTS', key) == 1 then
  redis.call('HINCRBYFLOAT', key, 't', ARGV[1])
end
return 1
"""


@dataclass(frozen=True, slots=True)
class Admission:
    admitted: bool
    retry_after_seconds: float


class TokenBucketLimiter:
    def __init__(self, redis: Redis, *, prefix: str = "avt:tb") -> None:
        self._redis = redis
        self._prefix = prefix
        self._acquire = redis.register_script(_ACQUIRE)
        self._reconcile = redis.register_script(_RECONCILE)

    def _key(self, scope: str) -> str:
        return f"{self._prefix}:{scope}"

    async def acquire(
        self,
        scope: str,
        *,
        requests_per_minute: int,
        tokens_per_minute: int,
        estimated_tokens: int,
    ) -> Admission:
        result = cast(
            "list[Any]",
            await self._acquire(
                keys=[self._key(scope)],
                args=[requests_per_minute, tokens_per_minute, 1, max(0, estimated_tokens)],
            ),
        )
        admitted, wait_ms = int(result[0]), int(result[1])
        return Admission(admitted=bool(admitted), retry_after_seconds=math.ceil(wait_ms) / 1000)

    async def reconcile(self, scope: str, *, estimated_tokens: int, actual_tokens: int) -> None:
        """Give back over-estimated tokens, or charge the under-estimate as debt."""
        delta = estimated_tokens - actual_tokens
        if delta:
            await self._reconcile(keys=[self._key(scope)], args=[delta])
