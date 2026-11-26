"""Nobody is held waiting on someone who does not answer (decision of 26 Nov 2026).

A yes unanswered: after an hour the one who has not answered is shown that
person again, once; after ``WAIT_HOURS`` the one who waits gets the next
profile with the news. A message or a CONFIRMER left without a word back:
the one who waits is told, once. Only in the day
(Douala), and never while the member is in the middle of something else.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.application.plugs.turn import Steer
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.dating.conversation import texts
from loka.bounded_contexts.dating.conversation.keys import LIKE_KEY, PROPOSED_KEY
from loka.bounded_contexts.dating.domain.matching import best_candidates
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
# The one who has not answered sees the person again after an hour, and gets
# at most one such reminder per ``ASK_SPACING_SECONDS``.
ASK_AFTER_MINUTES = 60
# A recent match: nobody pushes other profiles at its members.
MATCH_QUIET_DAYS = 7
ASK_SPACING_SECONDS = 3 * 3600
_ASK_KEY = "dating:ask:{silent}:{waiting}"
_ASKED_KEY = "dating:asked:{silent}"


@dataclass(frozen=True, slots=True)
class _Nudge:
    key: str
    member: Profile
    text: str
    media: str | None = None
    steer: Steer | None = None


def _proposing(other: Profile) -> Steer:
    """The member's next answer is read as a yes or a no to ``other``."""
    return Steer(
        flow=FlowName.DATING_PROFILE,
        step=FlowStep.COLLECT_CHOICE,
        question_key=LIKE_KEY,
        context={PROPOSED_KEY: str(other.profile_id), "_score": 0.0},
    )


async def _silent_side(
    store: SqlAlchemyDatingStore, redis: Redis, now: datetime
) -> list[_Nudge]:
    """A yes not answered after ``ASK_AFTER_MINUTES``: the one who has not
    answered sees that person again, once. Laure's yes to Morel never reached
    him, he was answering someone else (journal of 26 Nov 2026, 07:13)."""
    nudges: list[_Nudge] = []
    asked: set[str] = set()
    since = now - timedelta(days=LOOK_BACK_DAYS)
    for waiting, silent in await store.unanswered_yeses(
        now - timedelta(minutes=ASK_AFTER_MINUTES), since
    ):
        sid = str(silent.profile_id)
        # One person at a time: a second proposal would take the first one's place.
        if sid in asked or await redis.exists(_ASKED_KEY.format(silent=sid)):
            continue
        if await store.open_talk_of(silent.profile_id) is not None:
            continue
        key = _ASK_KEY.format(silent=sid, waiting=waiting.profile_id)
        if await redis.exists(key):
            continue
        asked.add(sid)
        text = f"{texts.someone_waits()}\n{texts.proposal(waiting, 'fr')}"
        nudges.append(_Nudge(key, silent, text, waiting.photo_key or None, _proposing(waiting)))
    return nudges


async def _next_for(store: SqlAlchemyDatingStore, member: Profile) -> Profile | None:
    excluded = await store.answered(member.profile_id) | await store.passed_by(member.profile_id)
    ranked = best_candidates(
        member, await store.members(), excluded=excluded, refusals=await store.refusals()
    )
    return ranked[0][0] if ranked else None


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
        nudges = await _silent_side(store, redis, now)
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
            names = [s.first_name for s in silents]
            # Three hours at most: the next profile comes with the news
            # (decision of 26 Nov 2026).
            other = await _next_for(store, waiting)
            if other is None:
                nudges.append(_Nudge(key, waiting, texts.still_waiting_yes(names)))
                continue
            text = f"{texts.still_waiting_next(names)}\n\n{texts.proposal(other, 'fr')}"
            nudges.append(
                _Nudge(key, waiting, text, other.photo_key or None, _proposing(other))
            )
        # Members someone said yes to, still unanswered: Morel, in a talk
        # Margaret left, is told someone else waits for him.
        wanted = {
            silent.profile_id
            for _, silent in await store.unanswered_yeses(
                now - timedelta(minutes=ASK_AFTER_MINUTES), since
            )
        }
        for talk in await store.open_talks():
            pair = talk.waiting()
            if pair is None or pair[0].last_at > int(cutoff.timestamp()):
                continue
            waiter, other_side = pair
            member = await store.profile(waiter.profile_id)
            them = await store.profile(other_side.profile_id)
            if member is None or them is None:
                continue
            key = _TALK_KEY.format(talk=talk.talk_id, side=waiter.profile_id, at=waiter.last_at)
            text = texts.still_waiting_talk(
                them, confirmed=waiter.confirmed, wanted=member.profile_id in wanted
            )
            nudges.append(_Nudge(key, member, text))
        told: set[str] = set()
        matched_after = now - timedelta(days=MATCH_QUIET_DAYS)
        for nudge in nudges:
            uid = str(nudge.member.user_id)
            if uid in told or await redis.exists(nudge.key):
                continue
            if await store.matched_since(nudge.member.profile_id, matched_after):
                # Eva, matched with Ismaël, was told four minutes later to look
                # at other profiles (journal of 26 Nov 2026, 10:43).
                continue
            if await desk.busy(nudge.member.user_id, now=now):
                continue
            if await desk.notify(
                nudge.member.user_id,
                nudge.text,
                now=now,
                steer=nudge.steer,
                media_object_key=nudge.media,
            ):
                told.add(uid)
                await redis.set(nudge.key, "1", ex=_KEEP_SECONDS)
                if nudge.key.startswith("dating:ask:"):
                    await redis.set(
                        _ASKED_KEY.format(silent=nudge.member.profile_id),
                        "1",
                        ex=ASK_SPACING_SECONDS,
                    )
                journal("dating_nudge", uid, text=nudge.text[:80])
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
