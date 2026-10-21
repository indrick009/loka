"""Tell a tenant his 1 000 FCFA withdrawal did not go through.

A failed payment is not a dead end: nothing was debited, so the honest move is
to say so and put him back on the step that reads his retry. The ``_unlock``
pair is kept, so "oui" re-asks the provider for the same lodging, and an
``AWAITING_PAYMENT`` that never settles is released back to ``CONFIRM_PAYMENT``
instead of being left waiting on a callback that will not come.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog

from loka.bounded_contexts.ai.application.services.unlock_copy import (
    PAYMENT_QUESTION_KEY,
    unlock_text,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork

# Only these two steps wait on the provider; anyone else has moved on and is
# not pulled back into a payment he has left.
_PAYMENT_STEPS = (FlowStep.CONFIRM_PAYMENT, FlowStep.AWAITING_PAYMENT)


class PresentPaymentFailureUseCase:
    name = "present_payment_failure"

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

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        correlation_id: str,
        now: datetime,
    ) -> bool:
        """``False`` when there is no thread, or nobody waiting on the payment."""
        session = await self._sessions.latest_by_user(tenant_id)
        if session is None:
            return False
        if session.step not in _PAYMENT_STEPS:
            self._logger.info(
                "payment_failure_ignored",
                session_id=str(session.id),
                step=session.step.value,
            )
            return True

        observed = session.revision
        session.transition(FlowStep.CONFIRM_PAYMENT, now=now)
        session.record_pending_question(PAYMENT_QUESTION_KEY)
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
                    question_key=PAYMENT_QUESTION_KEY,
                    text=unlock_text("failed", session.language, phone=session.phone.e164),
                    intent="PAYMENT_FAILED",
                    language=session.language,
                )
            ],
            correlation_id=correlation_id,
        )
        await self._uow.commit()
        self._logger.info("payment_failure_notified", session_id=str(session.id))
        return True
