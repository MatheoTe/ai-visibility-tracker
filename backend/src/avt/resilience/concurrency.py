"""Distributed concurrency cap (a leased semaphore on a Redis sorted set).

Each holder adds a member scored with its lease expiry. Expired leases are swept on
every acquire, so a worker that dies mid-call can never leak a slot for longer than
the lease.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from redis.asyncio import Redis

_ACQUIRE = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local lease_ms = tonumber(ARGV[2])
local member = ARGV[3]
local t = redis.call('TIME')
local now = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, now + lease_ms, member)
  redis.call('PEXPIRE', key, lease_ms * 2)
  return 1
end
return 0
"""


class ConcurrencyLimiter:
    def __init__(self, redis: Redis, *, prefix: str = "avt:sem", lease_seconds: float = 120) -> None:
        self._redis = redis
        self._prefix = prefix
        self._lease_ms = int(lease_seconds * 1000)
        self._acquire = redis.register_script(_ACQUIRE)

    def _key(self, scope: str) -> str:
        return f"{self._prefix}:{scope}"

    async def try_acquire(self, scope: str, limit: int) -> str | None:
        """Return a lease token, or ``None`` when ``limit`` holders already exist."""
        token = uuid.uuid4().hex
        ok = cast("Any", await self._acquire(keys=[self._key(scope)], args=[limit, self._lease_ms, token]))
        return token if int(ok) == 1 else None

    async def release(self, scope: str, token: str) -> None:
        await self._redis.zrem(self._key(scope), token)

    async def in_flight(self, scope: str) -> int:
        return int(await self._redis.zcard(self._key(scope)))
