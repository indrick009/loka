"""Every morning, a new profile for whoever has nothing under way.

Proposals only came when a member wrote « rencontre »: Aimée, quiet since the
24th, was never shown anyone again (decision of 26 Nov 2026). Once a day,
between ``HOUR`` and ``HOUR + 1`` (Douala), a member with no talk, no recent
match and nothing asked lately gets the best profile they have not answered.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.dating.conversation import texts
from loka.bounded_contexts.dating.conversation.ranking import proposing, ranked_for
from loka.bounded_contexts.dating.infrastructure.store import SqlAlchemyDatingStore
from loka.bounded_contexts.food.domain.menu import DOUALA
from loka.interfaces.worker.maintenance_desk import under_maintenance
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

HOUR = 9
SWEEP_EVERY_SECONDS = 10 * 60
# Someone who wrote this recently asks for profiles themselves.
QUIET_HOURS = 20
MATCH_QUIET_DAYS = 7
_KEY = "dating:morning:{day}:{profile}"
_KEEP_SECONDS = 2 * 86400


async def propose_once(database: Database, redis: Redis, *, now: datetime) -> int:
    local = now.astimezone(DOUALA)
    if local.hour != HOUR or await under_maintenance(redis):
        return 0
    sent = 0
    async with unit_of_work(database) as uow:
        store = SqlAlchemyDatingStore(uow.session)
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        wrote = await store.last_wrote()
        for member in await store.members():
            key = _KEY.format(day=local.date().isoformat(), profile=member.profile_id)
            last = wrote.get(member.user_id)
            if await redis.exists(key) or (
                last is not None and now - last < timedelta(hours=QUIET_HOURS)
            ):
                continue
            if await store.open_talk_of(member.profile_id) is not None or (
                await store.matched_since(member.profile_id, now - timedelta(days=MATCH_QUIET_DAYS))
            ):
                continue
            if await desk.busy(member.user_id, now=now):
                continue
            ranked = await ranked_for(store, member, now=now)
            await redis.set(key, "1", ex=_KEEP_SECONDS)
            if not ranked:
                continue
            other = ranked[0][0]
            text = f"{texts.morning_hello()}\n{texts.proposal(other, 'fr')}"
            if await desk.notify(
                member.user_id,
                text,
                now=now,
                steer=proposing(other),
                media_object_key=other.photo_key or None,
            ):
                journal("dating_morning", str(member.user_id), other=other.first_name)
                sent += 1
        await uow.commit()
    return sent


async def propose_forever(database: Database, settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    while True:
        try:
            await propose_once(database, redis, now=datetime.now(UTC))
        except Exception as exc:  # the next sweep tries again
            _logger.warning("dating_morning_failed", error=type(exc).__name__)
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
