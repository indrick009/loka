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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    AiUsageLedger,
    BudgetDecision,
    BudgetPolicy,
    ConversationPropertyHit,
    ConversationSearchQuery,
    ConversationTurn,
    ConversationTurnRepository,
    IntentModel,
    PropertyListing,
    PropertySearchGateway,
    ResponseModel,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.ai.domain.services.ai_provider import (
    SUPPORTED_LANGUAGES,
    LlmUsage,
    Prompt,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    LISTING_FLOWS,
    MIN_ACCEPTED_CONFIDENCE,
    PROPERTY_REQUIREMENTS,
    SEARCH_REQUIREMENTS,
    SUPPORTED_INTENTS,
    ExtractedFacts,
    FactExtractor,
    is_supported_intent,
    question_key_for,
    question_to_ask,
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
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import (
    MessageKind,
    SharedLocation,
)
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import DomainError
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import (
    ai_bypass_total,
    llm_calls_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_tokens_total,
)

USE_CASE = "analyse_conversation_message"

_logger = get_logger(__name__)

# Both prompts below can write the sentence a user reads, so the identity lives
# in one place. When only one of them carried a name, the assistant introduced
# itself as "Loka" on one turn and "Kasa" on another, from an account called Kora.
PERSONA = "You are Kora, a warm WhatsApp assistant for a Cameroonian services platform."

# A reference band anchors the model's own sense of magnitude — a studio at "50"
# means 50 000 francs. It is sourced, wide and partly unconfirmed, so it is not a
# tariff: quoted to a user it becomes a commercial promise the platform never
# made. One did reach a tenant as "le loyer minimum pour un appartement est de
# 40 000 FCFA".
PRICE_REFERENCE_RULE = (
    "A price reference range is background for your own judgement, never a "
    "tariff: never quote it to the user as a price, a minimum or a maximum."
)

SYSTEM_PROMPT = (
    f"{PERSONA} "
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
    " Also set 'language' to the language the user wrote in ('fr' or 'en'), then "
    "set 'reply': the next WhatsApp message to send, written in that same "
    "language, one to three short sentences of plain text (no markdown, no emoji, "
    "no signature). A message too short to tell the language (a bare number or a "
    "single word) keeps 'default_language'. Acknowledge the message briefly, then "
    "ask for the first field "
    "in 'required_fields' that is not already in known_facts; if none is missing, "
    "confirm the summary. If the message was not understood, ask the user to "
    "rephrase instead of posing a question."
    f" {PRICE_REFERENCE_RULE}"
)

# The closed set the model may choose from. Exposed in the prompt so the model
# never invents a state the platform does not have: an unsupported intent is
# discarded as untrusted, which would turn a simple "bonjour" into a confusing
# rephrase request.
ALLOWED_INTENTS: tuple[str, ...] = tuple(sorted(SUPPORTED_INTENTS))

REPLY_SYSTEM_PROMPT = (
    f"{PERSONA} Write the next message the assistant sends to the user. "
    "Answer in the language given in the input. One to three short sentences, plain "
    "text: no markdown, no emoji, no letter salutation and no signature. "
    "If a question is given, ask that question and no other, naturally in your own "
    "words; never substitute a different one, and never ask for something already "
    "present in known_facts. Do not answer that question on the user's behalf — "
    "only the user can say what their answer is. "
    "Never invent a listing, a price or a fact that is not in the context, and "
    f"never promise anything the platform did not confirm. {PRICE_REFERENCE_RULE}"
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
    # The tenant's side: a search asks its own questions, and these glosses are
    # what the reply model reads instead of the outbound copy.
    "ask.search_type": "the type of property the tenant is looking for",
    "ask.search_location": "the city and neighbourhood the tenant wants",
    "ask.search_budget": "the maximum monthly rent the tenant is willing to pay, in XAF",
    "ask.search_criteria": "which search criterion the tenant wants to change",
    "ask.search_results": "the list of properties matching the tenant's criteria",
    "ask.search_choice": "which property from the list the tenant picks",
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


def _target_flow(session: ConversationSession, intent: str) -> FlowName:
    """The flow this turn belongs to, including one it is about to open.

    Needed wherever the answer must be phrased *before* the session moves: the
    first search message still reads as SUPPORT in the session, but its next
    question is a tenant's.
    """
    opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
    if opening_flow is not None and _opens_new_flow(session, intent):
        return opening_flow
    return session.flow


def _neighbourhood_from_label(label: str | None) -> str | None:
    """A quartier the district index recognises inside a WhatsApp place name.

    The label is whatever the network attached — "Bastos", "Yaoundé, Cameroun",
    or a shop's own name — so it is searched for a *known* district and
    otherwise discarded. Passing it through unchecked would turn a boutique's
    signboard into a neighbourhood the catalogue cannot be searched by.
    """
    if not label:
        return None
    for part in label.split(","):
        canonical = market.canonical_neighbourhood(part)
        if canonical is not None:
            return canonical
    return None


def _known_facts(session: ConversationSession, intent: str) -> dict[str, Any]:
    """What this turn may build on: nothing from an abandoned attempt."""
    return {} if _opens_new_flow(session, intent) else dict(session.context)


def _prompt_flow(session: ConversationSession, hint_intent: str | None) -> FlowName:
    """The flow whose form the next question must be phrased against.

    On the message that *opens* a flow the session is still on SUPPORT, so
    phrasing the reply from ``session.flow`` hands a tenant the landlord's form
    ("standing", "charges", "deposit") and the model asks the wrong questions.
    The cheap rule set reads the opening intent, and its flow is the one the
    reply has to speak for.
    """
    if hint_intent is None:
        return session.flow
    opening_flow = FLOW_BY_OPENING_INTENT.get(hint_intent)
    if opening_flow is not None and _opens_new_flow(session, hint_intent):
        return opening_flow
    return session.flow


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
    # The search questions bind to the same entities: a tenant answering a
    # budget question with "150" is giving a price, exactly as a landlord is.
    "ask.search_type": "property_type",
    "ask.search_location": "city and neighbourhood",
    "ask.search_budget": "price",
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
        search: PropertySearchGateway | None = None,
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
        self._search = search

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

        # A pin and a photo are things the user *sent*, not sentences needing
        # interpretation. They are read here, before any model: paying one to
        # look at two floats would be slower, dearer and less reliable than
        # reading the fields, and it is why D3's media never had to involve a
        # model at all.
        if message.location is not None:
            return await self._from_shared_location(
                session, message_id=command.message_id, point=message.location, now=moment
            )
        if message.media_object_key is not None:
            return await self._from_media(
                session,
                message_id=command.message_id,
                object_key=message.media_object_key,
                kind=message.kind,
                text=text,
                now=moment,
            )

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
        # A cheap rule reading only picks which form the prompt shows; the model
        # still chooses the intent. On an opening turn it is what tells a tenant
        # the search fields instead of the landlord's.
        hint = self._classifier.classify(text, pending=pending_key)
        prompt = build_analysis_prompt(
            text=message.text,
            default_language=session.language,
            history=history,
            flow=_prompt_flow(session, hint.intent if hint is not None else None),
            step=session.step,
            pending_question=pending_key,
            known_facts=dict(session.context),
        )
        result = await self._model.understand_intent(prompt)
        # The model read the message, so it is the one that knows which language
        # the user is writing in. Adopting its answer keeps a bare "150000" in
        # tomorrow's fallback in the right language instead of snapping to French.
        if result.language in SUPPORTED_LANGUAGES:
            session.language = result.language
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

    async def _from_shared_location(
        self,
        session: ConversationSession,
        *,
        message_id: uuid.UUID,
        point: SharedLocation,
        now: datetime,
    ) -> AnalysisReport:
        """Read a shared position as the answer to the location question.

        ``confidence`` is 1.0 because nothing was inferred: the user pressed
        "send location" and these are the coordinates WhatsApp attached. The
        place name travels as a neighbourhood *candidate* — the extractor only
        keeps it if the district index recognises it, so a shop's name on a map
        never becomes a quartier.

        A point outside every known market yields no city, the extractor rejects
        the location, and the question stays open. That is the honest outcome:
        there is no geocoder here, and naming a city we cannot justify would
        file the listing in a town the landlord never mentioned.
        """
        ai_bypass_total.labels(stage="location").inc()
        entities: dict[str, Any] = {
            "latitude": point.latitude,
            "longitude": point.longitude,
        }
        neighbourhood = _neighbourhood_from_label(point.label)
        if neighbourhood is not None:
            entities["neighbourhood"] = neighbourhood
        return await self._finish(
            session=session,
            message_id=message_id,
            intent="COLLECT_LOCATION",
            confidence=1.0,
            entities=entities,
            used_llm=False,
            latency_ms=0,
            cost_usd=Decimal("0"),
            now=now,
        )

    async def _from_media(
        self,
        session: ConversationSession,
        *,
        message_id: uuid.UUID,
        object_key: str,
        kind: MessageKind,
        text: str,
        now: datetime,
    ) -> AnalysisReport:
        """Attach what the user sent to the listing the conversation is building.

        Housing step 9 makes photos mandatory, so this is the step the whole
        media pipeline exists to serve. There is nothing to classify: the bytes
        are already stored and the kind is already known.

        With no listing to attach to, the turn is still recorded and reported
        with a reason — a photo that vanishes with no trace is the defect, not
        the missing draft.
        """
        ai_bypass_total.labels(stage="media").inc()
        media_kind = "DOCUMENT" if kind is MessageKind.DOCUMENT else "PHOTO"
        property_id = session.assigned_property_id
        reason: str | None = None
        if self._listing is None or property_id is None:
            reason = "media_without_a_listing"
            _logger.warning(
                "media_without_a_listing",
                session_id=str(session.id),
                object_key=object_key,
                kind=media_kind,
            )
        else:
            try:
                await self._listing.attach_media_from_conversation(
                    property_id=property_id,
                    object_key=object_key,
                    kind=media_kind,
                    now=now,
                )
            except DomainError as exc:
                # The listing refused it — already attached, or past 30 photos.
                # The turn still happened, and the reason is what tells the
                # landlord why his photo did not count.
                reason = "media_refused"
                _logger.warning(
                    "media_refused",
                    session_id=str(session.id),
                    object_key=object_key,
                    error=exc.message,
                )

        return await self._finish(
            session=session,
            message_id=message_id,
            intent="COLLECT_MEDIA" if reason is None else "UNKNOWN",
            confidence=1.0,
            entities={},
            used_llm=False,
            latency_ms=0,
            cost_usd=Decimal("0"),
            now=now,
            reason=reason,
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
        known = _known_facts(session, intent)
        next_step = (
            self._extractor.next_step(
                intent, facts, flow=_target_flow(session, intent), known=known
            )
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

        next_key = (
            question_key_for(verdict.next_step, _target_flow(session, verdict.intent))
            if verdict.accepted
            else None
        )
        prompt = build_reply_prompt(
            text=text,
            language=session.language,
            verdict=verdict,
            next_key=next_key,
            history=history,
            flow=session.flow,
            known_facts=dict(session.context),
        )
        model_name = str(getattr(self._response_model, "model", "unknown"))
        try:
            response = await self._response_model.generate_response(prompt)
        except DomainError as exc:
            # The user still gets a sentence — the outbound layer renders the
            # template — but the turn's own call has already been counted as
            # "ok", so without these two lines a response model that is down
            # looks perfectly healthy: every answer quietly degrades to a
            # template and no dashboard says a word.
            llm_calls_total.labels(
                use_case=USE_CASE, model=model_name, outcome="error"
            ).inc()
            _logger.warning(
                "reply_composition_failed",
                session_id=str(session.id),
                model=model_name,
                error=type(exc).__name__,
            )
            return None, None
        sentence = response.content.strip()
        if not sentence:
            # Billed and unusable. The usage is still returned so the ledger and
            # the budget see what the call cost; dropping it made an empty-answer
            # regression indistinguishable from a deployment with no reply model.
            _logger.warning(
                "reply_composition_empty", session_id=str(session.id), model=model_name
            )
            return None, response.usage
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

        # The model's sentence is swept for decoding loops here: the
        # deterministic copy that may replace it is written by the platform and
        # needs no such handling.
        reply_text = _collapse_repeats(reply_text) if reply_text else reply_text

        # Decided by ``_advance_session`` and by nobody else. Computing it twice
        # is how the question the session remembered drifted away from the one
        # the user was actually asked.
        question: str | None = None

        if accepted:
            listing_text, next_step, listing_id = await self._confirm_listing(
                session, intent=intent, next_step=next_step, now=now
            )
            search_text, next_step = await self._present_results(
                session, intent=intent, facts=facts, next_step=next_step
            )
            question = await self._advance_session(
                session, intent, facts, next_step, now=now, attach_property_id=listing_id
            )
            # The catalogue's numbers are data, not prose: they win over both
            # the model's sentence and the listing summary, which cannot apply
            # to the same turn.
            reply_text = search_text or listing_text or reply_text

        # Committed with the turn: an analysis the platform understood but never
        # answered is a landlord staring at a silent thread, and no retry can
        # notice a reply that was never staged.
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
            return _copy(_LISTING_UNSAVED_TEXT, session.language), next_step, None

        if result.published:
            return None, FlowStep.PUBLISHED, result.property_id
        return (
            _missing_listing_reply(result.missing, session.language),
            FlowStep.COLLECT_MEDIA,
            result.property_id,
        )

    async def _present_results(
        self,
        session: ConversationSession,
        *,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
    ) -> tuple[str | None, FlowStep | None]:
        """Run the tenant's search once its criteria are complete.

        Only ``PRESENT_RESULTS`` reaches this, and the extractor hands that step
        over exactly when the tenant's form is filled — the same "when" rule
        ``_confirm_listing`` uses for a landlord's publication. The reply is
        written here rather than by the model because it is the catalogue
        speaking: a price or a neighbourhood a model rephrased would be a
        listing that does not exist.

        An empty result moves back to the criteria step, not to a choice: there
        is nothing to choose yet, so the reply offers the one move that can
        change the outcome — widening the search — without promising a follow-up
        the platform has no watcher to send.
        """
        if next_step is not FlowStep.PRESENT_RESULTS:
            return None, next_step
        if self._search is None:
            return _copy(_SEARCH_UNAVAILABLE_TEXT, session.language), next_step
        query = _search_query({**_known_facts(session, intent), **facts.as_context()})
        if query is None:
            # The form reported complete but the facts describe no place to
            # search: calling that "no match" would be a lie the tenant cannot
            # act on, so it is reported as a failure instead.
            return _copy(_SEARCH_UNAVAILABLE_TEXT, session.language), next_step
        hits = await self._search.search_for_conversation(query, limit=SEARCH_RESULT_LIMIT)
        if not hits:
            return _copy(_NO_RESULTS_TEXT, session.language), FlowStep.COLLECT_SEARCH_CRITERIA
        return _results_text(hits, session.language), FlowStep.COLLECT_PROPERTY_CHOICE

    async def _advance_session(
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
        carried = session.context.get("next_question")
        pending = carried if isinstance(carried, str) else None

        opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
        if opening_flow is not None and _opens_new_flow(session, intent):
            # Resets the context, so it must come first: the facts about to be
            # merged belong to this new attempt, not to the abandoned one.
            session.start_flow(opening_flow, now=now, first_step=next_step or FlowStep.START)
            pending = None

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
        asked = question_to_ask(next_step=next_step, flow=session.flow, pending=pending)
        session.record_pending_question(asked)
        await self._sessions.save(session, expected_revision=observed)
        return asked

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
# ``question_key``. Every platform-authored sentence exists in French (the
# default) and English: the catalogue must not speak French to a thread the
# model read as English, and it cannot hand the phrasing to the model either —
# a rephrased price would be a listing that does not exist.
_LANG_FR = "fr"


def _copy(table: Mapping[str, str], language: str) -> str:
    return table.get(language, table[_LANG_FR])


_MISSING_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "MISSING_PHOTOS": "les photos du logement (envoyez-en au moins une)",
        "MISSING_CHARGES": "les charges mensuelles",
        "MISSING_MINIMUM_DURATION": "la durée minimale de location",
        "MISSING_AVAILABILITY_DATE": "la date de disponibilité",
        "VAGUE_LOCATION": "le quartier précis",
        "MISSING_DEPOSIT": "le dépôt de garantie",
        "AMBIGUOUS_CONDITIONS": "les conditions de location",
        "SUSPICIOUS_PRICE": "le prix, qui semble anormal",
    },
    "en": {
        "MISSING_PHOTOS": "the property photos (send at least one)",
        "MISSING_CHARGES": "the monthly charges",
        "MISSING_MINIMUM_DURATION": "the minimum rental duration",
        "MISSING_AVAILABILITY_DATE": "the availability date",
        "VAGUE_LOCATION": "the exact neighbourhood",
        "MISSING_DEPOSIT": "the security deposit",
        "AMBIGUOUS_CONDITIONS": "the rental conditions",
        "SUSPICIOUS_PRICE": "the price, which looks unusual",
    },
}

_LISTING_UNSAVED_TEXT: dict[str, str] = {
    "fr": (
        "Je n'ai pas pu enregistrer l'annonce pour le moment. "
        "Réessayez dans un instant."
    ),
    "en": "I could not save the listing for now. Please try again in a moment.",
}

_MISSING_LISTING_INTRO: dict[str, str] = {
    "fr": (
        "C'est enregistré, votre annonce est créée. "
        "Pour pouvoir la publier, il manque encore : "
    ),
    "en": (
        "Saved, your listing is created. "
        "To publish it, the following is still missing: "
    ),
}

_JOIN_WORD: dict[str, str] = {"fr": " et ", "en": " and "}
_EMPTY_LIST: dict[str, str] = {"fr": "rien", "en": "nothing"}


def _missing_listing_reply(missing: tuple[str, ...], language: str) -> str:
    labels_by_code = _MISSING_LABELS.get(language, _MISSING_LABELS[_LANG_FR])
    labels = [labels_by_code.get(code, code) for code in missing]
    return _copy(_MISSING_LISTING_INTRO, language) + _join(labels, language) + "."


def _join(items: list[str], language: str) -> str:
    if not items:
        return _copy(_EMPTY_LIST, language)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + _copy(_JOIN_WORD, language) + items[-1]


# A WhatsApp thread is not a results page: five properties is already a long
# message, and the catalogue is read again on the next criteria anyway.
SEARCH_RESULT_LIMIT = 5

_SEARCH_UNAVAILABLE_TEXT: dict[str, str] = {
    "fr": (
        "Je n'ai pas pu lancer la recherche pour le moment. "
        "Réessayez dans un instant, ou reformulez votre demande."
    ),
    "en": (
        "I could not run the search for now. "
        "Please try again in a moment, or rephrase your request."
    ),
}

_NO_RESULTS_TEXT: dict[str, str] = {
    "fr": (
        "Je n'ai pas encore de logement qui corresponde à votre budget. "
        "Pour trouver plus vite, vous pouvez élargir la recherche : changez le "
        "quartier, la ville ou le budget, et je relance tout de suite."
    ),
    "en": (
        "I don't have a property matching your budget yet. To find one faster, "
        "you can widen the search: change the neighbourhood, the city or the "
        "budget, and I'll run it again right away."
    ),
}

_SEARCH_OUTRO: dict[str, str] = {
    "fr": "Répondez avec le numéro du logement qui vous intéresse.",
    "en": "Reply with the number of the property you are interested in.",
}

_PROPERTY_TYPE_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "APARTMENT": "Appartement",
        "COMMERCIAL": "Local commercial",
        "DUPLEX": "Duplex",
        "HOUSE": "Maison",
        "LAND": "Terrain",
        "LOFT": "Loft",
        "ROOM": "Chambre",
        "STUDIO": "Studio",
    },
    "en": {
        "APARTMENT": "Apartment",
        "COMMERCIAL": "Commercial space",
        "DUPLEX": "Duplex",
        "HOUSE": "House",
        "LAND": "Land",
        "LOFT": "Loft",
        "ROOM": "Room",
        "STUDIO": "Studio",
    },
}

_BEDROOM_LABELS: dict[str, tuple[str, str]] = {
    "fr": ("chambre", "chambres"),
    "en": ("bedroom", "bedrooms"),
}

_PRICE_SUFFIX: dict[str, str] = {"fr": "FCFA/mois", "en": "FCFA/month"}

_RESULTS_INTRO: dict[str, str] = {
    "fr": "J'ai trouvé {count} {word} :",
    "en": "I found {count} {word}:",
}

_RESULTS_WORD: dict[str, tuple[str, str]] = {
    "fr": ("logement", "logements"),
    "en": ("property", "properties"),
}


def _format_xaf(amount: int) -> str:
    """"150 000" — grouped the way it is read out loud here."""
    return f"{amount:,}".replace(",", " ")


def _hit_line(index: int, hit: ConversationPropertyHit, language: str) -> str:
    types = _PROPERTY_TYPE_LABELS.get(language, _PROPERTY_TYPE_LABELS[_LANG_FR])
    kind = types.get(hit.property_type, hit.property_type.title())
    details = [kind]
    if hit.bedrooms:
        singular, plural = _BEDROOM_LABELS.get(language, _BEDROOM_LABELS[_LANG_FR])
        details.append(f"{hit.bedrooms} {plural if hit.bedrooms > 1 else singular}")
    if hit.surface_m2:
        details.append(f"{hit.surface_m2} m2")
    place = ", ".join(part for part in (hit.neighbourhood, hit.city) if part)
    # The price is the public one: rent plus charges when they are billed on
    # top, which is the number a tenant actually compares.
    suffix = _copy(_PRICE_SUFFIX, language)
    return f"{index}. {' '.join(details)} - {place} - {_format_xaf(hit.price_xaf)} {suffix}"


def _results_text(hits: Sequence[ConversationPropertyHit], language: str) -> str:
    count = len(hits)
    singular, plural = _RESULTS_WORD.get(language, _RESULTS_WORD[_LANG_FR])
    word = singular if count == 1 else plural
    intro = _copy(_RESULTS_INTRO, language).format(count=count, word=word)
    lines = [intro]
    lines.extend(_hit_line(index, hit, language) for index, hit in enumerate(hits, start=1))
    lines.append(_copy(_SEARCH_OUTRO, language))
    return "\n".join(lines)


def _search_query(facts: Mapping[str, Any]) -> ConversationSearchQuery | None:
    """The catalogue query these facts describe, or ``None``.

    ``None`` means the facts cannot describe a search at all — no city, the one
    criterion the read model cannot do without. The caller reports that as a
    failure, because "nothing matches your budget" and "there was nothing to
    search with" are different answers.
    """
    location = facts.get("location")
    if not isinstance(location, dict):
        return None
    city = str(location.get("city") or "").strip()
    if not city:
        return None
    neighbourhood = str(location.get("neighbourhood") or "").strip()

    property_type = facts.get("property_type")
    rent = facts.get("rent")
    rooms = facts.get("rooms")
    bedrooms = rooms.get("bedrooms") if isinstance(rooms, dict) else None

    return ConversationSearchQuery(
        city=city,
        neighbourhoods=(neighbourhood,) if neighbourhood else (),
        property_types=(str(property_type),) if property_type else (),
        budget_xaf=_optional_amount(rent),
        min_bedrooms=_optional_amount(bedrooms, minimum=1),
    )


def _optional_amount(value: Any, *, minimum: int = 0) -> int | None:
    """An int that is not a bool and not below ``minimum``, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= minimum else None


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
    default_language: str,
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
        # The language of the *message* is the model's to read; this is only the
        # stand-in it keeps when the message is too short to tell.
        "default_language": (
            default_language if default_language in SUPPORTED_LANGUAGES else "fr"
        ),
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
    message, ask the next question) while the code still owns the order. Each
    flow walks its own form: a tenant is never shown the landlord's charges and
    deposit as fields still to come.
    """
    requirements = (
        SEARCH_REQUIREMENTS if flow is FlowName.PROPERTY_SEARCH else PROPERTY_REQUIREMENTS
    )
    fields: list[dict[str, str]] = []
    for name, step in requirements:
        if name in known:
            continue
        fields.append(
            {"field": name, "ask": question_topic(question_key_for(step, flow), flow) or ""}
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
    known_facts: dict[str, Any],
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
                    # Everything the session holds, not only what this message
                    # produced. A writer given one turn's extraction truthfully
                    # reports having no memory, then invents a number to fill
                    # the gap — and it is also how it asks for a field that was
                    # answered three turns ago. This turn's facts win, because a
                    # correction is the one case where they disagree.
                    "known_facts": {
                        **_public_known_facts(known_facts),
                        **(verdict.facts.as_context() if verdict.accepted else {}),
                    },
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
