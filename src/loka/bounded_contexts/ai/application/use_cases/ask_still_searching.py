"""Put "cherchez-vous toujours ?" to a tenant whose search has run a month.

The Property context decided the reminder is due; this writes it on his thread
and leaves the question open, so his "oui" or "non" is read against it (see
``demand_reminder``). Nothing is asked of a tenant in the middle of an unlock
— ``TenantBusy`` defers it — nor of one who is filling in another form: his
answer would be read as that form's, and the watch simply lapses a week on.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog

from loka.bounded_contexts.ai.application.services.demand_reminder import (
    STILL_SEARCHING_KEY,
    reminder_text,
)
from loka.bounded_contexts.ai.application.use_cases.present_match_proposals import (
    BUSY_STEPS,
    TenantBusy,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    SessionStatus,
)
from loka.bounded_contexts.messaging.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork

# Flows his answer cannot be misfiled in: his own search, or plain talk.
_FREE_FLOWS = (FlowName.PROPERTY_SEARCH, FlowName.SUPPORT)


class AskStillSearchingUseCase:
    name = "ask_still_searching"

    def __init__(
        self,
        uow: UnitOfWork,
        sessions: ConversationSessionRepository,
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._sessions = sessions
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(self, *, tenant_id: uuid.UUID, correlation_id: str, now: datetime) -> bool:
        """Whether the question was put. Raises ``TenantBusy`` mid-unlock."""
        session = await self._sessions.latest_by_user(tenant_id)
        if session is None:
            return False
        if session.step in BUSY_STEPS:
            raise TenantBusy(str(session.id))
        if session.flow not in _FREE_FLOWS or session.status in (
            SessionStatus.COMPLETED,
            SessionStatus.ABANDONED,
        ):
            self._logger.info(
                "still_searching_not_asked", session_id=str(session.id), flow=session.flow.value
            )
            return False

        observed = session.revision
        # Same step, new revision: an analysis racing this one must not
        # overwrite the open question without noticing.
        session.transition(session.step, now=now)
        session.record_pending_question(STILL_SEARCHING_KEY)
        await self._sessions.save(session, expected_revision=observed)
        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind="QUESTION",
                    question_key=STILL_SEARCHING_KEY,
                    text=reminder_text("ask", session.language),
                    intent="DEMAND_REMINDER",
                    language=session.language,
                )
            ],
            correlation_id=correlation_id,
        )
        await self._uow.commit()
        self._logger.info("still_searching_asked", session_id=str(session.id))
        return True
