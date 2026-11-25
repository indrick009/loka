"""Each morning, a restaurant whose menu of the day is not out is reminded.

« Même chose » brings yesterday's menu back (``same_menu``). Once a day per
restaurant, between 07:00 and 11:00 in Douala, and never into a conversation
under way: a busy owner is tried again at the next beat, until 11:00.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

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
from loka.bounded_contexts.food.conversation import texts
from loka.bounded_contexts.food.conversation.spec import FOOD
from loka.bounded_contexts.food.domain.menu import DOUALA, menu_day
from loka.bounded_contexts.food.infrastructure.store import SqlAlchemyFoodStore
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 900.0
FIRST_HOUR, LAST_HOUR = 7, 11
_KEY = "food:menu-reminder:{restaurant}:{day}"


async def remind_once(database: Database, redis: Redis, *, now: datetime) -> int:
    local = now.astimezone(DOUALA)
    if not FIRST_HOUR <= local.hour < LAST_HOUR:
        return 0
    day = menu_day(now)
    sent = 0
    async with unit_of_work(database) as uow:
        store = SqlAlchemyFoodStore(uow.session)
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        for restaurant in await store.restaurants():
            key = _KEY.format(restaurant=restaurant.restaurant_id, day=day.isoformat())
            if await redis.exists(key):
                continue
            if await store.menu_of(restaurant.restaurant_id, day) is not None:
                continue
            if await desk.busy(restaurant.owner_user_id, now=now):
                continue
            last = await store.last_menu(restaurant.restaurant_id)
            told = await desk.notify(
                restaurant.owner_user_id,
                texts.morning_reminder(restaurant.name, last.dishes if last else []),
                now=now,
                steer=Steer(
                    flow=FlowName.FOOD_RESTAURANT,
                    step=FlowStep.COLLECT_FIELD,
                    question_key=FOOD.question_key("dishes"),
                ),
            )
            await redis.set(key, "1", ex=2 * 86400)
            sent += int(told)
        await uow.commit()
    if sent:
        _logger.info("menu_reminders_sent", count=sent)
    return sent


async def sweep_forever(
    database: Database, settings: Settings, *, interval: float = SWEEP_INTERVAL_SECONDS
) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    while True:
        try:
            await remind_once(database, redis, now=datetime.now(UTC))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - resilience path
            _logger.exception("menu_reminders_failed", error=str(exc))
        await asyncio.sleep(interval)
