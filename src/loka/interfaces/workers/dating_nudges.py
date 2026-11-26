"""Nobody is held waiting on someone who does not answer (decision of 26 Nov 2026).

A yes unanswered, a message or a CONFIRMER left without a word back: after
``WAIT_HOURS`` the member who waits is told, once, that the other has not
answered yet and that other profiles are there meanwhile. Only in the day
(Douala), and never while the member is in the middle of something else.
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
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.infrastructure.store import SqlAlchemyDatingStore
from loka.bounded_contexts.food.domain.menu import DOUALA
from loka.interfaces.worker.maintenance_desk import under_maintenance
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

WAIT_HOURS = 3
# Older yeses are left alone: a nudge about a week-old yes reads as noise.
LOOK_BACK_DAYS = 3
FIRST_HOUR, LAST_HOUR = 8, 21
SWEEP_EVERY_SECONDS = 15 * 60
_YES_KEY = "dating:nudged:yes:{waiting}:{silent}"
_TALK_KEY = "dating:nudged:talk:{talk}:{side}:{at}"
_KEEP_SECONDS = 30 * 86400


async def nudge_once(database: Database, redis: Redis, *, now: datetime) -> int:
    if not FIRST_HOUR <= now.astimezone(DOUALA).hour < LAST_HOUR:
        return 0
    if await under_maintenance(redis):
        return 0
    cutoff = now - timedelta(hours=WAIT_HOURS)
    sent = 0
    async with unit_of_work(database) as uow:
        store = SqlAlchemyDatingStore(uow.session)
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        waits = []
        since = now - timedelta(days=LOOK_BACK_DAYS)
        # One message per member, naming everyone he waits on.
        yeses: dict[str, tuple[Profile, list[Profile]]] = {}
        for waiting, silent in await store.unanswered_yeses(cutoff, since):
            if await store.open_talk_of(waiting.profile_id) is not None:
                continue
            yeses.setdefault(str(waiting.profile_id), (waiting, []))[1].append(silent)
        for waiting, silents in yeses.values():
            ids = ".".join(sorted(str(s.profile_id) for s in silents))
            key = _YES_KEY.format(waiting=waiting.profile_id, silent=ids)
            waits.append((key, waiting, texts.still_waiting_yes([s.first_name for s in silents])))
        for talk in await store.open_talks():
            pair = talk.waiting()
            if pair is None or pair[0].last_at > int(cutoff.timestamp()):
                continue
            waiter, other = pair
            member = await store.profile(waiter.profile_id)
            them = await store.profile(other.profile_id)
            if member is None or them is None:
                continue
            key = _TALK_KEY.format(talk=talk.talk_id, side=waiter.profile_id, at=waiter.last_at)
            text = texts.still_waiting_talk(them, confirmed=waiter.confirmed)
            waits.append((key, member, text))
        for key, waiting, text in waits:
            if await redis.exists(key) or await desk.busy(waiting.user_id, now=now):
                continue
            if await desk.notify(waiting.user_id, text, now=now):
                await redis.set(key, "1", ex=_KEEP_SECONDS)
                journal("dating_nudge", str(waiting.user_id), text=text[:80])
                sent += 1
        await uow.commit()
    return sent


async def sweep_forever(database: Database, settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    while True:
        try:
            await nudge_once(database, redis, now=datetime.now(UTC))
        except Exception as exc:  # the next sweep tries again
            _logger.warning("dating_nudges_failed", error=type(exc).__name__)
        await asyncio.sleep(SWEEP_EVERY_SECONDS)
