"""Write one turn: record it, let its domain act, move the session, answer."""

from __future__ import annotations

import uuid

from loka.bounded_contexts.conversation.application.services.prompts import collapse_repeats
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.housing.turns import (  # noqa: E501
    HousingTurns,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    AnalysisReport,
    TurnCost,
    Verdict,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    CallUsage,
    TurnLedger,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.shared.application.unit_of_work import UnitOfWork


class TurnApplier:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        ledger: TurnLedger,
        writer: SessionWriter,
        housing: HousingTurns,
    ) -> None:
        self._uow = uow
        self._ledger = ledger
        self._writer = writer
        self._housing = housing

    async def apply(
        self,
        session: ConversationSession,
        verdict: Verdict,
        cost: TurnCost,
        *,
        call: CallUsage | None = None,
        reply_text: str | None = None,
        text: str | None = None,
        media_burst_of: uuid.UUID | None = None,
        media_marker: str | None = None,
    ) -> AnalysisReport:
        """Record the turn, move the conversation, and stage the answer.

        A rejected outcome is still recorded, so "the model was unsure" stays
        measurable instead of being silently converted into a wrong listing.
        """
        await self._ledger.record(session, intent=verdict.intent, cost=cost, call=call)
        # The model's sentence is swept for decoding loops here: the platform
        # copy that may replace it needs no such handling.
        reply_text = collapse_repeats(reply_text) if reply_text else reply_text

        settled = await self._housing.settle(
            session, text=text, intent=verdict.intent, now=cost.now
        )
        if settled is not None:
            return await self._writer.close(session, settled, cost)

        outcome = (
            await self._housing.apply(
                session, verdict, text=text, reply_text=reply_text, now=cost.now
            )
            if verdict.accepted
            else self._housing.refused(session, intent=verdict.intent, reply_text=reply_text)
        )
        next_step = outcome.next_step if verdict.accepted else verdict.next_step

        # Committed with the turn: an analysis the platform understood but never
        # answered is a landlord staring at a silent thread, and no retry can
        # notice a reply that was never staged.
        self._writer.stage_reply(
            session,
            intent=verdict.intent,
            reason=verdict.reason,
            question_key=outcome.question,
            text=outcome.reply_text,
            now=cost.now,
            # Stock copy goes out in the user's voice; a recap to confirm and
            # the model's own sentence go out as they are. A photo's caption
            # may be empty, and its acknowledgement is stock copy too, as is
            # the fast lane's template ("1", "60mil", "6").
            answering=(
                (text or media_marker)
                if media_marker is not None
                else text
                if (outcome.platform_text and outcome.reply_text == outcome.platform_text)
                or outcome.reply_text is None
                else None
            ),
            media_burst_of=media_burst_of,
        )
        await self._uow.commit()
        return AnalysisReport(
            session_id=session.id,
            message_id=cost.message_id,
            intent=verdict.intent,
            confidence=cost.confidence,
            used_llm=cost.used_llm,
            accepted=verdict.accepted,
            reason=verdict.reason,
            facts=verdict.facts.as_context() if verdict.accepted else {},
            rejected_facts=dict(verdict.facts.rejected),
            next_step=next_step.value if next_step else None,
            question_key=outcome.question,
            cost_usd=cost.cost_usd,
        )
