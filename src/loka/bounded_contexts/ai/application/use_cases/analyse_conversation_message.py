"""Understand one inbound message, with the conversation as context.

The pipeline is deliberately boring:

1. Read the message and its session, then **commit** so no transaction is held
   while a network call runs.
2. Ask the model first, handing it the state of the conversation — the flow, the
   step, the question the user is answering and the facts already known — so a
   bare "Bastos" is understood as an answer and not as noise.
3. Keep a deterministic fast lane for the replies where a model adds nothing: an
   exact "oui"/"non" and a message whose only content is a price.
4. Validate whatever came back through the domain value objects, then write the
   turn, the usage and the session state in one transaction.

The model never decides what happens next: it proposes facts, the code decides
whether they are good enough to move the conversation forward. When a
``ResponseModel`` is configured the same turn also gets a natural-language
reply; otherwise the outbound layer renders a stable template from the key.
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
    PropertyListing,
    ResponseModel,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.ai.domain.services.ai_provider import LlmUsage, Prompt
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    LISTING_FLOWS,
    MIN_ACCEPTED_CONFIDENCE,
    PROPERTY_REQUIREMENTS,
    SUPPORTED_INTENTS,
    ExtractedFacts,
    FactExtractor,
    is_supported_intent,
    question_key_for,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
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
from loka.shared.domain.errors import DomainError
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
    " You are given the state of the conversation (flow, step, the question the "
    "user is answering, the facts already known, the ordered required_fields, and "
    "the typical price_reference_monthly_xaf). Use them: a short reply like a "
    "neighbourhood or a number is an answer to that question, so set the matching "
    "entity instead of asking again. When 'conversation.awaiting_field' is set, a "
    "short bare answer (a number, 'immédiat', a date) belongs to exactly that "
    "entity and to no other."
    " A bare number given as a rent is in FCFA: '50' means 50000, not 50."
    " Set 'intent' to exactly one value from the 'intent' enum in 'schema' and nothing else. "
    "A greeting or small talk is SUPPORT; use UNKNOWN only when no listed intent "
    "applies at all."
    " Also set 'reply': the next WhatsApp message to send, in the conversation's "
    "language, one to three short sentences of plain text (no markdown, no emoji, "
    "no signature). Acknowledge the message briefly, then ask for the first field "
    "in 'required_fields' that is not already in known_facts; if none is missing, "
    "confirm the summary. If the message was not understood, ask the user to "
    "rephrase instead of posing a question."
)

# The closed set the model may choose from. Exposed in the prompt so the model
# never invents a state the platform does not have: an unsupported intent is
# discarded as untrusted, which would turn a simple "bonjour" into a confusing
# rephrase request.
ALLOWED_INTENTS: tuple[str, ...] = tuple(sorted(SUPPORTED_INTENTS))

REPLY_SYSTEM_PROMPT = (
    "You are Loka, a warm WhatsApp assistant for a Cameroonian real-estate "
    "platform. Write the next message the assistant sends to the user. "
    "Answer in the conversation's language. One to three short sentences, plain "
    "text: no markdown, no emoji, no letter salutation and no signature. "
    "If a question is given, ask it naturally in your own words. "
    "Never invent a listing, a price or a fact that is not in the context, and "
    "never promise anything the platform did not confirm."
)

# A short, provider-neutral gloss for each question, so the reply model knows
# what it is asking for without reading the outbound layer's copy.
QUESTION_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property (apartment, studio, house, ...)",
    "ask.standing": "whether the property is modern or not",
    "ask.location": "the city and neighbourhood",
    "ask.rent": "the monthly rent, in XAF",
    "ask.features": "the number of bedrooms and bathrooms, and the surface in m2",
    "ask.charges": "whether charges are included and, if not, their monthly amount",
    "ask.deposit": "the refundable deposit (caution) the tenant must pay, in XAF",
    "ask.minimum_duration": "the minimum rental duration",
    "ask.availability": "when the property becomes available (a date, or immediately)",
    "ask.conditions": (
        "the entry conditions: the advance in months, the agency or lease fee, "
        "and the tenant type accepted"
    ),
    "ask.confirm_property": "confirmation of the summary before publishing",
    "confirm.published": "confirmation that the property has been published",
}

# What a month of rent costs here, by property type, in XAF. A model with no
# Cameroonian grounding reads a bare "50" as fifty francs; these wide bands anchor
# the magnitude only — they never reject a price the user is sure about. The
# values live in the sourced market knowledge base so the code and the prompt
# share one authority (see ``ai.domain.knowledge.market``).
PRICE_REFERENCES_XAF: dict[str, tuple[int, int]] = {
    name: (band.low_xaf, band.high_xaf) for name, band in market.PRICE_BANDS_XAF.items()
}

# The same question is asked from opposite sides: a landlord lists a property, a
# tenant looks for one. The neutral gloss lets the model default to the tenant
# reading ("le loyer que vous recherchez") even in a listing, which reads as if
# the platform had mixed the two roles up. These overlays fix the wording.
_LANDLORD_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property the landlord is listing",
    "ask.standing": "whether the property the landlord is listing is modern or not",
    "ask.location": "the city and neighbourhood where the property is",
    "ask.rent": "the monthly rent the landlord is asking, in XAF",
    "ask.charges": (
        "whether the charges on the landlord's property are included in the "
        "rent and, if not, their monthly amount"
    ),
    "ask.deposit": (
        "the refundable deposit (caution) the landlord asks of the tenant, in XAF"
    ),
    "ask.minimum_duration": "the minimum duration the landlord wants for the rental",
    "ask.availability": "when the landlord's property becomes available (a date, or immediately)",
    "ask.conditions": (
        "the landlord's entry conditions: the advance in months, the agency or "
        "lease fee, and the tenant type the landlord accepts"
    ),
}
_TENANT_TOPICS: dict[str, str] = {
    "ask.property_type": "the type of property the tenant is looking for",
    "ask.standing": "whether the tenant wants a modern property or not",
    "ask.location": "the city and neighbourhood the tenant wants",
    "ask.rent": "the maximum monthly rent the tenant is willing to pay, in XAF",
}
_FLOW_TOPICS: dict[FlowName, dict[str, str]] = {
    FlowName.PROPERTY_CREATION: _LANDLORD_TOPICS,
    FlowName.PROPERTY_SEARCH: _TENANT_TOPICS,
}
_FLOW_ROLES: dict[FlowName, str] = {
    FlowName.PROPERTY_CREATION: "a landlord listing a property for rent",
    FlowName.PROPERTY_SEARCH: "a tenant looking for a property to rent",
    FlowName.SUPPORT: "a user who needs help with the platform",
}


def _has_listing_facts(context: dict[str, object]) -> bool:
    return any(name in context for name, _ in PROPERTY_REQUIREMENTS)


def _opens_new_flow(session: ConversationSession, intent: str) -> bool:
    """Whether this intent starts a *different* flow.

    A flow switch resets the context, so it is only allowed while the current
    flow has collected nothing yet. Otherwise a landlord half-way through a
    listing whose "je loue" is read as a tenant search would be silently thrown
    into the opposite role — the role is set by the facts already given, not by
    the model's reading of one ambiguous verb.
    """
    opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
    if opening_flow is None or session.flow is opening_flow:
        return False
    return not (session.flow in LISTING_FLOWS and _has_listing_facts(session.context))


def question_topic(next_key: str | None, flow: FlowName) -> str | None:
    """The plain-words gloss of the next question, phrased for this flow."""
    if not next_key:
        return None
    overlay = _FLOW_TOPICS.get(flow, {})
    return overlay.get(next_key) or QUESTION_TOPICS.get(next_key)


# The schema entity a short answer to each question fills. Handing the model the
# target field, not only the question key, is what stops a bare "5" from being
# stored as availability instead of the minimum duration the platform asked for.
_FIELD_FOR_QUESTION: dict[str, str] = {
    "ask.property_type": "property_type",
    "ask.standing": "standing",
    "ask.location": "city and neighbourhood",
    "ask.rent": "price",
    "ask.features": "bedrooms, bathrooms, surface_area",
    "ask.charges": "charges and charging_policy",
    "ask.deposit": "deposit",
    "ask.minimum_duration": "minimum_duration_months",
    "ask.availability": "availability",
    "ask.conditions": "conditions",
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["intent", "confidence", "entities"],
    "properties": {
        "intent": {"type": "string", "enum": list(ALLOWED_INTENTS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "language": {"type": "string"},
        "reply": {"type": "string"},
        "entities": {
            "type": "object",
            "properties": {
                "property_type": {"type": "string"},
                "standing": {"type": "string", "enum": ["MODERN", "NON_MODERN"]},
                "city": {"type": "string"},
                "neighbourhood": {"type": "string"},
                "price": {"type": "integer"},
                "bedrooms": {"type": "integer"},
                "bathrooms": {"type": "integer"},
                "surface_area": {"type": "integer"},
                "charges": {"type": "integer"},
                "charging_policy": {"type": "string", "enum": ["INCLUDED", "EXTRA"]},
                # The mission wants eau and électricité stated separately when
                # the landlord gives both; the code sums them for ``charges``.
                "water_charges": {"type": "integer"},
                "electricity_charges": {"type": "integer"},
                "deposit": {"type": "integer"},
                "minimum_duration_months": {"type": "integer"},
                "availability": {"type": "string"},
                "conditions": {"type": "string"},
                # Anything the landlord *said* about these five axes. Hints are
                # the probable readings, which never become facts on a listing.
                "amenities": {
                    "type": "object",
                    "properties": {
                        "parking": {"type": "boolean"},
                        "water": {"type": "boolean"},
                        "electricity": {"type": "boolean"},
                        "internet": {"type": "boolean"},
                        "security": {"type": "boolean"},
                        "extras": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "amenities_hints": {
                    "type": "object",
                    "properties": {
                        "parking": {"type": "boolean"},
                        "water": {"type": "boolean"},
                        "electricity": {"type": "boolean"},
                        "internet": {"type": "boolean"},
                        "security": {"type": "boolean"},
                    },
                },
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


@dataclass(frozen=True, slots=True)
class _Verdict:
    """What the domain accepted from one proposed intent."""

    intent: str
    facts: ExtractedFacts
    accepted: bool
    reason: str | None
    next_step: FlowStep | None


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
        response_model: ResponseModel | None = None,
        extractor: FactExtractor | None = None,
        budget: BudgetPolicy | None = None,
        provider_name: str = "openrouter",
        listing: PropertyListing | None = None,
    ) -> None:
        self._uow = uow
        self._inbound = inbound
        self._sessions = sessions
        self._turns = turns
        self._usage = usage
        self._classifier = classifier or DeterministicClassifier()
        self._model = model
        self._response_model = response_model
        self._extractor = extractor or FactExtractor()
        self._budget = budget
        self._provider = provider_name
        self._listing = listing

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

        # Nothing has been written yet, so releasing the transaction costs
        # nothing and the model call never holds a database connection.
        await self._uow.commit()

        text = message.text or ""
        pending = session.context.get("next_question")
        pending_key = pending if isinstance(pending, str) else None

        if self._model is None:
            return await self._without_a_model(
                session,
                message_id=command.message_id,
                text=text,
                pending=pending_key,
                now=moment,
            )

        # The rules are a fast lane now, not the primary reader: only replies a
        # model could not improve ("oui", "250 000 fcfa") skip the call. The open
        # question is passed along so a bare "non" answers "moderne ou non"
        # instead of being read as a refusal.
        shortcut = self._classifier.shortcut(text, pending=pending_key)
        if shortcut is not None:
            ai_bypass_total.labels(stage="regex").inc()
            return await self._finish(
                session=session,
                message_id=command.message_id,
                intent=shortcut.intent,
                confidence=shortcut.confidence,
                entities=dict(shortcut.entities),
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=moment,
            )

        decision = await self._budget_decision(session.user_id, moment)
        if not decision.allowed:
            ai_bypass_total.labels(stage="budget").inc()
            return await self._finish(
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

        ai_bypass_total.labels(stage="llm").inc()
        prompt = build_analysis_prompt(
            text=message.text,
            language=session.language,
            history=history,
            flow=session.flow,
            step=session.step,
            pending_question=pending_key,
            known_facts=dict(session.context),
        )
        result = await self._model.understand_intent(prompt)
        entities = dict(result.entities)
        # The locale shorthand ("50 mil") is a rule, not a guess: when the
        # message spells one it overrides whatever number the model proposed,
        # because the model's prior reads "mil" as a million.
        shorthand = self._classifier.shorthand_amount(text)
        if shorthand is not None:
            entities["price"] = shorthand
        verdict = self._verdict(
            session=session,
            intent=result.intent,
            confidence=result.confidence,
            entities=entities,
            reason=None,
        )

        # The reply is written before the write transaction opens: an LLM call
        # must never hold a database connection, and a reply that fails degrades
        # to a template rather than to silence.

        # Preferred path: the classification call already wrote the reply in its
        # JSON, so the whole turn costs one round-trip. Only a model that left
        # ``reply`` empty pays for a second call; a deployment that never asks
        # for an inline reply degrades to the same behaviour as before.
        reply_text: str | None = result.reply
        reply_usage: LlmUsage | None = None
        if reply_text is None:
            reply_text, reply_usage = await self._compose_reply(
                verdict=verdict,
                text=text,
                session=session,
                history=history,
                now=moment,
            )

        # Both calls are one turn: the ledger records their sum, so reply traffic
        # cannot spend past a budget that only counted intent calls.
        call_usage = result.usage
        model_name = call_usage.model if call_usage is not None else self._model.model
        input_tokens = call_usage.input_tokens if call_usage is not None else 0
        output_tokens = call_usage.output_tokens if call_usage is not None else 0
        latency_ms = call_usage.latency_ms if call_usage is not None else 0
        cost_usd = (
            Decimal(str(call_usage.estimated_cost_usd))
            if call_usage is not None
            else Decimal("0")
        )
        if reply_usage is not None:
            input_tokens += reply_usage.input_tokens
            output_tokens += reply_usage.output_tokens
            latency_ms += reply_usage.latency_ms
            cost_usd += Decimal(str(reply_usage.estimated_cost_usd))

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
            verdict=verdict,
            confidence=result.confidence,
            used_llm=True,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            now=moment,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reply_text=reply_text,
        )

    async def _without_a_model(
        self,
        session: ConversationSession,
        *,
        message_id: uuid.UUID,
        text: str,
        pending: str | None,
        now: datetime,
    ) -> AnalysisReport:
        """The deployment has no AI key: fall back to regexes and templates.

        The rule set here is the full classifier, not the fast lane, because it
        is now the only reader. An unresolved message still records its turn and
        asks the user to rephrase instead of leaving the thread silent.
        """
        match = self._classifier.classify(text, pending=pending)
        if match is not None:
            ai_bypass_total.labels(stage="regex").inc()
            return await self._finish(
                session=session,
                message_id=message_id,
                intent=match.intent,
                confidence=match.confidence,
                entities=dict(match.entities),
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=now,
            )

        ai_bypass_total.labels(stage="budget").inc()
        return await self._finish(
            session=session,
            message_id=message_id,
            intent="UNKNOWN",
            confidence=0.0,
            entities={},
            used_llm=False,
            latency_ms=0,
            cost_usd=Decimal("0"),
            now=now,
            reason="no_model_configured",
        )

    async def _finish(
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
    ) -> AnalysisReport:
        """Close a call that did not reach the model: no reply is written."""
        return await self._apply(
            session=session,
            message_id=message_id,
            verdict=self._verdict(
                session=session,
                intent=intent,
                confidence=confidence,
                entities=entities,
                reason=reason,
            ),
            confidence=confidence,
            used_llm=used_llm,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            now=now,
        )

    def _verdict(
        self,
        *,
        session: ConversationSession,
        intent: str,
        confidence: float,
        entities: dict[str, Any],
        reason: str | None,
    ) -> _Verdict:
        """Validate a proposal through the domain, before anything is written.

        Split out of ``_apply`` so the reply can be written from the *outcome*
        of the turn — the next question, or the fact it was refused — while the
        write transaction is still closed.
        """
        if not is_supported_intent(intent):
            intent, reason = "UNKNOWN", reason or "unsupported_intent"
        if reason is None and confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = "low_confidence"

        facts = self._extractor.validate(entities)
        # The next step must consider the whole form, not just this message: a
        # landlord who gives their city after naming the property type is one
        # step further, not back at the type question. When this message opens a
        # *new* flow, the old facts belong to the abandoned attempt and are
        # deliberately ignored — matching the reset ``_advance_session`` does.
        starts_new_flow = _opens_new_flow(session, intent)
        known = {} if starts_new_flow else dict(session.context)
        next_step = (
            self._extractor.next_step(intent, facts, flow=session.flow, known=known)
            if reason is None
            else None
        )
        return _Verdict(
            intent=intent,
            facts=facts,
            accepted=reason is None,
            reason=reason,
            next_step=next_step,
        )

    async def _compose_reply(
        self,
        *,
        verdict: _Verdict,
        text: str,
        session: ConversationSession,
        history: list[TurnSummary],
        now: datetime,
    ) -> tuple[str | None, LlmUsage | None]:
        """Let the model phrase the answer, or return ``(None, None)``.

        ``None`` means "use the template": either no response model is
        configured, or writing it failed. A reply failure must never cost the
        user their turn, so the exception is swallowed here and the outbound
        layer falls back to its stable copy.
        """
        if self._response_model is None or not text.strip():
            return None, None

        next_key = question_key_for(verdict.next_step) if verdict.accepted else None
        prompt = build_reply_prompt(
            text=text,
            language=session.language,
            verdict=verdict,
            next_key=next_key,
            history=history,
            flow=session.flow,
        )
        try:
            response = await self._response_model.generate_response(prompt)
        except DomainError:
            return None, None
        sentence = response.content.strip()
        if not sentence:
            return None, None
        return sentence, response.usage

    async def _apply(
        self,
        *,
        session: ConversationSession,
        message_id: uuid.UUID,
        verdict: _Verdict,
        confidence: float,
        used_llm: bool,
        latency_ms: int,
        cost_usd: Decimal,
        now: datetime,
        model_name: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reply_text: str | None = None,
    ) -> AnalysisReport:
        """Record the turn, move the conversation, and stage the answer.

        Extraction already happened in ``_verdict``; a rejected outcome is still
        recorded, so "the model was unsure" stays measurable instead of being
        silently converted into a wrong listing.
        """
        intent = verdict.intent
        facts = verdict.facts
        next_step = verdict.next_step
        reason = verdict.reason
        accepted = verdict.accepted

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
            listing_text, next_step, listing_id = await self._confirm_listing(
                session, intent=intent, next_step=next_step, now=now
            )
            await self._advance_session(
                session, intent, facts, next_step, now=now, attach_property_id=listing_id
            )
            reply_text = listing_text or reply_text

        # Committed with the turn: an analysis the platform understood but never
        # answered is a landlord staring at a silent thread, and no retry can
        # notice a reply that was never staged.
        reply_text = _collapse_repeats(reply_text) if reply_text else reply_text
        question = question_key_for(next_step) if accepted else None
        await self._stage_reply(
            session,
            intent=intent,
            reason=reason,
            question_key=question,
            text=reply_text,
            now=now,
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
        text: str | None = None,
    ) -> None:
        """Request the one reply this turn owes the user.

        Which question comes next is decided above; this only records that an
        answer is due and, when the model wrote one, the sentence to send. A
        missing ``text`` is not a silent thread: the outbound layer renders the
        stable copy for the ``question_key``, which is the deterministic path.
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
                    intent=intent,
                    text=text,
                    language=session.language,
                )
            ],
            correlation_id=current_context().get("correlation_id") or "unknown",
        )

    async def _confirm_listing(
        self,
        session: ConversationSession,
        *,
        intent: str,
        next_step: FlowStep | None,
        now: datetime,
    ) -> tuple[str | None, FlowStep | None, uuid.UUID | None]:
        """Register the listing when the landlord confirms the summary.

        A confirmation only means something at ``CONFIRM_PROPERTY``; anywhere
        else it is ordinary small talk and the flow is left untouched. The
        property is *not* attached here: attaching bumps the session revision
        and would break the optimistic-concurrency baseline ``_advance_session``
        captures, so the id is handed to it instead.
        """
        if (
            self._listing is None
            or intent != "AFFIRMATIVE"
            or session.step is not FlowStep.CONFIRM_PROPERTY
        ):
            return None, next_step, None

        result = await self._listing.register_from_conversation(
            user_id=session.user_id,
            facts=dict(session.context),
            existing_property_id=session.assigned_property_id,
            now=now,
        )
        if result is None:
            return _LISTING_UNSAVED_TEXT, next_step, None

        if result.published:
            return None, FlowStep.PUBLISHED, result.property_id
        return _missing_listing_reply(result.missing), FlowStep.COLLECT_MEDIA, result.property_id

    async def _advance_session(
        self,
        session: ConversationSession,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        *,
        now: datetime,
        attach_property_id: uuid.UUID | None = None,
    ) -> None:
        """Open the flow if needed, merge the facts, move to the chosen step.

        ``expected_revision`` is the revision read at load time, so if another
        worker moved this session meanwhile the save is refused and the analysis
        is discarded instead of overwriting a concurrent step.
        """
        observed = session.revision

        opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
        if opening_flow is not None and _opens_new_flow(session, intent):
            # Resets the context, so it must come first: the facts about to be
            # merged belong to this new attempt, not to the abandoned one.
            session.start_flow(opening_flow, now=now, first_step=next_step or FlowStep.START)

        # Transition before merging: `transition` compares expected_revision
        # against the current revision, and merging first would bump it and make
        # every save look like a conflict.
        if next_step is not None:
            session.transition(next_step, now=now, expected_revision=session.revision)
        session.merge_context(facts.as_context(), now=now)
        # Attaching after the baseline is captured keeps the save's
        # expected_revision equal to the value read from the row.
        if attach_property_id is not None:
            session.attach_property(attach_property_id, now=now)
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


# The quality gate reports stable codes; the user gets words. Kept here rather
# than in the outbound layer because the list is dynamic and cannot be a fixed
# ``question_key``.
_MISSING_LABELS_FR: dict[str, str] = {
    "MISSING_PHOTOS": "les photos du logement (envoyez-en au moins une)",
    "MISSING_CHARGES": "les charges mensuelles",
    "MISSING_MINIMUM_DURATION": "la durée minimale de location",
    "MISSING_AVAILABILITY_DATE": "la date de disponibilité",
    "VAGUE_LOCATION": "le quartier précis",
    "MISSING_DEPOSIT": "le dépôt de garantie",
    "AMBIGUOUS_CONDITIONS": "les conditions de location",
    "SUSPICIOUS_PRICE": "le prix, qui semble anormal",
}

_LISTING_UNSAVED_TEXT = (
    "Je n'ai pas pu enregistrer l'annonce pour le moment. Réessayez dans un instant."
)


def _missing_listing_reply(missing: tuple[str, ...]) -> str:
    labels = [_MISSING_LABELS_FR.get(code, code) for code in missing]
    return (
        "C'est enregistré, votre annonce est créée. "
        "Pour pouvoir la publier, il manque encore : " + _join_fr(labels) + "."
    )


def _join_fr(items: list[str]) -> str:
    if not items:
        return "rien"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " et " + items[-1]


def _collapse_repeats(text: str) -> str:
    """Drop a phrase the model emitted several times in a row.

    A reply model occasionally loops on a short fragment ("en sus en sus en sus
    en plus du loyer"). The loop is a decoding artefact, not meaning, and sending
    it verbatim makes the assistant look broken. Runs of a repeated one- or
    two-word unit collapse to a single occurrence; numeric tokens are left alone
    so a price never loses a digit.
    """
    tokens = text.split()
    out: list[str] = []
    index = 0
    total = len(tokens)
    while index < total:
        for unit in (2, 1):
            if index + 2 * unit > total:
                continue
            fragment = [token.casefold() for token in tokens[index : index + unit]]
            if not any(any(char.isalpha() for char in token) for token in fragment):
                continue
            end = index + unit
            while (
                end + unit <= total
                and [token.casefold() for token in tokens[end : end + unit]] == fragment
            ):
                end += unit
            if end > index + unit:
                out.extend(tokens[index : index + unit])
                index = end
                break
        else:
            out.append(tokens[index])
            index += 1
    return " ".join(out)


def build_analysis_prompt(
    *,
    text: str | None,
    language: str,
    history: list[TurnSummary],
    flow: FlowName,
    step: FlowStep,
    pending_question: str | None,
    known_facts: dict[str, Any],
) -> Prompt:
    """Assemble the request, with the state of the conversation as context.

    The context is what turns "Bastos" from noise into an answer: the model can
    see that it is replying to the location question of an open listing, and
    fill the matching entity instead of guessing an intent from three words.

    The phone number is never part of the prompt: the model has no business
    knowing who it is talking to, and prompts end up in vendor logs.
    """
    public_known = _public_known_facts(known_facts)
    payload: dict[str, Any] = {
        "message": text or "",
        "language": language if language in SUPPORTED_LANGUAGES else "fr",
        "you_are_helping": _FLOW_ROLES.get(flow, _FLOW_ROLES[FlowName.SUPPORT]),
        "conversation": {
            "flow": flow.value,
            "step": step.value,
            "awaiting_answer_to": pending_question,
            "awaiting_question": question_topic(pending_question, flow),
            "awaiting_field": _FIELD_FOR_QUESTION.get(pending_question or ""),
            "known_facts": public_known,
        },
        # Only what is still missing, in order, so the model asks the next real
        # question without re-reading fields already collected. The schema closes
        # the intent enum, so a second copy of the allowlist would be dead bytes.
        "required_fields": _missing_fields(flow, public_known),
        "schema": RESPONSE_SCHEMA,
    }
    # The price band anchors the magnitude of a bare number; once the rent is
    # known it is context that can never be used again, so it is dropped.
    if not public_known.get("rent"):
        payload["price_reference_monthly_xaf"] = {
            name: {"min": low, "max": high}
            for name, (low, high) in PRICE_REFERENCES_XAF.items()
        }
    turns = _turns_as_json(history)
    if turns:
        payload["recent_turns"] = turns
    return Prompt(
        system=SYSTEM_PROMPT,
        user=json.dumps(payload, ensure_ascii=False),
        response_schema=RESPONSE_SCHEMA,
    )


# Keys the pipeline keeps in the session for its own use, never for the model:
# leaking "next_question" into known_facts invites the model to treat an internal
# variable as a fact about the property.
_PROMPT_INTERNAL_KEYS = frozenset({"next_question", "abort_reason"})


def _public_known_facts(known_facts: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in known_facts.items()
        if name not in _PROMPT_INTERNAL_KEYS and not str(name).startswith("_")
    }


def _missing_fields(flow: FlowName, known: dict[str, Any]) -> list[dict[str, str]]:
    """The still-missing form fields, in order, glossed for this flow.

    Making the model decide the next question is expensive in tokens *and*
    fragile; handing it only the remaining fields lets it do both jobs (read the
    message, ask the next question) while the code still owns the order.
    """
    fields: list[dict[str, str]] = []
    for name, step in PROPERTY_REQUIREMENTS:
        if name in known:
            continue
        fields.append(
            {"field": name, "ask": question_topic(question_key_for(step), flow) or ""}
        )
    return fields


def build_reply_prompt(
    *,
    text: str,
    language: str,
    verdict: _Verdict,
    next_key: str | None,
    history: list[TurnSummary],
    flow: FlowName,
) -> Prompt:
    """Ask the model to write the sentence this turn sends back.

    Only accepted facts are shown, and the next question is described in plain
    words rather than by its internal key: the model tailors the phrasing, it
    does not get to decide what is asked — the code already did. The flow is
    passed too, so the wording matches the side of the conversation: a landlord
    is asked the rent they *want*, a tenant the rent they *seek*.
    """
    return Prompt(
        system=REPLY_SYSTEM_PROMPT,
        user=json.dumps(
            {
                "message": text,
                "language": language if language in SUPPORTED_LANGUAGES else "fr",
                "you_are_helping": _FLOW_ROLES.get(flow, _FLOW_ROLES[FlowName.SUPPORT]),
                "understood": {
                    "intent": verdict.intent,
                    "accepted": verdict.accepted,
                    "known_facts": verdict.facts.as_context() if verdict.accepted else {},
                },
                # None means "the message was not understood": ask for a rephrase
                # rather than posing a question the platform never reached.
                "ask_about": question_topic(next_key, flow),
                "recent_turns": _turns_as_json(history),
            },
            ensure_ascii=False,
        ),
        temperature=0.4,
        max_output_tokens=256,
    )


def _turns_as_json(history: list[TurnSummary]) -> list[dict[str, Any]]:
    return [
        {
            "direction": turn.direction,
            "intent": turn.intent,
            "confidence": round(turn.confidence, 2),
            "used_llm": turn.used_llm,
        }
        for turn in history
    ]
