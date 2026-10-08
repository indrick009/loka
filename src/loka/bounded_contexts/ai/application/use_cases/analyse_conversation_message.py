"""Understand one inbound message, at the lowest possible cost.

The pipeline is deliberately boring:

1. Read the message and its session, then **commit** so no transaction is held
   while a network call runs.
2. Try the deterministic rules. A short, unambiguous reply ("oui", "250 000")
   must never cost a model call.
3. Only if the rules abstain, ask the model — and only if the budget allows it.
4. Validate whatever came back through the domain value objects, then write
   the turn, the usage and the session state in one transaction.

The model never decides what happens next: it proposes facts, the code decides
whether they are good enough to move the conversation forward.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    AiUsageLedger,
    BudgetDecision,
    BudgetPolicy,
    ConversationTurn,
    ConversationTurnRepository,
    IntentModel,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import Prompt
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    MIN_ACCEPTED_CONFIDENCE,
    ExtractedFacts,
    FactExtractor,
    is_supported_intent,
    question_key_for,
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
from loka.bounded_contexts.messaging.domain.repositories.inbound_message_repository import (
    InboundMessageRepository,
)
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc
from loka.shared.infrastructure.metrics import (
    ai_bypass_total,
    llm_calls_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_tokens_total,
)

USE_CASE = "analyse_conversation_message"
SUPPORTED_LANGUAGES = frozenset({"fr", "en"})

SYSTEM_PROMPT = (
    "You classify WhatsApp messages for a Cameroonian real-estate platform. "
    "Reply with one JSON object and nothing else. "
    "Never invent a value that is not in the message: use null when unsure. "
    "Amounts are in XAF. Numbers must be plain integers."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "confidence", "entities"],
    "properties": {
        "intent": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "language": {"type": "string"},
        "entities": {
            "type": "object",
            "properties": {
                "property_type": {"type": "string"},
                "city": {"type": "string"},
                "neighbourhood": {"type": "string"},
                "price": {"type": "integer"},
                "bedrooms": {"type": "integer"},
                "bathrooms": {"type": "integer"},
                "surface_area": {"type": "integer"},
            },
        },
    },
}


@dataclass(frozen=True, slots=True)
class AnalysisCommand:
    message_id: uuid.UUID
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """What the pipeline concluded, for logs, metrics and the caller."""

    session_id: uuid.UUID
    message_id: uuid.UUID
    intent: str
    confidence: float
    used_llm: bool
    accepted: bool
    reason: str | None
    facts: dict[str, Any] = field(default_factory=dict)
    rejected_facts: dict[str, str] = field(default_factory=dict)
    next_step: str | None = None
    question_key: str | None = None
    cost_usd: Decimal = Decimal("0")


class AnalyseConversationMessageUseCase:
    name = USE_CASE

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        inbound: InboundMessageRepository,
        sessions: ConversationSessionRepository,
        turns: ConversationTurnRepository,
        usage: AiUsageLedger,
        classifier: DeterministicClassifier | None = None,
        model: IntentModel | None = None,
        extractor: FactExtractor | None = None,
        budget: BudgetPolicy | None = None,
        provider_name: str = "openrouter",
    ) -> None:
        self._uow = uow
        self._inbound = inbound
        self._sessions = sessions
        self._turns = turns
        self._usage = usage
        self._classifier = classifier or DeterministicClassifier()
        self._model = model
        self._extractor = extractor or FactExtractor()
        self._budget = budget
        self._provider = provider_name

    async def execute(self, command: AnalysisCommand, *, now: datetime) -> AnalysisReport:
        moment = ensure_utc(now)

        message = await self._inbound.get_by_id(command.message_id)
        session = (
            await self._sessions.get_by_id(command.session_id) if message is not None else None
        )
        if message is None or session is None:
            # Nothing was written yet, and inventing a session would be worse
            # than admitting the message is gone.
            return AnalysisReport(
                session_id=command.session_id,
                message_id=command.message_id,
                intent="UNKNOWN",
                confidence=0.0,
                used_llm=False,
                accepted=False,
                reason="message_not_found" if message is None else "session_not_found",
            )

        history = await self._turns.recent(session.id, limit=5)
        prompt = build_analysis_prompt(
            text=message.text, language=session.language, history=history
        )

        # Nothing has been written yet, so releasing the transaction costs
        # nothing and the model call never holds a database connection.
        await self._uow.commit()

        match = self._classifier.classify(message.text or "")
        if match is not None:
            ai_bypass_total.labels(stage="regex").inc()
            return await self._apply(
                session=session,
                message_id=command.message_id,
                intent=match.intent,
                confidence=match.confidence,
                entities=dict(match.entities),
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=moment,
            )

        ai_bypass_total.labels(stage="budget" if self._model is None else "llm").inc()

        if self._model is None:
            return await self._apply(
                session=session,
                message_id=command.message_id,
                intent="UNKNOWN",
                confidence=0.0,
                entities={},
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=moment,
                reason="no_model_configured",
            )

        decision = await self._budget_decision(session.user_id, moment)
        if not decision.allowed:
            return await self._apply(
                session=session,
                message_id=command.message_id,
                intent="UNKNOWN",
                confidence=0.0,
                entities={},
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=moment,
                reason=decision.reason,
            )

        model = self._model
        result = await model.understand_intent(prompt)
        llm_usage = result.usage
        model_name = llm_usage.model if llm_usage is not None else model.model
        input_tokens = llm_usage.input_tokens if llm_usage is not None else 0
        output_tokens = llm_usage.output_tokens if llm_usage is not None else 0
        latency_ms = llm_usage.latency_ms if llm_usage is not None else 0
        cost_usd = (
            Decimal(str(llm_usage.estimated_cost_usd))
            if llm_usage is not None
            else Decimal("0")
        )

        llm_calls_total.labels(use_case=USE_CASE, model=model_name, outcome="ok").inc()
        llm_tokens_total.labels(model=model_name, direction="input").inc(input_tokens)
        llm_tokens_total.labels(model=model_name, direction="output").inc(output_tokens)
        llm_latency_seconds.labels(use_case=USE_CASE, model=model_name).observe(
            latency_ms / 1000
        )
        if cost_usd > 0:
            llm_cost_usd_total.labels(use_case=USE_CASE).inc(float(cost_usd))

        return await self._apply(
            session=session,
            message_id=command.message_id,
            intent=result.intent,
            confidence=result.confidence,
            entities=dict(result.entities),
            used_llm=True,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            now=moment,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _apply(
        self,
        *,
        session: ConversationSession,
        message_id: uuid.UUID,
        intent: str,
        confidence: float,
        entities: dict[str, Any],
        used_llm: bool,
        latency_ms: int,
        cost_usd: Decimal,
        now: datetime,
        reason: str | None = None,
        model_name: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> AnalysisReport:
        """Validate, record, and only then move the conversation.

        Extraction runs even for a rejected outcome: knowing *what* the model
        proposed and which value objects refused it is what makes a bad answer
        debuggable instead of mysterious.
        """
        if not is_supported_intent(intent):
            intent, reason = "UNKNOWN", reason or "unsupported_intent"
        if reason is None and confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = "low_confidence"

        facts = self._extractor.validate(entities)
        next_step = (
            self._extractor.next_step(intent, facts, flow=session.flow)
            if reason is None
            else None
        )
        accepted = reason is None

        await self._turns.add(
            ConversationTurn(
                session_id=session.id,
                message_id=message_id,
                direction="INBOUND",
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                occurred_at=now,
            )
        )
        if used_llm:
            await self._usage.append(
                UsageEntry(
                    use_case=USE_CASE,
                    provider=self._provider,
                    model=model_name or "unknown",
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost_usd,
                    user_id=session.user_id,
                    conversation_id=session.id,
                    correlation_id=None,
                    occurred_at=now,
                )
            )

        if accepted:
            await self._advance_session(session, intent, facts, next_step, now=now)

        # Committed with the turn: an analysis the platform understood but never
        # answered is a landlord staring at a silent thread, and no retry can
        # notice a reply that was never staged.
        question = question_key_for(next_step) if accepted else None
        await self._stage_reply(
            session, intent=intent, reason=reason, question_key=question, now=now
        )

        await self._uow.commit()
        return AnalysisReport(
            session_id=session.id,
            message_id=message_id,
            intent=intent,
            confidence=confidence,
            used_llm=used_llm,
            accepted=accepted,
            reason=reason,
            facts=facts.as_context() if accepted else {},
            rejected_facts=dict(facts.rejected),
            next_step=next_step.value if next_step else None,
            question_key=question,
            cost_usd=cost_usd,
        )

    async def _stage_reply(
        self,
        session: ConversationSession,
        *,
        intent: str,
        reason: str | None,
        question_key: str | None,
        now: datetime,
    ) -> None:
        """Request the one reply this turn owes the user.

        Which question comes next is decided above; this only records that an
        answer is due and what kind it is. The copy itself belongs to the
        outbound layer, so rewording a question never touches the model or the
        conversation state.
        """
        self._uow.events.stage(
            [
                WhatsAppMessageSendRequested(
                    aggregate_type="ConversationSession",
                    aggregate_id=session.id,
                    aggregate_version=session.revision,
                    occurred_at=now,
                    session_id=session.id,
                    recipient_phone=session.phone.e164,
                    reply_kind=_reply_kind(reason=reason, question_key=question_key),
                    question_key=question_key,
                    language=session.language,
                )
            ],
            correlation_id=current_context().get("correlation_id") or "unknown",
        )

    async def _advance_session(
        self,
        session: ConversationSession,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        *,
        now: datetime,
    ) -> None:
        """Open the flow if needed, merge the facts, move to the chosen step.

        ``expected_revision`` is the revision read at load time, so if another
        worker moved this session meanwhile the save is refused and the analysis
        is discarded instead of overwriting a concurrent step.
        """
        observed = session.revision

        opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
        if opening_flow is not None and session.flow is not opening_flow:
            # Resets the context, so it must come first: the facts about to be
            # merged belong to this new attempt, not to the abandoned one.
            session.start_flow(opening_flow, now=now, first_step=next_step or FlowStep.START)

        # Transition before merging: `transition` compares expected_revision
        # against the current revision, and merging first would bump it and make
        # every save look like a conflict.
        if next_step is not None:
            session.transition(next_step, now=now, expected_revision=session.revision)
        session.merge_context(facts.as_context(), now=now)
        question = question_key_for(next_step)
        if question:
            session.context["next_question"] = question
        await self._sessions.save(session, expected_revision=observed)

    async def _budget_decision(
        self, user_id: uuid.UUID | None, now: datetime
    ) -> BudgetDecision:
        if self._budget is None:
            return BudgetDecision(allowed=True)
        today = now.date()
        global_spend = await self._usage.total_cost_usd(on=today)
        user_spend = (
            await self._usage.total_cost_usd(on=today, user_id=user_id)
            if user_id is not None
            else Decimal("0")
        )
        return self._budget.evaluate(
            global_spend=global_spend,
            user_spend=user_spend,
            has_user=user_id is not None,
        )


def _reply_kind(*, reason: str | None, question_key: str | None) -> str:
    """What kind of answer this turn owes the user.

    Three cases, decided here so the outbound layer never has to re-derive
    whether an analysis succeeded. ``reason`` is what the pipeline refused:
    answering a question when the message was not understood would move a
    conversation the platform never actually followed.
    """
    if reason is not None:
        return "CLARIFY"
    return "QUESTION" if question_key else "ACK"


def build_analysis_prompt(
    *,
    text: str | None,
    language: str,
    history: list[TurnSummary],
) -> Prompt:
    """Assemble the request, with the recent turns as context.

    The phone number is never part of the prompt: the model has no business
    knowing who it is talking to, and prompts end up in vendor logs.
    """
    return Prompt(
        system=SYSTEM_PROMPT,
        user=json.dumps(
            {
                "message": text or "",
                "language": language if language in SUPPORTED_LANGUAGES else "fr",
                "recent_turns": [
                    {
                        "direction": turn.direction,
                        "intent": turn.intent,
                        "confidence": round(turn.confidence, 2),
                        "used_llm": turn.used_llm,
                    }
                    for turn in history
                ],
                "schema": RESPONSE_SCHEMA,
            },
            ensure_ascii=False,
        ),
        response_schema=RESPONSE_SCHEMA,
    )
