"""Write one turn: record it, let its domain act, move the session, answer."""

from __future__ import annotations

import uuid
from dataclasses import replace

from loka.bounded_contexts.conversation.application.plugs.registry import (
    HOLDING_QUESTIONS,
    spec_for,
)
from loka.bounded_contexts.conversation.application.plugs.turn import (
    DomainTurns,
    PlugDesk,
    PlugTurn,
)
from loka.bounded_contexts.conversation.application.services.prompts import collapse_repeats
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.flow_rules import (  # noqa: E501
    carried_question,
    opens_new_flow,
    target_flow,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    AnalysisReport,
    TurnCost,
    TurnOutcome,
    Verdict,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    CallUsage,
    TurnLedger,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.housing.conversation.turns.turns import (
    HousingTurns,
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
        plugs: dict[str, DomainTurns] | None = None,
        desk: PlugDesk | None = None,
    ) -> None:
        self._uow = uow
        self._ledger = ledger
        self._writer = writer
        self._housing = housing
        self._plugs = plugs or {}
        self._desk = desk

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

        target = target_flow(session, verdict.intent)
        spec = spec_for(target)
        if spec is not None and spec.name in self._plugs and self._desk is not None:
            outcome = await self._plugged(
                session, verdict, cost, target, text=text, reply_text=reply_text
            )
            return await self._answer(
                session,
                verdict,
                cost,
                outcome,
                next_step=outcome.next_step,
                text=text,
                media_burst_of=media_burst_of,
                # A domain's exact copy stays exact after a photo too: observed
                # live, Dating's « le plus important : ta description… » was
                # rewritten into a casual line that dropped what mattered.
                media_marker=media_marker if outcome.platform_text is not None else None,
            )

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
        return await self._answer(
            session,
            verdict,
            cost,
            outcome,
            next_step=next_step,
            text=text,
            media_burst_of=media_burst_of,
            media_marker=media_marker,
        )

    async def _plugged(
        self,
        session: ConversationSession,
        verdict: Verdict,
        cost: TurnCost,
        flow: FlowName,
        *,
        text: str | None,
        reply_text: str | None,
    ) -> TurnOutcome:
        """Hand the turn to the domain that owns its flow."""
        spec = spec_for(flow)
        assert spec is not None and self._desk is not None
        fresh = opens_new_flow(session, verdict.intent) or session.flow is not flow
        base = (
            {}
            if fresh
            else {name: value for name, value in session.context.items() if name != "next_question"}
        )
        facts = verdict.facts.as_context() if verdict.accepted else {}
        asked = carried_question(session)
        turn = PlugTurn(
            session=session,
            flow=flow,
            intent=verdict.intent,
            facts=facts,
            known={**base, **facts},
            fresh=fresh,
            text=text,
            now=cost.now,
            model_reply=reply_text,
            open_field=spec.field_of(asked),
            rejected=dict(verdict.facts.rejected),
        )
        outcome = await self._plugs[spec.name].apply(turn, self._desk)
        if (
            verdict.intent == "SUPPORT"
            and reply_text
            and not facts
            and outcome.question == asked
            and asked not in HOLDING_QUESTIONS
        ):
            # Small talk mid-form: the model's own answer, which was told the
            # open question and comes back to it, beats the bare question. Only
            # then: observed live, « oui envoie moi sa photo » recorded a yes in
            # Dating, and the model's guess replaced Kora's answer to it.
            return replace(outcome, reply_text=reply_text, platform_text=None)
        return outcome

    async def _answer(
        self,
        session: ConversationSession,
        verdict: Verdict,
        cost: TurnCost,
        outcome: TurnOutcome,
        *,
        next_step: FlowStep | None,
        text: str | None,
        media_burst_of: uuid.UUID | None,
        media_marker: str | None,
    ) -> AnalysisReport:
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
            media_object_key=outcome.media_object_key,
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
