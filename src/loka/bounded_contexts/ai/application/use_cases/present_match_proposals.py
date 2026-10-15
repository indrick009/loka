"""Send a tenant the lodgings that answer his demand, and wait for his pick.

Nothing of his triggered this message: a listing went live, or his own search
found something a moment after the thread acknowledged it. So it is staged on
the thread he last used, and his session is moved to the step that reads his
answer — the choice step that leads on to the availability check and the
1 000 FCFA unlock. Without that move his "1" would be read as a fresh message
and the lodgings he was just sent would name nothing.

His session is only taken over while it is still his search. A tenant who has
since started something else — publishing a lodging of his own — still gets
the message, but his form is not torn up to read a digit he may never send.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import structlog

from loka.bounded_contexts.ai.application.services.match_proposals import (
    SHOWN_LISTINGS_KEY,
    match_proposals_text,
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

CHOICE_QUESTION_KEY = "ask.search_choice"

_CLOSED = (SessionStatus.COMPLETED, SessionStatus.ABANDONED)


class PresentMatchProposalsUseCase:
    name = "present_match_proposals"

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
        proposals: Sequence[Mapping[str, Any]],
        correlation_id: str,
        now: datetime,
    ) -> bool:
        """``False`` when there is nobody to write to or nothing to show."""
        shown = [proposal for proposal in proposals if proposal.get("property_id")]
        session = await self._sessions.latest_by_user(tenant_id)
        if session is None or not shown:
            return False

        if session.flow is FlowName.PROPERTY_SEARCH and session.status not in _CLOSED:
            observed = session.revision
            session.transition(
                FlowStep.COLLECT_PROPERTY_CHOICE,
                now=now,
                context_patch={
                    SHOWN_LISTINGS_KEY: [str(proposal["property_id"]) for proposal in shown]
                },
            )
            session.record_pending_question(CHOICE_QUESTION_KEY)
            await self._sessions.save(session, expected_revision=observed)
        else:
            self._logger.warning(
                "match_sent_outside_search",
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
                    reply_kind="QUESTION",
                    question_key=CHOICE_QUESTION_KEY,
                    text=match_proposals_text(shown, session.language),
                    intent="DEMAND_MATCHED",
                    language=session.language,
                )
            ],
            correlation_id=correlation_id,
        )
        await self._uow.commit()
        self._logger.info(
            "match_proposals_sent", session_id=str(session.id), proposals=len(shown)
        )
        return True
