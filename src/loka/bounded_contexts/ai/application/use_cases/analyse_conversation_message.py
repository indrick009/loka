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
    PropertySearchGateway,
    ResponseModel,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.conversation_copy import (
    SEARCH_RESULT_LIMIT,
    listing_unsaved_text,
    missing_listing_reply,
    no_results_text,
    reply_kind,
    results_text,
    search_query,
    search_unavailable_text,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.ai.application.services.prompts import (
    build_analysis_prompt,
    build_reply_prompt,
    collapse_repeats,
)
from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.ai.domain.services.ai_provider import (
    SUPPORTED_LANGUAGES,
    LlmUsage,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    FLOW_BY_OPENING_INTENT,
    LISTING_FLOWS,
    MIN_ACCEPTED_CONFIDENCE,
    PROPERTY_REQUIREMENTS,
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
            intent=verdict.intent,
            accepted=verdict.accepted,
            accepted_facts=verdict.facts.as_context(),
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
        reply_text = collapse_repeats(reply_text) if reply_text else reply_text

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
                    reply_kind=reply_kind(reason=reason, question_key=question_key),
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
            return listing_unsaved_text(session.language), next_step, None

        if result.published:
            return None, FlowStep.PUBLISHED, result.property_id
        return (
            missing_listing_reply(result.missing, session.language),
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
            return search_unavailable_text(session.language), next_step
        query = search_query({**_known_facts(session, intent), **facts.as_context()})
        if query is None:
            # The form reported complete but the facts describe no place to
            # search: calling that "no match" would be a lie the tenant cannot
            # act on, so it is reported as a failure instead.
            return search_unavailable_text(session.language), next_step
        hits = await self._search.search_for_conversation(query, limit=SEARCH_RESULT_LIMIT)
        if not hits:
            return no_results_text(session.language), FlowStep.COLLECT_SEARCH_CRITERIA
        return results_text(hits, session.language), FlowStep.COLLECT_PROPERTY_CHOICE

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


