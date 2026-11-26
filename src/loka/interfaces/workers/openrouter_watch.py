"""Watch OpenRouter, idia's backup behind the shared CLIProxyAPI.

The subscription answers first; OpenRouter takes over when it fails. A backup
that is down or out of credit fails silently until the day it is needed, so it
is checked every ``CHECK_EVERY_SECONDS``: unreachable, or under
``openrouter_min_credit_usd`` left, and the admin is told on WhatsApp — once
every ``ALERT_EVERY_SECONDS`` (decision of 26 Nov 2026).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

ALERT_PHONE = "+237692617975"
CHECK_EVERY_SECONDS = 30 * 60
ALERT_EVERY_SECONDS = 6 * 3600
_ALERTED_KEY = "openrouter:alerted"
_CREDITS_URL = "https://openrouter.ai/api/v1/credits"


async def problem(key: str, minimum: float) -> str | None:
    """What is wrong with OpenRouter, or ``None`` when it can take over."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(_CREDITS_URL, headers={"Authorization": f"Bearer {key}"})
        response.raise_for_status()
        data = response.json()["data"]
        left = float(data["total_credits"]) - float(data["total_usage"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        return f"OpenRouter ne répond pas ({type(exc).__name__})"
    if left < minimum:
        return f"il reste {left:.2f} $ de crédit OpenRouter"
    return None


async def check_once(database: Database, redis: Redis, settings: Settings) -> str | None:
    key = settings.ai.openrouter_watch_key.get_secret_value()
    if not key:
        return None
    wrong = await problem(key, settings.ai.openrouter_min_credit_usd)
    if wrong is None or not await redis.set(_ALERTED_KEY, "1", ex=ALERT_EVERY_SECONDS, nx=True):
        return wrong
    text = (
        f"⚠️ idia : {wrong}. OpenRouter est le secours derrière ChatGPT : "
        "s'il tombe aussi, idia ne pourra plus répondre. Recharge le compte OpenRouter."
    )
    async with unit_of_work(database) as uow:
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        sent = await desk.notify_phone(ALERT_PHONE, text, now=datetime.now(UTC))
        await uow.commit()
    journal("openrouter_alert", ALERT_PHONE, problem=wrong, sent=sent)
    return wrong


async def watch_forever(database: Database, settings: Settings) -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    while True:
        try:
            await check_once(database, redis, settings)
        except Exception as exc:  # a failed check is tried again next time
            _logger.warning("openrouter_watch_failed", error=type(exc).__name__)
        await asyncio.sleep(CHECK_EVERY_SECONDS)
