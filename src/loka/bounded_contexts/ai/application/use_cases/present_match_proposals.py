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
from datetime import datetime, timedelta
from typing import Any

import structlog

from loka.bounded_contexts.ai.application.services.match_proposals import (
    SHOWN_LISTINGS_KEY,
    match_proposal_caption,
    match_proposal_line,
    match_proposals_text,
)
from loka.bounded_contexts.ai.domain.knowledge.places import nearest_district
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
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

# The gap between the list and each of its photos, in order.
PHOTO_SPACING_SECONDS = 2


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

        events = [
            WhatsAppMessageSendRequested(
                aggregate_type="ConversationSession",
                aggregate_id=session.id,
                aggregate_version=session.revision,
                occurred_at=now,
                session_id=session.id,
                recipient_phone=session.phone.e164,
                reply_kind="QUESTION",
                question_key=CHOICE_QUESTION_KEY,
                text=match_proposals_text(
                    shown, session.language, tenant_district=_tenant_district(session)
                ),
                intent="DEMAND_MATCHED",
                language=session.language,
            )
        ]
        events.extend(self._photo_events(session, shown, now=now))
        self._uow.events.stage(events, correlation_id=correlation_id)
        await self._uow.commit()
        self._logger.info(
            "match_proposals_sent",
            session_id=str(session.id),
            proposals=len(shown),
            photos=len(events) - 1,
        )
        return True

    def _photo_events(
        self,
        session: ConversationSession,
        shown: Sequence[Mapping[str, Any]],
        *,
        now: datetime,
    ) -> list[WhatsAppMessageSendRequested]:
        """One image per stored photo, each captioned with its lodging's number.

        The list is sent first so the tenant reads what was found, then sees it.
        Every photo travels: a landlord who attached five pictures expects five,
        and one picture often cannot tell one lodging from another. The number
        on the picture is the one his reply names, and the outbound layer
        presigns the key — a URL minted here could expire before the send.
        """
        events: list[WhatsAppMessageSendRequested] = []
        position = 0
        for number, proposal in enumerate(shown, start=1):
            raw = proposal.get("photo_object_keys")
            if not isinstance(raw, list):
                continue
            keys = [key for key in raw if isinstance(key, str) and key.strip()]
            if not keys:
                continue
            line = match_proposal_line(proposal, number, session.language)
            for index, key in enumerate(keys, start=1):
                position += 1
                events.append(
                    WhatsAppMessageSendRequested(
                        aggregate_type="ConversationSession",
                        aggregate_id=session.id,
                        aggregate_version=session.revision,
                        occurred_at=now,
                        session_id=session.id,
                        recipient_phone=session.phone.e164,
                        reply_kind="ACK",
                        text=match_proposal_caption(
                            line,
                            index=index,
                            total=len(keys),
                            language=session.language,
                        ),
                        media_object_key=key,
                        intent="DEMAND_MATCHED_PHOTO",
                        language=session.language,
                        # Staged together, these used to race: a photo reached
                        # the tenant before the sentence that numbers it. Each
                        # waits its turn behind the list and the photo before.
                        not_before=now + timedelta(seconds=PHOTO_SPACING_SECONDS * position),
                    )
                )
        return events


def _tenant_district(session: ConversationSession) -> str | None:
    """Where he wants to live: the district he named, else the one his pin is in."""
    location = session.context.get("location")
    if not isinstance(location, dict):
        return None
    named = location.get("neighbourhood")
    if isinstance(named, str) and named.strip():
        return named.strip()
    latitude, longitude = location.get("latitude"), location.get("longitude")
    if isinstance(latitude, int | float) and isinstance(longitude, int | float):
        return nearest_district(latitude, longitude)
    return None
