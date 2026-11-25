"""Maintenance: idia answers nobody, and says so once.

Switched on and off by hand, without a deploy (decision of 26 Nov 2026):

    redis-cli SET kora:maintenance 1     # on
    redis-cli DEL kora:maintenance       # off

While on, a message gets « je te reviens dans un instant » — once per
conversation every ``TOLD_FOR_SECONDS`` — and nothing else; a message that only
closes the exchange (« ok », « merci ») gets nothing at all. Every message kept
unanswered is listed under ``PENDING_KEY``; once the switch is off, the
worker reads them again by itself (``replay_forever``): per conversation, every
photo or video and the last written message — the thread gives the model the
rest, and five « ?? » do not get five answers. The admin numbers go through,
to test.

A desk that cannot read Redis lets the message through.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.services.closing import is_closing
from loka.bounded_contexts.conversation.domain.events.message_events import (
    ConversationAnalysisRequested,
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.messaging_models import (
    InboundMessageRow,
)
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

SWITCH_KEY = "kora:maintenance"
PENDING_KEY = "kora:maintenance:pending"
_TOLD_KEY = "kora:maintenance:told:{session}"
TOLD_FOR_SECONDS = 30 * 60
_TEXT = {
    "fr": "Je te reviens dans un instant 🙏🏾",
    "en": "I'll get back to you in a moment 🙏🏾",
}


async def under_maintenance(redis: Redis) -> bool:
    try:
        return bool(await redis.exists(SWITCH_KEY))
    except Exception as exc:  # see the module docstring
        _logger.warning("maintenance_switch_unreadable", error=type(exc).__name__)
        return False


class MaintenanceDesk:
    def __init__(self, database: Database, redis: Redis, settings: Settings) -> None:
        self._database = database
        self._redis = redis
        self._admins = {
            re.sub(r"\D", "", p)[-9:]
            for p in settings.whatsapp.alert_phones.split(",")
            if p.strip()
        }

    async def handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """Whether the message was kept unanswered for the maintenance."""
        if not await under_maintenance(self._redis):
            return False
        try:
            return await self._handle(message_id, session_id)
        except Exception as exc:  # see the module docstring
            _logger.warning("maintenance_desk_failed", error=type(exc).__name__)
            return False

    async def _handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        async with unit_of_work(self._database) as uow:
            message = await SqlAlchemyInboundMessageRepository(uow.session).get_by_id(message_id)
            session = await SqlAlchemyConversationSessionRepository(uow.session).get_by_id(
                session_id
            )
            if message is None or session is None:
                return False
            phone = session.phone.e164
            if re.sub(r"\D", "", phone)[-9:] in self._admins:
                return False
            await self._redis.rpush(PENDING_KEY, f"{message_id} {session_id}")
            told = await self._redis.set(
                _TOLD_KEY.format(session=session_id), "1", ex=TOLD_FOR_SECONDS, nx=True
            )
            if is_closing(message.text) or not told:
                journal("maintenance", phone, told=False)
                return True
            now = datetime.now(UTC)
            language = "en" if (session.language or "").startswith("en") else "fr"
            uow.events.stage(
                [
                    WhatsAppMessageSendRequested(
                        aggregate_type="ConversationSession",
                        aggregate_id=session.id,
                        aggregate_version=session.revision,
                        occurred_at=now,
                        session_id=session.id,
                        recipient_phone=phone,
                        text=_TEXT[language],
                        language=session.language,
                    )
                ],
                correlation_id=f"maintenance-{message_id}",
            )
            await uow.commit()
            journal("maintenance", phone, told=True)
            return True


REPLAY_EVERY_SECONDS = 20


async def replay_once(database: Database, redis: Redis) -> int:
    """Hand the kept messages back to the analysis, once maintenance is off."""
    if await under_maintenance(redis) or not await redis.llen(PENDING_KEY):
        return 0
    entries = [str(entry) for entry in await redis.lrange(PENDING_KEY, 0, -1)]
    await redis.ltrim(PENDING_KEY, len(entries), -1)
    by_session: dict[str, list[uuid.UUID]] = {}
    for entry in entries:
        message, _, session = entry.partition(" ")
        by_session.setdefault(session, []).append(uuid.UUID(message))
    staged = 0
    async with unit_of_work(database) as uow:
        for ids in by_session.values():
            found = [await uow.session.get(InboundMessageRow, i) for i in ids]
            rows = [row for row in found if row is not None]
            texts = [row for row in rows if row.media_object_key is None]
            wanted = [row for row in rows if row.media_object_key is not None] + texts[-1:]
            for row in sorted(wanted, key=lambda r: r.received_at):
                uow.events.stage(
                    [
                        ConversationAnalysisRequested(
                            aggregate_type="InboundMessage",
                            aggregate_id=row.id,
                            aggregate_version=2,
                            occurred_at=datetime.now(UTC),
                            message_id=row.id,
                            session_id=row.session_id,
                            sender_user_id=row.sender_user_id,
                            provider=row.provider,
                            message_kind=row.message_kind,
                        )
                    ],
                    correlation_id=f"maintenance-replay-{row.id}",
                )
                staged += 1
                journal("maintenance_replayed", row.sender_phone, kind=row.message_kind)
        await uow.commit()
    return staged


async def replay_forever(database: Database, redis: Redis) -> None:
    while True:
        try:
            await replay_once(database, redis)
        except Exception as exc:  # the list stays; the next pass tries again
            _logger.warning("maintenance_replay_failed", error=type(exc).__name__)
        await asyncio.sleep(REPLAY_EVERY_SECONDS)
