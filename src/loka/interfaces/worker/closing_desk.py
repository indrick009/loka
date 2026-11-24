"""Before a message is read: a bare « ok » that closes the exchange gets no answer.

Only when nothing awaits an answer: no open question, and the task at rest.
An « ok » to « réponds OUI ou NON » is an answer and goes on to be read.
A desk that fails lets the message through: never a silence because of it.
"""

from __future__ import annotations

import uuid

from loka.bounded_contexts.conversation.application.services.closing import is_closing
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_applier import (  # noqa: E501
    RESTING_STEPS,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.infrastructure.persistence.conversation_session_repository import (  # noqa: E501
    SqlAlchemyConversationSessionRepository,
)
from loka.bounded_contexts.conversation.infrastructure.persistence.inbound_message_repository import (  # noqa: E501
    SqlAlchemyInboundMessageRepository,
)
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


class ClosingDesk:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        """Whether the message only closed the exchange and was left unanswered."""
        try:
            return await self._handle(message_id, session_id)
        except Exception as exc:  # see the module docstring
            _logger.warning("closing_desk_failed", error=type(exc).__name__)
            return False

    async def _handle(self, message_id: uuid.UUID, session_id: uuid.UUID) -> bool:
        async with unit_of_work(self._database) as uow:
            inbound = SqlAlchemyInboundMessageRepository(uow.session)
            message = await inbound.get_by_id(message_id)
            session = await SqlAlchemyConversationSessionRepository(uow.session).get_by_id(
                session_id
            )
            if message is None or session is None or not is_closing(message.text):
                return False
            if session.context.get("next_question"):
                return False
            if session.flow is not FlowName.SUPPORT and session.step not in RESTING_STEPS:
                return False
            await inbound.mark_processed(message_id)
            await uow.commit()
            journal("closing", session.phone.e164, text=message.text)
            return True
