"""Move the session and stage the one reply a turn owes the user."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.flow_rules import (  # noqa: E501
    carried_question,
    facts_worth_carrying,
    opens_new_flow,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    AnalysisReport,
    Settled,
    TurnCost,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)
from loka.bounded_contexts.conversation.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.conversation.domain.repositories.conversation_session_repository import (
    ConversationSessionRepository,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    ExtractedFacts,
    question_to_ask,
)
from loka.bounded_contexts.housing.conversation.texts.negotiation_copy import (
    OFFER_QUESTION_KEY,
)
from loka.bounded_contexts.housing.conversation.texts.unlock_copy import UNLOCK_KEY
from loka.bounded_contexts.housing.conversation.turns.keys import (
    AVAILABILITY_QUESTION_KEY,
    PENDING_MEDIA_KEY,
    is_availability_answer,
)
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork

# How long an acknowledgement waits for the rest of an album.
PHOTO_BURST_SECONDS = 6


def reply_kind(*, reason: str | None, question_key: str | None) -> str:
    """What kind of answer this turn owes the user.

    Three cases, decided here so the outbound layer never has to re-derive
    whether an analysis succeeded. ``reason`` is what the pipeline refused:
    answering a question when the message was not understood would move a
    conversation the platform never actually followed.
    """
    if reason is not None:
        return "CLARIFY"
    return "QUESTION" if question_key else "ACK"


class SessionWriter:
    def __init__(self, uow: UnitOfWork, sessions: ConversationSessionRepository) -> None:
        self._uow = uow
        self._sessions = sessions

    async def save(self, session: ConversationSession, *, observed: int) -> None:
        await self._sessions.save(session, expected_revision=observed)

    def stage_reply(
        self,
        session: ConversationSession,
        *,
        intent: str,
        reason: str | None,
        question_key: str | None,
        now: datetime,
        text: str | None = None,
        answering: str | None = None,
        media_burst_of: uuid.UUID | None = None,
    ) -> None:
        """Request the one reply this turn owes the user.

        Which question comes next is decided by the caller; this only records
        that an answer is due and, when the model wrote one, the sentence to
        send. A missing ``text`` is not a silent thread: the outbound layer
        renders the stable copy for the ``question_key``, the deterministic path.
        """
        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    actor_id=session.user_id,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind=reply_kind(reason=reason, question_key=question_key),
                    question_key=question_key,
                    intent=intent,
                    text=text,
                    language=session.language,
                    answering=answering or None,
                    media_burst_of=media_burst_of,
                    # Long enough for the rest of an album to arrive: only the
                    # last photo of a burst is answered (see the outbound layer).
                    not_before=(
                        now + timedelta(seconds=PHOTO_BURST_SECONDS)
                        if media_burst_of is not None
                        else None
                    ),
                )
            ],
            correlation_id=current_context().get("correlation_id") or "unknown",
        )

    async def close(
        self, session: ConversationSession, settled: Settled, cost: TurnCost
    ) -> AnalysisReport:
        """Move the step, stage its one sentence, and commit.

        Shared by every turn settled by a rule alone: the payment, the offer
        and the month-on reminder. The turn was already recorded; this closes
        it exactly like the ordinary path would, so the session state and the
        sentence cannot describe different steps.
        """
        observed = session.revision
        session.transition(settled.next_step, now=cost.now, expected_revision=observed)
        session.record_pending_question(settled.question_key)
        if settled.clear_unlock:
            session.context.pop(UNLOCK_KEY, None)
        await self._sessions.save(session, expected_revision=observed)
        self.stage_reply(
            session,
            intent=settled.intent,
            reason=None,
            question_key=settled.question_key,
            text=settled.reply,
            now=cost.now,
        )
        await self._uow.commit()
        return AnalysisReport(
            session_id=session.id,
            message_id=cost.message_id,
            intent=settled.intent,
            confidence=cost.confidence,
            used_llm=cost.used_llm,
            accepted=True,
            reason=None,
            next_step=settled.next_step.value,
            question_key=settled.question_key,
            cost_usd=cost.cost_usd,
        )

    async def advance(
        self,
        session: ConversationSession,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        *,
        now: datetime,
        attach_property_id: uuid.UUID | None = None,
    ) -> str | None:
        """Open the flow if needed, merge the facts, move to the chosen step.

        Returns the question this turn asks, which is also the one the session
        records: one decision, one writer, so the two cannot drift apart.

        ``expected_revision`` is the revision read at load time, so if another
        worker moved this session meanwhile the save is refused and the analysis
        is discarded instead of overwriting a concurrent step.
        """
        observed = session.revision
        # Read before ``start_flow`` can clear the context: a question left open
        # by an attempt the user has walked away from must not be asked again.
        pending = carried_question(session)
        # Answered, the availability question is closed and he is back in the
        # form he was filling. Left open, his next photo caption would have
        # been read as a second answer about a lodging already settled.
        resumed_step: FlowStep | None = None
        if pending in (AVAILABILITY_QUESTION_KEY, OFFER_QUESTION_KEY) and is_availability_answer(
            intent
        ):
            pending, resumed_step = None, session.step

        # Read before the flow opens, because opening it clears the context:
        # the facts stated in the same breath as "publier ou chercher ?"
        # belong to the flow about to start.
        carried = (
            facts_worth_carrying(session.context)
            if session.step is FlowStep.CLARIFY_ROLE
            else {}
        )

        opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
        if opening_flow is not None and opens_new_flow(session, intent):
            # Photos sent before he said what they were for belong to the
            # listing he is about to start, not to the context being reset.
            kept_media = session.context.get(PENDING_MEDIA_KEY)
            if kept_media:
                carried = {**carried, PENDING_MEDIA_KEY: kept_media}
            # Resets the context, so it must come first: the facts about to be
            # merged belong to this new attempt, not to the abandoned one.
            session.start_flow(opening_flow, now=now, first_step=next_step or FlowStep.START)
            pending = None

        # Transition before merging: `transition` compares expected_revision
        # against the current revision, and merging first would bump it and make
        # every save look like a conflict.
        if next_step is not None:
            session.transition(next_step, now=now, expected_revision=session.revision)
        # Attached with the listing just saved, so never attached twice.
        released: dict[str, Any] = (
            {PENDING_MEDIA_KEY: []} if attach_property_id is not None else {}
        )
        session.merge_context({**carried, **facts.as_context(), **released}, now=now)
        # Attaching after the baseline is captured keeps the save's
        # expected_revision equal to the value read from the row.
        if attach_property_id is not None:
            session.attach_property(attach_property_id, now=now)
        asked = question_to_ask(
            next_step=next_step or resumed_step, flow=session.flow, pending=pending
        )
        session.record_pending_question(asked)
        await self._sessions.save(session, expected_revision=observed)
        return asked
