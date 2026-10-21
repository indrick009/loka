"""Turn a landlord's "le logement est encore disponible ?" answer into a move.

The tenant chose a lodging and is waiting on the owner's word; this is what
that word does to his thread.

*Confirmed* — the offer of the 1 000 FCFA unlock is written, and the session is
moved to ``CONFIRM_PAYMENT`` with the (tenant, listing) pair kept under
``_unlock`` so the next message is read as his answer to the offer and not as a
fresh search.

*Not confirmed* — no fee is asked for a lodging that is gone. He is told so and
returned to the choice step with the list he was shown, or to waiting when
there is no list to choose from.

The session is taken over only while it still waits on this answer. A tenant
who has since started something else keeps his thread; the message would only
tear up a form he is filling in.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog

from loka.bounded_contexts.ai.application.services.match_proposals import (
    SHOWN_LISTINGS_KEY,
)
from loka.bounded_contexts.ai.application.services.unlock_copy import (
    PAYMENT_QUESTION_KEY,
    UNLOCK_KEY,
    unlock_text,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)
from loka.bounded_contexts.messaging.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork

CHOICE_QUESTION_KEY = "ask.search_choice"


class HandleAvailabilityAnswerUseCase:
    name = "handle_availability_answer"

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
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        confirmed: bool,
        correlation_id: str,
        now: datetime,
    ) -> bool:
        """``False`` when there is no thread; ``True`` once the move is made."""
        session = await self._sessions.latest_by_user(tenant_id)
        if session is None:
            return False
        if session.step is not FlowStep.AWAITING_AVAILABILITY:
            # Redelivery, or a tenant who has moved on. His current step stands.
            self._logger.info(
                "availability_answer_ignored",
                session_id=str(session.id),
                step=session.step.value,
            )
            return True

        if confirmed:
            await self._offer(
                session,
                property_id=property_id,
                landlord_id=landlord_id,
                correlation_id=correlation_id,
                now=now,
            )
        else:
            await self._withdraw(session, correlation_id=correlation_id, now=now)
        return True

    async def _offer(
        self,
        session: ConversationSession,
        *,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        correlation_id: str,
        now: datetime,
    ) -> None:
        observed = session.revision
        session.transition(
            FlowStep.CONFIRM_PAYMENT,
            now=now,
            context_patch={
                UNLOCK_KEY: {
                    "property_id": str(property_id),
                    "landlord_id": str(landlord_id),
                }
            },
        )
        session.record_pending_question(PAYMENT_QUESTION_KEY)
        await self._sessions.save(session, expected_revision=observed)
        self._stage(
            session,
            text=unlock_text("offer", session.language, phone=session.phone.e164),
            question_key=PAYMENT_QUESTION_KEY,
            intent="UNLOCK_OFFER",
            correlation_id=correlation_id,
            now=now,
        )
        await self._uow.commit()
        self._logger.info("unlock_offer_sent", session_id=str(session.id))

    async def _withdraw(
        self, session: ConversationSession, *, correlation_id: str, now: datetime
    ) -> None:
        shown = session.context.get(SHOWN_LISTINGS_KEY)
        back_to_choice = isinstance(shown, list) and bool(shown)
        observed = session.revision
        session.transition(
            FlowStep.COLLECT_PROPERTY_CHOICE if back_to_choice else FlowStep.AWAITING_MATCH,
            now=now,
        )
        session.record_pending_question(CHOICE_QUESTION_KEY if back_to_choice else None)
        await self._sessions.save(session, expected_revision=observed)
        self._stage(
            session,
            text=unlock_text("unavailable", session.language),
            question_key=CHOICE_QUESTION_KEY if back_to_choice else None,
            intent="AVAILABILITY_REFUSED",
            correlation_id=correlation_id,
            now=now,
        )
        await self._uow.commit()
        self._logger.info("availability_unavailable_sent", session_id=str(session.id))

    def _stage(
        self,
        session: ConversationSession,
        *,
        text: str,
        question_key: str | None,
        intent: str,
        correlation_id: str,
        now: datetime,
    ) -> None:
        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind="QUESTION" if question_key else "ACK",
                    question_key=question_key,
                    text=text,
                    intent=intent,
                    language=session.language,
                )
            ],
            correlation_id=correlation_id,
        )
