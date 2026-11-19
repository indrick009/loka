"""Before a message is read: the first welcome, the privacy summary, the erasure.

Run by the analysis worker once it holds the conversation lock, before any
model. Three fixed answers (see ``services/privacy`` for the words):

- the very first message of a number gets the welcome, with the consent line.
  A bare « bonjour » is answered by the welcome alone; anything else is then
  read as usual, so « je cherche un studio à Bastos » loses nothing;
- « confidentialité » gets the privacy summary;
- « supprimer mes données » asks for confirmation, and « SUPPRIMER » within ten
  minutes erases the history kept by idia (see ``persistence/erasure``).

A desk that fails lets the message through to the analysis: a user must never
go unanswered because the welcome could not be written.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select

from loka.bounded_contexts.conversation.application.services import privacy
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.erasure import (
    erase_history,
    erase_journal,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.messaging_models import (
    InboundMessageRow,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

_ERASE_PENDING = "erase:{}"
_ERASE_WINDOW_SECONDS = 600


class PrivacyDesk:
    def __init__(self, database: Database, redis: Redis, settings: Settings) -> None:
        self._database = database
        self._redis = redis
        self._settings = settings

    async def handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """Whether the message was fully answered here and needs no analysis."""
        try:
            return await self._handle(message_id, session_id)
        except Exception as exc:  # see the module docstring
            _logger.warning("privacy_desk_failed", error=type(exc).__name__)
            return False

    async def _handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        now = datetime.now(UTC)
        settings = self._settings.privacy
        async with unit_of_work(self._database) as uow:
            inbound = SqlAlchemyInboundMessageRepository(uow.session)
            message = await inbound.get_by_id(message_id)
            session = await SqlAlchemyConversationSessionRepository(uow.session).get_by_id(
                session_id
            )
            if message is None or session is None:
                return False
            text = message.text or ""
            phone = session.phone.e164
            language = session.language
            request = privacy.privacy_request(text, erase_pending=await self._pending(session_id))

            if request == "confirm_erase":
                await self._clear_pending(session_id)
                erased = await erase_history(uow.session, phone=phone, now=now)
                erase_journal(self._settings.conversation_log_path, phone=phone)
                done = privacy.erased(language, contact_email=settings.contact_email)
                self._say(uow, session, done, now)
                await uow.commit()
                _logger.warning("history_erased", session_id=str(session_id), messages=erased)
                return True
            if request == "erase":
                await self._set_pending(session_id)
                self._say(uow, session, privacy.erase_question(language), now)
                await inbound.mark_processed(message_id)
                await uow.commit()
                journal("privacy", phone, request="erase_asked")
                return True
            if request == "policy":
                summary = privacy.policy_summary(
                    language,
                    contact_email=settings.contact_email,
                    policy_url=settings.policy_url,
                    terms_url=settings.terms_url,
                )
                self._say(uow, session, summary, now)
                await inbound.mark_processed(message_id)
                await uow.commit()
                journal("privacy", phone, request="policy")
                return True

            earlier = await uow.session.scalar(
                select(func.count())
                .select_from(InboundMessageRow)
                .where(InboundMessageRow.sender_phone == phone, InboundMessageRow.id != message_id)
            )
            if earlier:
                return False
            welcome = privacy.welcome(
                language, policy_url=settings.policy_url, terms_url=settings.terms_url
            )
            self._say(uow, session, welcome, now)
            alone = privacy.is_bare_greeting(text)
            if alone:
                await inbound.mark_processed(message_id)
            await uow.commit()
            journal("privacy", phone, request="welcome")
            return alone

    @staticmethod
    def _say(uow: UnitOfWork, session: ConversationSession, text: str, now: datetime) -> None:
        uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    actor_id=session.user_id,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    text=text,
                    language=session.language,
                )
            ],
            correlation_id=f"privacy-{session.id}",
        )

    async def _pending(self, session_id: uuid.UUID) -> bool:
        try:
            return bool(await self._redis.get(_ERASE_PENDING.format(session_id)))
        except RedisError:
            return False

    async def _set_pending(self, session_id: uuid.UUID) -> None:
        try:
            await self._redis.set(_ERASE_PENDING.format(session_id), "1", ex=_ERASE_WINDOW_SECONDS)
        except RedisError as exc:
            _logger.warning("privacy_pending_unavailable", error=type(exc).__name__)

    async def _clear_pending(self, session_id: uuid.UUID) -> None:
        try:
            await self._redis.delete(_ERASE_PENDING.format(session_id))
        except RedisError:
            return
