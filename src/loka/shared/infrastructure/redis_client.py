"""Redis-backed rate limiting, distributed locks and idempotency.

Redis is never a source of truth for business data: every guard here is a
performance optimisation whose loss only costs efficiency, never correctness.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import cache_operations_total

_logger = get_logger(__name__)

# Short enough that a freed lock is picked up promptly, long enough not to
# hammer Redis while a turn runs.
_LOCK_POLL_SECONDS = 0.25


class NotAcquired(RuntimeError):
    """The lock is held by someone else. Try again shortly."""


class LockBackendUnavailable(NotAcquired):
    """The lock could not even be attempted: Redis itself is unreachable.

    A subclass, so every ``except NotAcquired`` keeps failing closed — losing
    mutual exclusion because the backend is down would be far worse than
    delaying the work. But the two are different incidents: contention clears
    by itself in seconds, an outage hits every conversation at once, and an
    operator told "already being analysed" during an outage goes looking for a
    stuck worker instead of for Redis.
    """


class RateLimiter(Protocol):
    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool: ...


class RedisRateLimiter:
    """Fixed-window counter. Adequate for WhatsApp abuse control; swap for a
    sliding window or token bucket if abuse patterns demand finer granularity."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        window = int(asyncio.get_running_loop().time()) // window_seconds
        bucket = f"rl:{key}:{window}"
        try:
            count = await self._redis.incr(bucket)
            if count == 1:
                await self._redis.expire(bucket, window_seconds * 2)
        except RedisError as exc:
            _logger.warning("rate_limiter_unavailable", error=str(exc))
            return True
        allowed = count <= limit
        cache_operations_total.labels(result="allowed" if allowed else "blocked").inc()
        return allowed


class RedisIdempotencyStore:
    """Guards replayed inbound webhooks. The DB unique constraint remains
    the authoritative guard; this only short-circuits obvious duplicates."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def acquire(self, key: str, *, ttl_seconds: int) -> bool:
        try:
            ok = await self._redis.set(f"idem:{key}", "1", nx=True, ex=ttl_seconds)
        except RedisError:
            return True
        return bool(ok)

    async def release(self, key: str) -> None:
        try:
            await self._redis.delete(f"idem:{key}")
        except RedisError:
            return


class RedisLock:
    """SET NX PX lock with a Lua-guarded release.

    Used only where a DB constraint cannot express the mutual exclusion, for
    example serialising outbound WhatsApp sends per conversation.
    """

    _RELEASE_SCRIPT = """
    if redis.call('get', KEYS[1]) == ARGV[1] then
        return redis.call('del', KEYS[1])
    else
        return 0
    end
    """

    def __init__(
        self,
        redis: Redis,
        *,
        name: str,
        ttl_ms: int = 10_000,
        wait_ms: int = 0,
    ) -> None:
        self._redis = redis
        self._key = f"lock:{name}"
        self._ttl_ms = ttl_ms
        # How long to keep trying before giving up. Zero keeps the original
        # fail-fast behaviour; a caller whose retry budget is shorter than the
        # work it serialises needs to wait here instead, or its second message
        # dead-letters while the first is still being processed.
        self._wait_ms = wait_ms
        # Globally unique. ``id(self)`` is only unique within one process, so
        # two workers could mint the same token and one would then release the
        # other's lock — the single failure this lock exists to prevent.
        self._token = uuid.uuid4().hex

    async def __aenter__(self) -> RedisLock:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._wait_ms / 1000
        while True:
            try:
                acquired = await self._redis.set(
                    self._key, self._token, nx=True, px=self._ttl_ms
                )
            except RedisError as exc:
                raise LockBackendUnavailable(f"lock backend unavailable: {exc}") from exc
            if acquired:
                return self
            if loop.time() >= deadline:
                raise NotAcquired(f"could not acquire lock {self._key}")
            await asyncio.sleep(_LOCK_POLL_SECONDS)

    async def __aexit__(self, *exc_info: object) -> None:
        try:
            await self._redis.eval(self._RELEASE_SCRIPT, 1, self._key, self._token)
        except RedisError as exc:
            _logger.warning("lock_release_failed", key=self._key, error=str(exc))


@dataclass(frozen=True, slots=True)
class CacheEntry:
    value: str
    version: str | None = None


class VersionedCache:
    """Cache keyed by aggregate version.

    Invalidation happens by publishing the new version from the domain event,
    so a stale entry can never surface a property as available after it was
    rented: readers compare the cached version with the current one.
    """

    def __init__(self, redis: Redis, *, namespace: str = "cache") -> None:
        self._redis = redis
        self._namespace = namespace

    async def get(self, key: str) -> str | None:
        try:
            value = await self._redis.get(self._full(key))
        except RedisError as exc:
            _logger.warning("cache_read_failed", key=key, error=str(exc))
            return None
        cache_operations_total.labels(result="hit" if value else "miss").inc()
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, *, ttl_seconds: int = 300) -> None:
        try:
            await self._redis.set(self._full(key), value, ex=ttl_seconds)
            cache_operations_total.labels(result="write").inc()
        except RedisError as exc:
            _logger.warning("cache_write_failed", key=key, error=str(exc))

    async def invalidate(self, key: str) -> None:
        try:
            await self._redis.delete(self._full(key))
        except RedisError:
            return

    def _full(self, key: str) -> str:
        return f"{self._namespace}:{key}"


async def build_redis(url: str, *, max_connections: int = 100) -> Redis:
    return Redis.from_url(
        url,
        max_connections=max_connections,
        decode_responses=True,
        socket_keepalive=True,
        socket_connect_timeout=3,
        socket_timeout=3,
        health_check_interval=30,
    )


async def redis_healthcheck(redis: Redis) -> bool:
    try:
        return bool(await redis.ping())
    except RedisError as exc:
        _logger.warning("redis_healthcheck_failed", error=str(exc))
        return False