"""Write one turn: record it, let its domain act, move the session, answer."""

from __future__ import annotations

import re
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
from loka.bounded_contexts.conversation.application.services.prompts.state import (
    public_known_facts,
)
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
from loka.shared.infrastructure.conversation_log import journal

# Steps where Kora waits for what comes next rather than for a free text: a
# question asked there is an aside, answered without redoing the task. Never
# COLLECT_CHOICE or AWAITING_REVIEW: Dating relays and Found proofs read the
# raw text there.
RESTING_STEPS = frozenset(
    {
        FlowStep.PUBLISHED,
        FlowStep.PRESENT_RESULTS,
        FlowStep.COLLECT_PROPERTY_CHOICE,
        FlowStep.AWAITING_MATCH,
        FlowStep.AWAITING_AVAILABILITY,
        FlowStep.DONE,
    }
)

# Where the user said he is before any task, read by the task that follows.
SHARED_PLACE = "place"

# Shorter than this, what the model said before its question is a « Super ! »,
# not an explanation worth keeping.
MIN_EXPLANATION_CHARS = 60
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def explanation_in(reply_text: str | None) -> str | None:
    """What the model explained, without the questions it asked after it."""
    sentences = _SENTENCE_END.split((reply_text or "").strip())
    told = " ".join(s for s in sentences if not s.rstrip().endswith("?")).strip()
    return told if len(told) >= MIN_EXPLANATION_CHARS else None


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
        asking: str | None = None,
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
        is_aside = self._is_aside(session, verdict, target, reply_text)
        journal(
            "reading", session.phone.e164, intent=verdict.intent, accepted=verdict.accepted,
            reason=verdict.reason, flow=session.flow.value, step=session.step.value,
            facts=verdict.facts.as_context(), aside=is_aside or None,
            model_reply=reply_text, asking=asking, known=public_known_facts(session.context),
        )
        if is_aside:
            # « C'est les seuls ? », « tu as ma commande ? »: the model, which
            # sees the whole state, answers; the task is not run again. It used
            # to resend the list and its photos (replays of 3 Nov 2026).
            aside = TurnOutcome(
                question=carried_question(session),
                next_step=None,
                reply_text=reply_text,
                platform_text=None,
            )
            return await self._answer(
                session,
                verdict,
                cost,
                aside,
                next_step=None,
                text=text,
                media_burst_of=None,
                media_marker=None,
            )
        shared = verdict.facts.as_context().get("location")
        if (
            session.flow is FlowName.SUPPORT
            and verdict.intent == "COLLECT_LOCATION"
            and isinstance(shared, dict)
            and shared.get("city")
        ):
            # A position before any task says where the user is, not that he
            # wants a lodging: kept for the task that follows, and he is asked
            # what he needs (journal of 5 Nov 2026: a pin got « publier ou
            # chercher ? », then the food search asked for it again).
            observed = session.revision
            session.merge_context({SHARED_PLACE: shared}, now=cost.now)
            await self._writer.save(session, observed=observed)
            ask = TurnOutcome(question=None, next_step=None, reply_text=None, platform_text=None)
            return await self._answer(
                session,
                replace(verdict, intent="SUPPORT"),
                cost,
                ask,
                next_step=None,
                text=text,
                media_burst_of=None,
                media_marker=media_marker,
            )
        spec = spec_for(target)
        if spec is not None and spec.name in self._plugs and self._desk is not None:
            outcome = await self._plugged(
                session, verdict, cost, target, text=text, reply_text=reply_text, asking=asking
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

    async def aside(
        self,
        session: ConversationSession,
        verdict: Verdict,
        cost: TurnCost,
        *,
        call: CallUsage | None,
        reply_text: str,
        text: str | None,
    ) -> AnalysisReport:
        """The model's answer to a remark, the task left where it was."""
        await self._ledger.record(session, intent=verdict.intent, cost=cost, call=call)
        outcome = TurnOutcome(
            question=carried_question(session),
            next_step=None,
            reply_text=reply_text,
            platform_text=None,
        )
        return await self._answer(
            session, verdict, cost, outcome, next_step=None, text=text,
            media_burst_of=None, media_marker=None,
        )

    @staticmethod
    def _is_aside(
        session: ConversationSession,
        verdict: Verdict,
        target: FlowName,
        reply_text: str | None,
    ) -> bool:
        return (
            verdict.intent == "SUPPORT"
            and bool(reply_text)
            and target is session.flow
            and session.step in RESTING_STEPS
            and not verdict.facts.as_context()
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
        asking: str | None,
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
        where = session.context.get(SHARED_PLACE)
        if fresh and isinstance(where, dict) and any(f.name == "place" for f in spec.form(flow)):
            base = {"place": where}
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
        if outcome.asks and reply_text and asking == outcome.asks and asking not in facts:
            # The domain decides what is asked, the model how: its sentence,
            # which reacts to what the user said, asks the very field decided.
            # The stock question used to replace it on every turn (journal of
            # 5 Nov 2026: « D'accord Indrick, c'est noté. Quel est ton âge ? »
            # went out as « Quel âge as-tu ? »).
            # Said once: a model that wrote the notice itself is not doubled.
            notice = outcome.notice
            if notice and notice[:40] in reply_text:
                notice = None
            said = f"{reply_text}\n\n{notice}" if notice else reply_text
            return replace(outcome, reply_text=said, platform_text=None)
        told = explanation_in(reply_text) if fresh else None
        if told and outcome.platform_text and outcome.media_object_key is None:
            # « How dating work? »: the model explained, then the domain's first
            # question replaced it (journal of 5 Nov 2026). The explanation goes
            # first, the domain's exact question stays last.
            return replace(outcome, reply_text=f"{told}\n\n{outcome.platform_text}")
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
