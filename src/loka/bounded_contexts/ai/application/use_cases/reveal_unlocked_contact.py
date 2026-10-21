"""Send the tenant what the 1 000 FCFA bought, once the provider has settled.

This is the only place the landlord's number and the lodging's location are
handed over, and it runs only after a ``PaymentSucceeded``. Nothing of the
tenant triggered it — a callback did — so it is staged on the thread he last
used, and his session is moved to ``DONE`` so the unlock is closed rather than
left mid-way.

The pieces come from other contexts through :class:`UnlockedContactDetails`,
which returns ``None`` when the number cannot be assembled in full. A paid
unlock that cannot be resolved is surfaced to the caller as an error, never
answered with a partial number: the tenant paid, so the fail is real and must
be seen, not swallowed.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import structlog

from loka.bounded_contexts.ai.application.ports import UnlockedContactDetails
from loka.bounded_contexts.ai.application.services.unlock_copy import (
    UNLOCK_KEY,
    contact_text,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
    SessionStatus,
)
from loka.bounded_contexts.messaging.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.messaging.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork

_CLOSED = (SessionStatus.COMPLETED, SessionStatus.ABANDONED)


class RevealUnlockedContactUseCase:
    name = "reveal_unlocked_contact"

    def __init__(
        self,
        uow: UnitOfWork,
        sessions: ConversationSessionRepository,
        *,
        contacts: UnlockedContactDetails,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._sessions = sessions
        self._contacts = contacts
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        correlation_id: str,
        now: datetime,
    ) -> bool:
        """``False`` when there is no session or no contact to hand over."""
        session = await self._sessions.latest_by_user(tenant_id)
        if session is None:
            return False

        contact = await self._contacts.for_listing(
            property_id=property_id, landlord_id=landlord_id, language=session.language
        )
        if contact is None:
            return False

        # The contact is sent even if the thread has moved on — he paid for it.
        # Only the session is left alone when it is no longer his search: a
        # tenant who has started something else is not dragged back to DONE.
        if session.flow is FlowName.PROPERTY_SEARCH and session.status not in _CLOSED:
            observed = session.revision
            session.context.pop(UNLOCK_KEY, None)
            session.transition(FlowStep.DONE, now=now)
            session.record_pending_question(None)
            await self._sessions.save(session, expected_revision=observed)
        else:
            self._logger.warning(
                "unlock_revealed_outside_search",
                session_id=str(session.id),
                flow=session.flow.value,
            )

        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind="ACK",
                    question_key=None,
                    text=contact_text(
                        landlord_phone=contact.landlord_phone,
                        lodging=contact.lodging,
                        point=contact.point,
                        language=session.language,
                    ),
                    intent="CONTACT_UNLOCKED",
                    language=session.language,
                )
            ],
            correlation_id=correlation_id,
        )
        await self._uow.commit()
        self._logger.info(
            "unlocked_contact_revealed",
            session_id=str(session.id),
            property_id=str(property_id),
        )
        return True
