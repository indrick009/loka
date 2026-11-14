"""Conversations held for a person to look at, kept in Redis.

A hold is operational state, not business state: it says "Kora stopped
answering this thread until someone releases it". It lives beside the locks
and the rate limiter rather than in the session, so the outbound worker can
set it without racing the turn that owns the session row.

Redis being down never holds anybody: every read answers "not held".
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_HOLD = "hold:{}"
_INDEX = "hold:index"
_MISREAD = "misread:{}"
# A run of misread turns is only meaningful within one sitting.
_MISREAD_TTL_SECONDS = 6 * 3600


@dataclass(frozen=True, slots=True)
class Hold:
    session_id: str
    phone: str
    signal: str
    language: str
    # UTC, ISO 8601, like the conversation journal.
    since: str
    # The reply that was not sent, for whoever reads the alert.
    withheld: str
    # The last message received while held: answered first on release.
    pending_message_id: str | None = None


class RedisConversationHolds:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, session_id: uuid.UUID | str) -> Hold | None:
        try:
            raw = await self._redis.get(_HOLD.format(session_id))
        except RedisError as exc:
            _logger.warning("holds_unavailable", error=type(exc).__name__)
            return None
        return Hold(**json.loads(raw)) if raw else None

    async def put(self, hold: Hold) -> None:
        try:
            await self._redis.set(_HOLD.format(hold.session_id), json.dumps(asdict(hold)))
            await self._redis.sadd(_INDEX, hold.session_id)
        except RedisError as exc:
            _logger.warning("holds_unavailable", error=type(exc).__name__)

    async def note_pending(self, hold: Hold, message_id: uuid.UUID) -> None:
        await self.put(Hold(**{**asdict(hold), "pending_message_id": str(message_id)}))

    async def release(self, session_id: uuid.UUID | str) -> None:
        await self._redis.delete(_HOLD.format(session_id), _MISREAD.format(session_id))
        await self._redis.srem(_INDEX, str(session_id))

    async def all(self) -> list[Hold]:
        holds = [await self.get(str(member)) for member in await self._redis.smembers(_INDEX)]
        return sorted((h for h in holds if h is not None), key=lambda h: h.since)

    async def misread_run(self, session_id: uuid.UUID, *, misread: bool) -> int:
        """How many turns in a row have now been answered "not understood"."""
        key = _MISREAD.format(session_id)
        try:
            if not misread:
                await self._redis.delete(key)
                return 0
            run = int(await self._redis.incr(key))
            await self._redis.expire(key, _MISREAD_TTL_SECONDS)
            return run
        except RedisError as exc:
            _logger.warning("holds_unavailable", error=type(exc).__name__)
            return 0
