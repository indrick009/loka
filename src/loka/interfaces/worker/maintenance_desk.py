"""Maintenance: idia answers nobody, and says so once.

Switched on and off by hand, without a deploy (decision of 26 Nov 2026):

    redis-cli SET kora:maintenance 1     # on
    redis-cli DEL kora:maintenance       # off

While on, a message gets « je te reviens dans un instant » — once per
conversation every ``TOLD_FOR_SECONDS`` — and nothing else; a message that only
closes the exchange (« ok », « merci ») gets nothing at all. Every message kept
unanswered is listed under ``PENDING_KEY`` to be read again afterwards. The
admin numbers go through, to test.

A desk that cannot read Redis lets the message through.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis

from loka.bounded_contexts.conversation.application.services.closing import is_closing
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
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
