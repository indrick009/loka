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
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from loka.bounded_contexts.ai.application.ports import (
    AiUsageLedger,
    AvailabilityAnswers,
    AvailabilityRequests,
    BudgetDecision,
    BudgetPolicy,
    ConversationExchanges,
    ConversationTurn,
    ConversationTurnRepository,
    Exchange,
    IntentModel,
    PropertyListing,
    PropertySearchGateway,
    ResponseModel,
    TenantDemands,
    TurnSummary,
    UnlockPayments,
    UnlockRequestOutcome,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.conversation_copy import (
    availability_answer_text,
    availability_pending_text,
    choice_out_of_range_text,
    demand_recorded_text,
    demand_waiting_text,
    listing_unsaved_text,
    missing_listing_reply,
    reply_kind,
    search_query,
    search_unavailable_text,
)
from loka.bounded_contexts.ai.application.services.decision_shadow import DecisionShadow
from loka.bounded_contexts.ai.application.services.deterministic_classifier import (
    ADDS_NEIGHBOURHOODS,
    DeterministicClassifier,
    DeterministicMatch,
)
from loka.bounded_contexts.ai.application.services.listing_recap import (
    kept_media_published_text,
    listing_recap_text,
    media_kept_text,
    media_received_text,
    media_refused_text,
    unsupported_media_text,
)
from loka.bounded_contexts.ai.application.services.match_proposals import (
    SHOWN_LISTINGS_KEY,
)
from loka.bounded_contexts.ai.application.services.prompts import (
    build_analysis_prompt,
    build_reply_prompt,
    collapse_repeats,
)
from loka.bounded_contexts.ai.application.services.unlock_copy import (
    PAYMENT_QUESTION_KEY,
    UNLOCK_KEY,
    PaymentAnswer,
    read_payment_answer,
    unlock_text,
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
    SEARCH_REQUIREMENTS,
    ExtractedFacts,
    FactExtractor,
    is_supported_intent,
    question_to_ask,
)
from loka.bounded_contexts.ai.infrastructure.adapters.conversation_availability import (
    is_availability_answer,
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
    StoredInboundMessage,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import (
    MessageKind,
    SharedLocation,
)
from loka.shared.application.context import current_context
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import DomainError, ExternalServiceUnavailable
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import (
    ai_bypass_total,
    llm_calls_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_tokens_total,
)

if TYPE_CHECKING:
    from loka.bounded_contexts.property.application.use_cases.create_property_from_conversation import (  # noqa: E501
        ConversationMediaResult,
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
    question is a tenant's. Phrasing a reply from ``session.flow`` on that turn
    hands a tenant the landlord's form — "standing", "charges", "deposit" — and
    the model then asks one of those instead of the question it was given.

    One function for the rule, deliberately: it used to exist twice, the two
    copies drifted, and the reply prompt ended up using neither.
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


_SHOWN_LISTINGS = SHOWN_LISTINGS_KEY

# What each WhatsApp kind becomes on a listing. A voice note or a sticker is
# absent on purpose: both used to be filed as photos of the lodging.
_LISTING_MEDIA: dict[MessageKind, str] = {
    MessageKind.IMAGE: "PHOTO",
    MessageKind.VIDEO: "VIDEO",
    MessageKind.DOCUMENT: "DOCUMENT",
}

# What the rewriter is told the user sent when there is no caption: told
# "(photo)" for a video, it wrote back that his photo was received.
_MEDIA_MARKERS: dict[MessageKind, str] = {
    MessageKind.IMAGE: "(photo envoyée)",
    MessageKind.VIDEO: "(vidéo envoyée)",
    MessageKind.DOCUMENT: "(document envoyé)",
    MessageKind.AUDIO: "(note vocale envoyée)",
    MessageKind.STICKER: "(sticker envoyé)",
}

# Photos sent before the listing exists — very common, and they used to be
# lost. Kept as ``[{"key", "kind"}]`` and attached the moment it is saved.
# The leading underscore keeps them out of every prompt.
PENDING_MEDIA_KEY = "_pending_media"

# How long an acknowledgement waits for the rest of an album.
PHOTO_BURST_SECONDS = 6

# Staged on a landlord's thread when a tenant picks his lodging. Set as the
# open question there, so his next message is read as the answer to it.
AVAILABILITY_QUESTION_KEY = "ask.availability_check"


def _availability_reading(
    pending: str | None,
    hint: DeterministicMatch | None,
    intent: str,
    entities: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """His answer to "le logement est encore disponible ?", when that is open.

    A rule, not a guess. Observed live: "Oui, il est toujours disponible" came
    back AFFIRMATIVE, was read as a reply to his photo question, and the tenant
    waiting on it was never released. The rule had it right; the model did not
    know the question was open.

    A bare "oui" is a yes to the question asked. A bare "non" is left to the
    model: taken for good or for now are different answers, and only one of
    them takes the listing out of search.
    """
    if pending != AVAILABILITY_QUESTION_KEY:
        return intent, entities
    if hint is not None and is_availability_answer(hint.intent):
        return hint.intent, {**entities, **hint.entities}
    if intent == "AFFIRMATIVE":
        return "AVAILABILITY_STILL_AVAILABLE", entities
    return intent, entities


def _carried_question(session: ConversationSession) -> str | None:
    """The question still open from an earlier turn, if any.

    One reader for two callers on purpose: the sentence the user receives and
    the question the session records are decided from the same value, so they
    cannot end up describing different questions.
    """
    carried = session.context.get("next_question")
    return carried if isinstance(carried, str) else None


def _facts_worth_carrying(context: dict[str, Any]) -> dict[str, Any]:
    """The real facts in a context, without the platform's bookkeeping.

    ``next_question`` is a question key and ``_``-prefixed keys are internal —
    carried into a fresh flow they would make the new form believe a question
    was asked, or offer a result list from a search that is over.
    """
    return {
        name: value
        for name, value in context.items()
        if name != "next_question" and not str(name).startswith("_")
    }


def _with_known_neighbourhoods(
    entities: dict[str, Any], session: ConversationSession
) -> dict[str, Any]:
    """Add the places already accepted when the user says this one comes on top.

    Only the rules set the marker; the model is asked to list them all itself.
    A place in another city starts over rather than being mixed with these.
    """
    if not entities.get(ADDS_NEIGHBOURHOODS):
        return entities
    merged = {name: value for name, value in entities.items() if name != ADDS_NEIGHBOURHOODS}
    known = session.context.get("location")
    if not isinstance(known, dict) or known.get("city") != merged.get("city"):
        return merged
    earlier = known.get("neighbourhoods") or [known.get("neighbourhood")]
    added = merged.get("neighbourhoods") or [merged.get("neighbourhood")]
    names = [str(name) for name in [*earlier, *added] if name]
    merged["neighbourhood"] = names[0] if names else merged.get("neighbourhood")
    merged["neighbourhoods"] = names
    return merged


def _keeping_the_place(
    entities: dict[str, Any], session: ConversationSession
) -> dict[str, Any]:
    """A pin and a district name are two halves of one place: keep both.

    Each used to replace the other. A landlord who shared his location and was
    then asked the neighbourhood lost the pin the moment he typed "Odza"; one
    who typed it first lost the name to the pin, which carries none. Merged
    only when both name the same city — a place in another city starts over.
    """
    known = session.context.get("location")
    if not isinstance(known, dict) or not known.get("city"):
        return entities
    pinned = isinstance(entities.get("latitude"), int | float)
    named = bool(entities.get("neighbourhood") or entities.get("neighbourhoods"))
    if pinned and not named and known.get("neighbourhood"):
        city = market.resolve_city_by_coordinates(entities["latitude"], entities["longitude"])
        if city == known["city"]:
            return {
                **entities,
                "neighbourhood": known["neighbourhood"],
                **(
                    {"neighbourhoods": known["neighbourhoods"]}
                    if known.get("neighbourhoods")
                    else {}
                ),
            }
    if named and not pinned and isinstance(known.get("latitude"), int | float):
        city = entities.get("city") or market.resolve_neighbourhood(
            str(entities.get("neighbourhood") or "")
        )
        if city in (None, "", known["city"]):
            return {
                **entities,
                "city": known["city"],
                "latitude": known["latitude"],
                "longitude": known["longitude"],
            }
    return entities


# How a question opens, when it does not end with "?" — WhatsApp users often
# leave it off. Kept short: a false "question" only re-shows the recap.
_QUESTION_OPENINGS = (
    "est-ce", "pourquoi", "comment", "combien", "quand", "quel", "quelle",
    "c'est quoi", "tu as", "as-tu", "what", "why", "how", "where", "when", "did ",
)


def _is_a_question(text: str | None) -> bool:
    """Whether a message asks something rather than answering.

    Observed live: "Tu as retenu la localisation ?", sent at the summary, was
    read as AFFIRMATIVE and the listing was registered — nobody had said yes.
    A confirmation publishes a listing; a question is never one.
    """
    stripped = (text or "").strip().casefold()
    return stripped.endswith("?") or stripped.startswith(_QUESTION_OPENINGS)


CONFIRM_QUESTION_KEY = "ask.confirm_property"

_AFFIRMATIONS = ("oui", "ok", "okay", "d'accord", "dac", "c'est bon", "vas y", "vas-y",
                 "publie", "go", "yes", "yep", "valide", "confirme", "exact", "parfait")
_HESITATIONS = ("non", "pas ", "attends", "attend ", "corrige", "change", "modifie",
                "erreur", "trompé", "trompe", "mais ")


def _confirms_the_summary(text: str | None) -> bool:
    """A plain yes to "confirmez-vous la publication ?", with no "but" in it."""
    stripped = (text or "").strip().casefold()
    if not stripped or _is_a_question(stripped):
        return False
    if any(word in stripped for word in _HESITATIONS):
        return False
    return stripped.startswith(_AFFIRMATIONS)


def _is_search(flow: FlowName) -> bool:
    """Which form a prompt shows: the tenant's search, or the landlord's.

    Every other flow, SUPPORT included, is shown the landlord's fields.
    """
    return flow is FlowName.PROPERTY_SEARCH


# Steps whose words the inline writer is never in a position to choose well,
# but whose sentence the platform replaces anyway, or that the analysis prompt
# tells it how to handle ("if none is missing, confirm the summary").
_STEPS_THE_WRITER_MAY_REACH = frozenset({FlowStep.CONFIRM_PROPERTY, FlowStep.PRESENT_RESULTS})


def _field_for_step(step: FlowStep, flow: FlowName) -> str | None:
    """The form field name a step collects, in this flow's form.

    The name is what the writer is handed in ``required_fields`` and reports
    back as ``asking``; ``None`` means the step is outside the form, so its
    question is not one the writer is ever asked to phrase.
    """
    form = SEARCH_REQUIREMENTS if _is_search(flow) else PROPERTY_REQUIREMENTS
    for name, form_step in form:
        if form_step is step:
            return name
    return None


def _inline_reply_fits(
    prompt_flow: FlowName,
    *,
    session: ConversationSession,
    verdict: _Verdict,
    asking: str | None = None,
) -> bool:
    """Whether the sentence written with the analysis asks what was decided.

    The inline writer only ever sees one form and picks its next missing field;
    the backend decides afterwards and may land elsewhere. Two ways it did:

    * the other side's form. "I'm looking for a modern studio in Bastos"
      missed the rules, the model was shown the landlord's fields, and the
      tenant was asked how many identical lodgings his listing offers;
    * a step outside any form. The backend asked "publier ou chercher ?" and
      the sentence asked for the property type, so the user answered a
      question the session was not waiting for.

    Either way the sentence is dropped and written again against the decision.

    A refused turn is a third way: the model read "Juste un seul" with no
    confidence, the turn was refused and the quantity kept open, yet its
    sentence had already moved on to the rent. The landlord's next number
    would have been filed against a question he no longer thought was open.

    A turn that recorded no field of an open form question is a fourth: the
    writer answered as if the fact had been handed over, so its sentence asks
    the *following* question while the session still waits for the current one.

    A fifth is the same drift with a field recorded: the writer moved past a
    field it did not fill. The writer now names the field it asks about
    (``asking``); when that name is not the field the backend kept open, the
    sentence asked the wrong question and is written again.
    """
    if not verdict.accepted:
        return False
    target = _target_flow(session, verdict.intent)
    if _is_search(prompt_flow) is not _is_search(target):
        return False
    step = verdict.next_step
    if step is None or step in _STEPS_THE_WRITER_MAY_REACH:
        return True
    field = _field_for_step(step, target)
    if field is None:
        return False
    # A form step, and the turn recorded nothing: the inline writer only ever
    # saw the message, so it may have *read* a fact it failed to hand over and
    # written the question *after* the one the backend kept open. Observed live:
    # a landlord's "elle sont inclus" produced no charges fact, the writer moved
    # on to the deposit, and the session stayed on the charges — so his later
    # "aucune", meant for the minimum duration, was filed as "no charges" and
    # the published listing said the water was charged separately. With nothing
    # recorded there is nothing to phrase an answer around, so the question the
    # backend decided is the one asked again.
    if not verdict.facts.facts:
        return False
    # The writer that said which field its sentence asks about is held to it:
    # a name other than the open field means the sentence moved on, and the
    # record and the sentence would describe two different questions. A writer
    # that stayed silent keeps today's behaviour — the sentence is used, as the
    # guard cannot contradict it.
    return asking is None or asking == field


def _while_waiting(
    session: ConversationSession, *, intent: str, next_step: FlowStep | None
) -> str | None:
    """The platform's answer to a tenant writing again while his demand waits.

    Only when the turn changed nothing: a new criterion re-records the demand
    and is acknowledged as such, and opening another flow is its own answer.
    Everything else — "tu me reviens quand ?", "merci", "do you have something
    now?" — gets his criteria back and the truth that nothing is ready yet,
    never a model's sentence promising a delay or a visit.
    """
    if session.step is not FlowStep.AWAITING_MATCH or next_step is not None:
        return None
    if _opens_new_flow(session, intent):
        return None
    query = search_query(session.context)
    if query is None:
        return None
    return demand_waiting_text(query, session.language)


def _nth_listing(shown: Any, choice: int) -> uuid.UUID | None:
    """The listing behind a one-based line number, or ``None``.

    Defensive about the stored value because it comes back out of a JSON
    column: a malformed context must leave the tenant with a question, never a
    crash and never the wrong lodging.
    """
    if not isinstance(shown, list) or not 1 <= choice <= len(shown):
        return None
    try:
        return uuid.UUID(str(shown[choice - 1]))
    except (ValueError, AttributeError, TypeError):
        return None


def _as_uuid(value: Any) -> uuid.UUID | None:
    """A stored id read back out of a JSON column, or ``None`` if unusable.

    Defensive like :func:`_nth_listing`: a malformed booking must leave the
    tenant with a question, never start a withdrawal against a garbage id.
    """
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


def _known_facts(session: ConversationSession, intent: str) -> dict[str, Any]:
    """What this turn may build on: nothing from an abandoned attempt.

    One exception, and it is not an abandoned attempt at all: a user answering
    "publier ou chercher ?" is finishing the sentence he started. Dropping what
    he said a moment earlier would ask him for the studio he has just named.
    """
    if session.step is FlowStep.CLARIFY_ROLE:
        return dict(session.context)
    return {} if _opens_new_flow(session, intent) else dict(session.context)




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
        availability: AvailabilityAnswers | None = None,
        availability_requests: AvailabilityRequests | None = None,
        unlock_payments: UnlockPayments | None = None,
        demands: TenantDemands | None = None,
        search: PropertySearchGateway | None = None,
        shadow: DecisionShadow | None = None,
        exchanges: ConversationExchanges | None = None,
    ) -> None:
        self._uow = uow
        self._exchanges = exchanges
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
        self._availability = availability
        self._availability_requests = availability_requests
        self._unlock_payments = unlock_payments
        self._demands = demands
        self._search = search
        self._shadow = shadow

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
        # The words, not only the labels: what the user and Kora actually said.
        thread = (
            await self._exchanges.recent(session.id, excluding=command.message_id)
            if self._exchanges is not None
            else []
        )

        # Nothing has been written yet, so releasing the transaction costs
        # nothing and the model call never holds a database connection.
        await self._uow.commit()

        # Asked before the turn and read after it: the decision model works
        # while the pipeline does, and the user waits for neither. A pin or a
        # photo carries no sentence to decide on.
        shadow = (
            self._shadow.start(session, message_id=command.message_id, text=message.text or "")
            if self._shadow is not None
            and message.location is None
            and message.media_object_key is None
            else None
        )
        try:
            report = await self._analyse(command, message, session, history, thread, moment)
        except BaseException:
            if shadow is not None:
                DecisionShadow.cancel(shadow)
            raise
        if shadow is not None and self._shadow is not None:
            await self._shadow.finish(
                shadow,
                intent=report.intent,
                accepted=report.accepted,
                used_llm=report.used_llm,
                next_step=report.next_step,
                now=moment,
            )
        return report

    async def _analyse(
        self,
        command: AnalysisCommand,
        message: StoredInboundMessage,
        session: ConversationSession,
        history: list[TurnSummary],
        thread: list[Exchange],
        moment: datetime,
    ) -> AnalysisReport:
        """Read one loaded message and answer it."""
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
        if message.media_refused is not None:
            # Too heavy to store — the gateway still forwarded it so he hears
            # why, instead of a video that vanished without a word.
            ai_bypass_total.labels(stage="media").inc()
            return await self._finish(
                session=session,
                message_id=command.message_id,
                intent="UNKNOWN",
                confidence=1.0,
                entities={},
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=moment,
                reason="media_refused",
                reply_text=media_refused_text(message.media_refused, session.language),
                text=text,
                media_marker=_MEDIA_MARKERS.get(message.kind, "(fichier envoyé)"),
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
            intent, entities = self._read_open_question(
                pending_key=pending_key,
                hint=shortcut,
                intent=shortcut.intent,
                entities=dict(shortcut.entities),
                text=text,
            )
            return await self._finish(
                session=session,
                message_id=command.message_id,
                intent=intent,
                confidence=shortcut.confidence,
                entities=entities,
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=moment,
                text=text,
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
                text=text,
            )

        ai_bypass_total.labels(stage="llm").inc()
        # A cheap rule reading only picks which form the prompt shows; the model
        # still chooses the intent. On an opening turn it is what tells a tenant
        # the search fields instead of the landlord's.
        hint = self._classifier.classify(text, pending=pending_key)
        prompt_flow = (
            _target_flow(session, hint.intent) if hint is not None else session.flow
        )
        prompt = build_analysis_prompt(
            text=message.text,
            default_language=session.language,
            history=history,
            flow=prompt_flow,
            step=session.step,
            pending_question=pending_key,
            known_facts=dict(session.context),
            exchanges=thread,
        )
        try:
            result = await self._model.understand_intent(prompt)
        except ExternalServiceUnavailable as exc:
            # The provider is down, timing out, or answering with something
            # unusable. Letting this escape meant the broker retried the
            # message a few times and then parked it in the DLQ, and the user's
            # message was never answered at all — the exact silence this
            # pipeline exists to prevent.
            #
            # Observed live: the primary model failed on transport, the
            # fallback returned an empty completion, then invalid JSON, and the
            # turn was retried twice on its way to being dropped.
            #
            # The rule set and the templates are already the answer when no key
            # is configured or the budget is spent; a provider outage is the
            # same situation. A worse reply beats no reply, and the turn is
            # recorded so the outage stays measurable.
            _logger.warning(
                "analysis_degraded_to_rules",
                session_id=str(session.id),
                message_id=str(command.message_id),
                error=exc.message,
            )
            ai_bypass_total.labels(stage="provider_down").inc()
            return await self._without_a_model(
                session,
                message_id=command.message_id,
                text=text,
                pending=pending_key,
                now=moment,
            )
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
        intent = result.intent
        intent, entities = self._read_open_question(
            pending_key=pending_key, hint=hint, intent=intent, entities=entities, text=text
        )
        verdict = self._verdict(
            session=session,
            intent=intent,
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
        if not _inline_reply_fits(
            prompt_flow, session=session, verdict=verdict, asking=result.asking
        ):
            reply_text = None
        reply_usage: LlmUsage | None = None
        if reply_text is None:
            reply_text, reply_usage = await self._compose_reply(
                verdict=verdict,
                text=text,
                session=session,
                history=history,
                thread=thread,
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
            text=text,
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
            # Answered by stock copy, in "vous", with no word about the pin:
            # the rewriter is told what he sent so the reply can say so.
            media_marker="(position WhatsApp partagée)",
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

        A photo sent before the listing exists is kept and attached once it is
        saved. Its acknowledgement names the photo (``media_burst_of``), so an
        album of ten is answered once, by its last photo, instead of ten times.
        """
        ai_bypass_total.labels(stage="media").inc()
        media_kind = _LISTING_MEDIA.get(kind)
        if media_kind is None:
            return await self._finish(
                session=session,
                message_id=message_id,
                intent="UNKNOWN",
                confidence=1.0,
                entities={},
                used_llm=False,
                latency_ms=0,
                cost_usd=Decimal("0"),
                now=now,
                reason="media_unsupported",
                reply_text=unsupported_media_text(kind.value, session.language),
                text=text,
                media_marker=_MEDIA_MARKERS.get(kind, "(fichier envoyé)"),
            )
        property_id = session.assigned_property_id
        reason: str | None = None
        reply_text: str | None = None
        settles_on: FlowStep | None = None
        if self._listing is None or property_id is None:
            if session.flow is FlowName.PROPERTY_SEARCH or self._listing is None:
                reason = "media_without_a_listing"
                _logger.warning(
                    "media_without_a_listing",
                    session_id=str(session.id),
                    object_key=object_key,
                    kind=media_kind,
                )
            else:
                reason = "media_kept_for_listing"
                reply_text = await self._keep_for_listing(
                    session, object_key=object_key, kind=media_kind, now=now
                )
        else:
            try:
                attached = await self._listing.attach_media_from_conversation(
                    property_id=property_id,
                    object_key=object_key,
                    kind=media_kind,
                    now=now,
                )
                if attached is not None and attached.published:
                    # The listing is online: the conversation is too. Left on
                    # COLLECT_MEDIA, it kept asking for the photos it had.
                    settles_on = FlowStep.PUBLISHED
                if attached is not None:
                    reply_text = media_received_text(
                        kind=media_kind,
                        photos=attached.photos,
                        published=attached.published,
                        just_published=attached.just_published,
                        language=session.language,
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
            reply_text=reply_text,
            text=text,
            media_burst_of=message_id,
            media_marker=_MEDIA_MARKERS.get(kind, "(fichier envoyé)"),
            settles_on=settles_on,
        )

    async def _keep_for_listing(
        self, session: ConversationSession, *, object_key: str, kind: str, now: datetime
    ) -> str:
        """Hold a photo sent before its listing exists, and say so."""
        observed = session.revision
        earlier = session.context.get(PENDING_MEDIA_KEY)
        kept = [
            *(earlier if isinstance(earlier, list) else []),
            {"key": object_key, "kind": kind},
        ]
        session.merge_context({PENDING_MEDIA_KEY: kept}, now=now)
        await self._sessions.save(session, expected_revision=observed)
        return media_kept_text(
            count=len(kept),
            photos_only=all(item.get("kind") == "PHOTO" for item in kept),
            listing_started=session.flow in LISTING_FLOWS,
            language=session.language,
        )

    async def _attach_kept_media(
        self, session: ConversationSession, property_id: uuid.UUID, *, now: datetime
    ) -> ConversationMediaResult | None:
        """The photos sent early, onto the listing just saved."""
        kept = session.context.get(PENDING_MEDIA_KEY)
        if self._listing is None or not isinstance(kept, list):
            return None
        result: ConversationMediaResult | None = None
        for item in kept:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                continue
            try:
                result = await self._listing.attach_media_from_conversation(
                    property_id=property_id,
                    object_key=item["key"],
                    kind=str(item.get("kind") or "PHOTO"),
                    now=now,
                ) or result
            except DomainError as exc:
                _logger.warning(
                    "kept_media_refused", session_id=str(session.id), error=exc.message
                )
        return result

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
                text=text,
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
            text=text,
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
        reply_text: str | None = None,
        text: str | None = None,
        media_burst_of: uuid.UUID | None = None,
        media_marker: str | None = None,
        settles_on: FlowStep | None = None,
    ) -> AnalysisReport:
        """Close a call that did not reach the model.

        No model writes the reply; ``reply_text`` is platform text when the
        turn has a fact of its own to report, such as a photo just received.
        ``text`` is the raw message, handed on so a step that is read by a rule
        alone — the 1 000 FCFA answer — can be decided without a model.
        """
        verdict = self._verdict(
            session=session,
            intent=intent,
            confidence=confidence,
            entities=entities,
            reason=reason,
        )
        if settles_on is not None and verdict.accepted:
            verdict = replace(verdict, next_step=settles_on)
        return await self._apply(
            session=session,
            message_id=message_id,
            text=text,
            verdict=verdict,
            confidence=confidence,
            used_llm=used_llm,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            now=now,
            reply_text=reply_text,
            media_burst_of=media_burst_of,
            media_marker=media_marker,
        )

    def _read_open_question(
        self,
        *,
        pending_key: str | None,
        hint: DeterministicMatch | None,
        intent: str,
        entities: dict[str, Any],
        text: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Apply the rules that only mean something while a question is open.

        Both lanes end here — the fast regex lane and the model lane — so a bare
        "oui" answers "le logement est encore disponible ?" whichever one read
        it. Observed live: the regex lane returned AFFIRMATIVE for a landlord's
        "oui", skipped this reading, and the tenant waiting on his answer stayed
        blocked until the landlord rephrased as "oui c'est disponible".

        A "non" the model read as the bare dialogue act still answers the open
        question when that question has exactly one "no": no caution, neither
        guard nor parking, a single lodging. Observed live: "Non, ni gardien ni
        parking" came back NEGATIVE, nothing was recorded, and the landlord was
        asked about "other equipment" until he gave up.
        """
        denial = (
            self._classifier.denial_answer(pending_key) if intent == "NEGATIVE" else None
        )
        if denial is not None:
            intent, entities = denial.intent, {**denial.entities, **entities}
        if pending_key == CONFIRM_QUESTION_KEY and _confirms_the_summary(text):
            # The most consequential answer in the flow is read by a rule, not
            # left to a label. Observed live: "oui c'est bon publie" came back
            # CREATE_PROPERTY, the listing was never registered, and the
            # landlord was shown his summary again.
            intent = "AFFIRMATIVE"
        return _availability_reading(pending_key, hint, intent, entities)

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

        facts = self._extractor.validate(
            _keeping_the_place(_with_known_neighbourhoods(entities, session), session)
        )
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
        thread: list[Exchange] | None = None,
    ) -> tuple[str | None, LlmUsage | None]:
        """Let the model phrase the answer, or return ``(None, None)``.

        ``None`` means "use the template": either no response model is
        configured, or writing it failed. A reply failure must never cost the
        user their turn, so the exception is swallowed here and the outbound
        layer falls back to its stable copy.
        """
        if self._response_model is None or not text.strip():
            return None, None

        # A refused turn moved nothing, so the question still open is the one
        # to ask again — with the rephrase request, never past it.
        next_key: str | None = _carried_question(session)
        if verdict.accepted:
            next_key = question_to_ask(
                next_step=verdict.next_step,
                flow=_target_flow(session, verdict.intent),
                pending=_carried_question(session),
            )
            # A turn that advanced nothing leaves the previous question open,
            # and the writer has to be told *that* one. Handed no question at
            # all it invents one, and then the sentence and the session
            # disagree about what was asked.
            #
            # Observed live: a landlord answered "aucun" to the caution, the
            # extractor recorded no value, so no step was decided and the
            # writer was given nothing to ask. It wrote "D'accord, pas de
            # caution. Combien de mois d'avance ?" — so he was asked the
            # advance while the session still waited for the caution, and his
            # "6" would have been filed as six months of caution. Silently.
            #
            # The same rule as ``_advance_session``, through one helper, so the
            # question asked and the question recorded cannot drift apart again.
        prompt = build_reply_prompt(
            text=text,
            language=session.language,
            intent=verdict.intent,
            accepted=verdict.accepted,
            accepted_facts=verdict.facts.as_context(),
            next_key=next_key,
            history=history,
            # The same flow ``next_key`` was computed from, and not
            # ``session.flow``: on the message that *opens* a flow the session
            # still reads SUPPORT, so the writer was handed the wrong role and
            # the wrong form. Observed live: a tenant said he was looking for a
            # studio, the backend decided "where are you looking?", and the
            # writer asked "moderne ou non ?" — a landlord field. He answered
            # it, the answer was filed as a bare "oui" against a question
            # nobody had asked him, and his "moderne" was lost.
            flow=_target_flow(session, verdict.intent),
            known_facts=dict(session.context),
            exchanges=thread,
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
        text: str | None = None,
        media_burst_of: uuid.UUID | None = None,
        media_marker: str | None = None,
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

        # The 1 000 FCFA answer is read by a rule and never by the model: a
        # misread charges the wrong number, or charges twice. It is settled here,
        # before the ordinary flow, and closes the turn on its own.
        if session.step in (FlowStep.CONFIRM_PAYMENT, FlowStep.AWAITING_PAYMENT):
            return await self._settle_payment_turn(
                session,
                message_id=message_id,
                text=text,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
            )

        if accepted:
            availability_text = await self._record_availability_answer(
                session, intent=intent, now=now
            )
            listing_text, next_step, listing_id = await self._confirm_listing(
                session, intent=intent, next_step=next_step, now=now, text=text
            )
            search_text, next_step = await self._record_demand(
                session, intent=intent, facts=facts, next_step=next_step, now=now
            )
            choice_text, next_step = await self._choose_listing(
                session, intent=intent, facts=facts, next_step=next_step, now=now
            )
            # The values the landlord is asked to confirm, as stored. Rebuilt
            # on every turn that lands here, so a correction shows at once.
            recap_text = (
                listing_recap_text(
                    {**_known_facts(session, intent), **facts.as_context()}, session.language
                )
                if next_step is FlowStep.CONFIRM_PROPERTY
                else None
            )
            # Read before the session moves: it is where he *was* that says his
            # message is a follow-up on a demand already waiting.
            waiting_text = _while_waiting(session, intent=intent, next_step=next_step)
            question = await self._advance_session(
                session,
                intent,
                facts,
                next_step,
                now=now,
                attach_property_id=listing_id,
            )
            # The catalogue's numbers are data, not prose: they win over both
            # the model's sentence and the listing summary, which cannot apply
            # to the same turn.
            platform_text = (
                availability_text or search_text or waiting_text or choice_text or listing_text
            )
            reply_text = platform_text or recap_text or reply_text
        else:
            # Not understood, but nothing he says now can be misfiled: the
            # demand is kept and no question is open. Observed live with the
            # model timing out: "tu me reviens quand ?" reached the rules alone
            # and was told to rephrase. His criteria and the truth beat that.
            platform_text = _while_waiting(session, intent=intent, next_step=None)
            reply_text = platform_text or reply_text

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
            # Stock copy goes out in the user's voice; a recap to confirm and
            # the model's own sentence go out as they are. A photo's caption
            # may be empty, and its acknowledgement is stock copy too.
            answering=(
                (text or media_marker)
                if media_marker is not None
                # Stock copy too: the fast lane ("1", "60mil", "6") answers
                # with a template, which used to go out in "vous" mid-thread.
                else text
                if (platform_text and reply_text == platform_text) or reply_text is None
                else None
            ),
            media_burst_of=media_burst_of,
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
        answering: str | None = None,
        media_burst_of: uuid.UUID | None = None,
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

    async def _settle_payment_turn(
        self,
        session: ConversationSession,
        *,
        message_id: uuid.UUID,
        text: str | None,
        intent: str,
        confidence: float,
        used_llm: bool,
        latency_ms: int,
        cost_usd: Decimal,
        now: datetime,
    ) -> AnalysisReport:
        """Read the tenant's answer to the 1 000 FCFA offer.

        Platform text and one rule, never the model: "oui" must mean pay with
        the number shown, a number must mean that number, and nothing else may
        start a withdrawal. Only reached on the payment steps, and it always
        returns a finished report.
        """
        language = session.language
        phone = session.phone.e164

        if session.step is FlowStep.AWAITING_PAYMENT:
            # The withdrawal is on his phone; only the provider's callback moves
            # him on, never his word that he paid.
            return await self._close_payment_turn(
                session,
                next_step=FlowStep.AWAITING_PAYMENT,
                question_key=PAYMENT_QUESTION_KEY,
                reply=unlock_text("waiting", language, phone=phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
            )

        booking = session.context.get(UNLOCK_KEY)
        property_id = landlord_id = None
        if isinstance(booking, dict):
            property_id = _as_uuid(booking.get("property_id"))
            landlord_id = _as_uuid(booking.get("landlord_id"))
        if property_id is None or landlord_id is None or session.user_id is None:
            # Nothing to unlock: the booking is gone or never existed. Re-asking
            # is honest; inventing a listing to charge for is not.
            return await self._close_payment_turn(
                session,
                next_step=FlowStep.CONFIRM_PAYMENT,
                question_key=PAYMENT_QUESTION_KEY,
                reply=unlock_text("not_started", language, phone=phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
            )

        def back_to_his_list() -> FlowStep:
            """Where a tenant goes when the listing he picked falls through.

            His results when he still has a list to choose from — otherwise
            waiting for a match — so a dead end never becomes a dead thread.
            """
            shown = session.context.get(_SHOWN_LISTINGS)
            return (
                FlowStep.COLLECT_PROPERTY_CHOICE
                if isinstance(shown, list) and shown
                else FlowStep.AWAITING_MATCH
            )

        answer, other_number = read_payment_answer(text or "")
        if answer is PaymentAnswer.CANCEL:
            # Nothing is charged. He is sent back to his results when there are
            # any left, and to waiting otherwise — never left with no way out.
            return await self._close_payment_turn(
                session,
                next_step=back_to_his_list(),
                question_key=None,
                reply=unlock_text("cancelled", language, phone=phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
                clear_unlock=True,
            )
        if answer is PaymentAnswer.UNCLEAR or self._unlock_payments is None:
            return await self._close_payment_turn(
                session,
                next_step=FlowStep.CONFIRM_PAYMENT,
                question_key=PAYMENT_QUESTION_KEY,
                reply=unlock_text("unclear", language, phone=phone)
                if answer is PaymentAnswer.UNCLEAR
                else unlock_text("not_started", language, phone=phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
            )

        # ``read_payment_answer`` only returns a number on OTHER_NUMBER, but the
        # type is optional; falling back to the thread's own number keeps the
        # request well-formed rather than crashing the turn.
        payer_phone = (
            other_number
            if answer is PaymentAnswer.OTHER_NUMBER and other_number
            else phone
        )
        outcome = await self._unlock_payments.request(
            tenant_id=session.user_id,
            property_id=property_id,
            landlord_id=landlord_id,
            payer_phone=payer_phone,
            now=now,
        )
        if outcome is UnlockRequestOutcome.REQUESTED:
            return await self._close_payment_turn(
                session,
                next_step=FlowStep.AWAITING_PAYMENT,
                question_key=PAYMENT_QUESTION_KEY,
                reply=unlock_text("requested", language, phone=payer_phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
            )
        if outcome is UnlockRequestOutcome.ALREADY_UNLOCKED:
            return await self._close_payment_turn(
                session,
                next_step=FlowStep.DONE,
                question_key=None,
                reply=unlock_text("already", language, phone=phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
                clear_unlock=True,
            )
        if outcome is UnlockRequestOutcome.NOT_CONFIRMED:
            # The confirmation was stale — the listing went between the offer
            # and his yes. Nothing is charged, and he is put back on his list.
            return await self._close_payment_turn(
                session,
                next_step=back_to_his_list(),
                question_key=None,
                reply=unlock_text("not_confirmed", language, phone=phone),
                message_id=message_id,
                intent=intent,
                confidence=confidence,
                used_llm=used_llm,
                latency_ms=latency_ms,
                cost_usd=cost_usd,
                now=now,
                clear_unlock=True,
            )
        # FAILED: nothing was recorded as owed, so another attempt is honest.
        return await self._close_payment_turn(
            session,
            next_step=FlowStep.CONFIRM_PAYMENT,
            question_key=PAYMENT_QUESTION_KEY,
            reply=unlock_text("failed", language, phone=payer_phone),
            message_id=message_id,
            intent=intent,
            confidence=confidence,
            used_llm=used_llm,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            now=now,
        )

    async def _close_payment_turn(
        self,
        session: ConversationSession,
        *,
        next_step: FlowStep,
        question_key: str | None,
        reply: str,
        message_id: uuid.UUID,
        intent: str,
        confidence: float,
        used_llm: bool,
        latency_ms: int,
        cost_usd: Decimal,
        now: datetime,
        clear_unlock: bool = False,
    ) -> AnalysisReport:
        """Move the payment step, stage its one sentence, and commit.

        The turn was already recorded by ``_apply``; this closes it exactly like
        the ordinary path would, so the session state and the sentence cannot
        describe different steps.
        """
        observed = session.revision
        session.transition(next_step, now=now, expected_revision=observed)
        session.record_pending_question(question_key)
        if clear_unlock:
            session.context.pop(UNLOCK_KEY, None)
        await self._sessions.save(session, expected_revision=observed)
        await self._stage_reply(
            session,
            intent=intent,
            reason=None,
            question_key=question_key,
            text=reply,
            now=now,
        )
        await self._uow.commit()
        return AnalysisReport(
            session_id=session.id,
            message_id=message_id,
            intent=intent,
            confidence=confidence,
            used_llm=used_llm,
            accepted=True,
            reason=None,
            next_step=next_step.value,
            question_key=question_key,
            cost_usd=cost_usd,
        )

    async def _record_availability_answer(
        self,
        session: ConversationSession,
        *,
        intent: str,
        now: datetime,
    ) -> str | None:
        """Apply "le logement est encore disponible ?" to the listing it was about.

        These intents were recognised and then discarded: a landlord could
        write "c'est déjà loué" and the lodging stayed in every tenant's search
        results. Nothing else in the system can take a listing out — Kora never
        infers availability — so this answer is the only thing that does.

        ``None`` when the turn was not one of these answers, or when the
        landlord was never asked: the reply then comes from wherever it usually
        would.
        """
        if self._availability is None or session.user_id is None:
            return None
        if not is_availability_answer(intent):
            return None

        result = await self._availability.record_answer(
            landlord_id=session.user_id, answer=intent, now=now
        )
        if result is None:
            return None
        return availability_answer_text(
            session.language,
            lodgings_left=result.lodgings_left,
            delisted=result.delisted,
        )

    async def _confirm_listing(
        self,
        session: ConversationSession,
        *,
        intent: str,
        next_step: FlowStep | None,
        now: datetime,
        text: str | None = None,
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
        if _is_a_question(text):
            # Not a yes: the summary is shown again, with the place it holds,
            # and the confirmation is still his to give.
            return None, FlowStep.CONFIRM_PROPERTY, None

        result = await self._listing.register_from_conversation(
            user_id=session.user_id,
            facts=dict(session.context),
            existing_property_id=session.assigned_property_id,
            now=now,
        )
        if result is None:
            return listing_unsaved_text(session.language), next_step, None

        kept = await self._attach_kept_media(session, result.property_id, now=now)
        if kept is not None and kept.published and not result.published:
            # The photos he sent early were all the listing was waiting for.
            return (
                kept_media_published_text(
                    count=kept.photos, photos_only=True, language=session.language
                ),
                FlowStep.PUBLISHED,
                result.property_id,
            )
        if result.published:
            return None, FlowStep.PUBLISHED, result.property_id
        return (
            missing_listing_reply(result.missing, session.language),
            FlowStep.COLLECT_MEDIA,
            result.property_id,
        )

    async def _choose_listing(
        self,
        session: ConversationSession,
        *,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        now: datetime,
    ) -> tuple[str | None, FlowStep | None]:
        """Turn the tenant's line number into a question for the landlord.

        This is the step the thread used to die on: the list was printed, the
        number was asked for, the answer was understood — and nothing consumed
        it. Kora says nothing about a reservation and promises no visit, only
        that the landlord is being asked, because that is the only thing that
        actually happens (housing spec §9).

        A number naming nothing is re-asked rather than resolved to the nearest
        listing: charging 1 000 FCFA for a lodging the tenant never saw is
        worse than one more question. The one exception is a *single* result —
        there, "je prends" names the only lodging there is, and re-asking for a
        number the tenant has nothing to choose between is the same dead end
        one turn later.
        """
        # Keyed on where the session *is*, not on where the form recompute
        # would go: ``next_step`` walks the tenant's criteria again and would
        # send him back to the type question, which is not what answering a
        # choice means. Same rule as ``_confirm_listing``.
        if session.step is not FlowStep.COLLECT_PROPERTY_CHOICE:
            return None, next_step
        choice = facts.get("property_choice")
        if not isinstance(choice, int) or isinstance(choice, bool):
            shown = session.context.get(_SHOWN_LISTINGS)
            if intent == "AFFIRMATIVE" and isinstance(shown, list) and len(shown) == 1:
                choice = 1
            else:
                return None, FlowStep.COLLECT_PROPERTY_CHOICE
        if self._availability_requests is None:
            return None, FlowStep.COLLECT_PROPERTY_CHOICE

        listing_id = _nth_listing(session.context.get(_SHOWN_LISTINGS), choice)
        if listing_id is None:
            # Either he answered a list he was never shown, or the number is
            # past its end. Both leave him on the same step, asked again.
            return (
                choice_out_of_range_text(session.language),
                FlowStep.COLLECT_PROPERTY_CHOICE,
            )

        asked = await self._availability_requests.request(
            property_id=listing_id, tenant_id=session.user_id, now=now
        )
        if not asked:
            return (
                choice_out_of_range_text(session.language),
                FlowStep.COLLECT_PROPERTY_CHOICE,
            )
        return availability_pending_text(session.language), FlowStep.AWAITING_AVAILABILITY

    async def _record_demand(
        self,
        session: ConversationSession,
        *,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        now: datetime,
    ) -> tuple[str | None, FlowStep | None]:
        """Take the tenant's search and promise nothing.

        Kora does not answer a search in the thread. It records the demand and
        says so — whether a lodging matches or not — because a direct match, a
        neighbouring area or a price a little above all need preparing, and
        §8's negotiation with a landlord cannot happen in the time a reply
        takes.

        This used to print five listings and ask for a number. That made Kora
        answer instantly at the cost of answering badly: with nothing in the
        catalogue it told the tenant to change his own criteria, and when he
        asked which neighbourhoods had studios it repeated the instruction.

        Only ``PRESENT_RESULTS`` reaches this, which is the step the extractor
        hands over exactly when the tenant's form is filled.
        """
        if next_step is not FlowStep.PRESENT_RESULTS:
            return None, next_step
        if session.step is FlowStep.COLLECT_PROPERTY_CHOICE and isinstance(
            facts.get("property_choice"), int
        ):
            # His "1" picks a lodging he was sent; it is not his search again.
            # Read as one, it re-opened the demand that list had just answered,
            # was matched again and sent him the same list a second time.
            return None, next_step
        stated = facts.as_context()
        if session.step is FlowStep.AWAITING_AVAILABILITY and not any(
            name in stated and stated[name] != session.context.get(name)
            for name, _ in SEARCH_REQUIREMENTS
        ):
            # Same trap one step later. Observed live: his lodging was already
            # put to its owner, "ok je prends le premier" re-recorded the
            # demand and the same lodging was proposed to him again. Only a new
            # criterion is a new search; anything else waits on the owner.
            return availability_pending_text(session.language), FlowStep.AWAITING_AVAILABILITY
        if self._demands is None or session.user_id is None:
            # Nowhere to keep the demand, so nothing to come back to: a
            # deployment that never wired the port fails as a sentence, not as
            # a model reply promising a follow-up.
            return search_unavailable_text(session.language), next_step

        query = search_query({**_known_facts(session, intent), **facts.as_context()})
        if query is None:
            # The form reported complete but the facts name no place to search.
            # Acknowledging would promise a follow-up on a request nobody can
            # act on, so he is asked which criterion to give instead.
            return search_unavailable_text(session.language), FlowStep.COLLECT_SEARCH_CRITERIA

        recorded = await self._demands.record(
            tenant_id=session.user_id, query=query, now=now
        )
        if not recorded:
            return search_unavailable_text(session.language), FlowStep.COLLECT_SEARCH_CRITERIA
        return demand_recorded_text(session.language), FlowStep.AWAITING_MATCH

    async def _advance_session(
        self,
        session: ConversationSession,
        intent: str,
        facts: ExtractedFacts,
        next_step: FlowStep | None,
        *,
        now: datetime,
        attach_property_id: uuid.UUID | None = None,
        extra_context: dict[str, Any] | None = None,
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
        pending = _carried_question(session)
        # Answered, the availability question is closed and he is back in the
        # form he was filling. Left open, his next photo caption would have
        # been read as a second answer about a lodging already settled.
        resumed_step: FlowStep | None = None
        if pending == AVAILABILITY_QUESTION_KEY and is_availability_answer(intent):
            pending, resumed_step = None, session.step

        # Read before the flow opens, because opening it clears the context:
        # the facts stated in the same breath as "publier ou chercher ?"
        # belong to the flow about to start.
        carried = (
            _facts_worth_carrying(session.context)
            if session.step is FlowStep.CLARIFY_ROLE
            else {}
        )

        opening_flow = FLOW_BY_OPENING_INTENT.get(intent)
        if opening_flow is not None and _opens_new_flow(session, intent):
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
        session.merge_context(
            {**carried, **facts.as_context(), **(extra_context or {}), **released}, now=now
        )
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


