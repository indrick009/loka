"""Ports of the AI context.

``IntentModel`` is intentionally narrower than
:class:`~loka.bounded_contexts.ai.domain.services.ai_provider.AIProvider`: the
analysis pipeline needs intent understanding and nothing else. Depending on the
narrow port keeps every test double honest about what is actually used.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from loka.bounded_contexts.ai.domain.services.ai_provider import (
    IntentResult,
    LlmResponse,
    Prompt,
)

if TYPE_CHECKING:
    from loka.bounded_contexts.property.application.use_cases.create_property_from_conversation import (  # noqa: E501
        ConversationListingResult,
        ConversationMediaResult,
    )


class IntentModel(Protocol):
    """Intent understanding, and its own identity for cost attribution."""

    @property
    def model(self) -> str: ...

    async def understand_intent(self, prompt: Prompt) -> IntentResult: ...


class ResponseModel(Protocol):
    """Natural-language reply generation, and what the call cost.

    Deliberately a separate port from :class:`IntentModel`: writing the reply and
    reading the message are different jobs, and a deployment may want to use a
    different model — or none at all — for one of them. The pipeline falls back
    to fixed templates when no ``ResponseModel`` is configured.

    It returns ``LlmResponse`` rather than a bare string so the reply's tokens
    reach the ledger: a reply written by a model is a real cost, and a budget
    that only counted intent calls would under-report every conversation.
    """

    @property
    def model(self) -> str: ...

    async def generate_response(self, prompt: Prompt) -> LlmResponse: ...


class PropertyListing(Protocol):
    """Turns a confirmed listing conversation into a real property draft.

    Kept as a narrow port so the analysis pipeline depends on the *act* of
    registering a listing, not on the Property context's use cases. The AI
    context already reads the property domain's value objects; this is the one
    write it triggers, and it stays behind a boundary a test can stub.
    """

    async def register_from_conversation(
        self,
        *,
        user_id: uuid.UUID | None,
        facts: Mapping[str, Any],
        existing_property_id: uuid.UUID | None = None,
        now: datetime,
    ) -> ConversationListingResult | None: ...

    async def attach_media_from_conversation(
        self,
        *,
        property_id: uuid.UUID,
        object_key: str,
        kind: str,
        now: datetime,
    ) -> ConversationMediaResult | None:
        """Attach one stored object to a draft listing.

        ``kind`` is ``PHOTO`` or ``DOCUMENT`` as a plain string: the AI context
        reads the property domain's value objects but should not have to import
        its media enum to hand over a key.

        ``None`` means there was nothing to attach it to. A refusal by the
        listing itself — already attached, past the photo limit — raises, so
        the caller can tell "no draft yet" from "the draft said no".
        """
        ...


class TenantDemands(Protocol):
    """Records what a tenant is looking for, so Kora can come back to him.

    Narrow like the other write ports: the pipeline depends on the *act* of
    recording a demand, not on the catalogue's use cases. Kora answers no
    search in the thread, so without this the acknowledgement it sends would
    promise a follow-up to a request nothing kept.
    """

    async def record(
        self, *, tenant_id: uuid.UUID, query: ConversationSearchQuery, now: datetime
    ) -> bool: ...


class AvailabilityRequests(Protocol):
    """Puts the question to the landlord of a listing a tenant picked.

    The pipeline knows which listing the tenant chose and nothing about who
    owns it. Asking its landlord is the catalogue's business, and delivering
    the message is the outbound layer's: this port only starts it, and returns
    whether a question is now outstanding.
    """

    async def request(
        self, *, property_id: uuid.UUID, tenant_id: uuid.UUID | None, now: datetime
    ) -> bool: ...


class UnlockRequestOutcome(StrEnum):
    """What asking for the 1 000 FCFA produced, in the terms the reply needs."""

    REQUESTED = "REQUESTED"
    ALREADY_UNLOCKED = "ALREADY_UNLOCKED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    FAILED = "FAILED"


class UnlockPayments(Protocol):
    """Starts the withdrawal for one unlock: this tenant, this listing.

    The pipeline knows the tenant agreed and which number to charge; whether
    the landlord's confirmation still holds, and what the provider does, is
    Payment's business. Settlement is never reported here — only the
    provider's callback releases the contact.
    """

    async def request(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        payer_phone: str,
        now: datetime,
    ) -> UnlockRequestOutcome: ...


@dataclass(frozen=True, slots=True)
class UnlockedContact:
    """What the 1 000 FCFA buys, once it is settled: a number and a place.

    The landlord's number, read aloud on the phone; a label for the lodging as
    the tenant already saw it; and the point when the listing has one. All three
    come from other contexts, so the pipeline holds them as plain values and
    never learns a landlord profile id or a listing row.
    """

    landlord_phone: str
    lodging: str
    point: dict[str, float] | None = None


class UnlockedContactDetails(Protocol):
    """Resolves the contact a settled unlock releases.

    ``None`` means the pieces could not all be found — a landlord with no
    account or no thread, a listing that has vanished. A paid unlock whose
    contact cannot be assembled is a real failure and must be surfaced, never
    answered with a partial number.
    """

    async def for_listing(
        self, *, property_id: uuid.UUID, landlord_id: uuid.UUID, language: str
    ) -> UnlockedContact | None: ...


class AvailabilityAnswers(Protocol):
    """Carries a landlord's "le logement est encore disponible ?" answer home.

    Narrow on purpose, like :class:`PropertyListing`: the pipeline depends on
    the *act* of recording an answer, not on the catalogue's use cases. The
    landlord's reply names no listing — it arrives in a WhatsApp thread — so
    the adapter is the thing that knows which question he was asked.

    ``None`` means he was not asked anything. That is a real case: a landlord
    may volunteer "c'est déjà loué" out of the blue, and guessing which listing
    he meant would take a lodging out of search on a coincidence.
    """

    async def record_answer(
        self, *, landlord_id: uuid.UUID, answer: str, now: datetime
    ) -> AvailabilityAnswerResult | None: ...


@dataclass(frozen=True, slots=True)
class AvailabilityAnswerResult:
    """What the answer did, in the terms a reply needs.

    No listing, no landlord details: enough to tell the landlord his answer
    landed and to say how many lodgings remain.
    """

    confirms_availability: bool
    lodgings_left: int
    delisted: bool


@dataclass(frozen=True, slots=True)
class ConversationSearchQuery:
    """One tenant search, stated in the facts the conversation accepted.

    ``budget_xaf`` is the rent the tenant *asked for* — the centre of the band
    the adapter matches around — not a hard ceiling. Deliberately not the
    Property context's search criteria: the pipeline knows a city, a budget and
    a type, and a widening of the query language (a sort, a cursor, a date
    filter) must not silently change what a conversation can ask for. The
    adapter translates.
    """

    city: str
    neighbourhoods: tuple[str, ...] = ()
    property_types: tuple[str, ...] = ()
    budget_xaf: int | None = None
    min_bedrooms: int | None = None
    # The WhatsApp location he shared from where he wants to live, or the
    # centre of the district he typed when ``near_exact`` is false.
    near: tuple[float, float] | None = None
    near_exact: bool = True


@dataclass(frozen=True, slots=True)
class ConversationPropertyHit:
    """The slice of a listing a WhatsApp reply is allowed to show.

    Enough to name the property and its monthly cost; never the landlord's
    identity, never an internal status — the reply is sent to a stranger who
    has not been shown anything yet.
    """

    property_id: str
    property_type: str
    city: str
    neighbourhood: str | None
    price_xaf: int
    bedrooms: int | None = None
    surface_m2: int | None = None


class PropertySearchGateway(Protocol):
    """The catalogue read the search flow triggers.

    The single read this pipeline performs. Returning typed hits rather than
    the projection's dictionaries keeps the reply formatting independent of how
    the read model is shaped, and keeps the port stubbable in a test.
    """

    async def search_for_conversation(
        self, query: ConversationSearchQuery, *, limit: int = 5
    ) -> list[ConversationPropertyHit]: ...


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    """One analysed message, recorded whatever the outcome."""

    session_id: uuid.UUID
    message_id: uuid.UUID | None
    direction: str
    intent: str
    confidence: float
    used_llm: bool
    latency_ms: int
    occurred_at: datetime
    turn_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class TurnSummary:
    """The only history a prompt is given.

    Deliberately no message text: ``conversation_turns`` stores no copy, so the
    prompt context can never leak more than what the platform already keeps
    about the conversation's shape.
    """

    direction: str
    intent: str
    confidence: float
    used_llm: bool


@dataclass(frozen=True, slots=True)
class Exchange:
    """One line of the thread as the user saw it: who spoke, and what was said.

    The labels in ``TurnSummary`` say *that* a turn happened; this says *what*
    was said. Without it "le premier là", "comme je t'ai dit" or "rappelle-moi
    mon budget" point at nothing, and the assistant answers like a form.
    """

    speaker: str  # "user" or "kora"
    text: str


class ConversationExchanges(Protocol):
    async def recent(
        self, session_id: uuid.UUID, *, excluding: uuid.UUID | None = None, limit: int = 6
    ) -> list[Exchange]:
        """Oldest-first, without ``excluding`` — the message being answered."""


class ConversationTurnRepository(Protocol):
    async def add(self, turn: ConversationTurn) -> uuid.UUID: ...

    async def recent(self, session_id: uuid.UUID, *, limit: int = 5) -> list[TurnSummary]:
        """Oldest-first summaries, for prompt context."""


@dataclass(frozen=True, slots=True)
class UsageEntry:
    use_case: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal
    user_id: uuid.UUID | None
    conversation_id: uuid.UUID | None
    correlation_id: str | None
    occurred_at: datetime


class AiUsageLedger(Protocol):
    async def append(self, entry: UsageEntry) -> uuid.UUID: ...

    async def total_cost_usd(
        self, *, on: date, user_id: uuid.UUID | None = None
    ) -> Decimal:
        """Spend for a day, globally or for one user.

        Read before every model call: an unbounded retry loop must not be able
        to spend more than the budget allows.
        """


@dataclass(frozen=True, slots=True)
class ChoiceQuestion:
    """One decision put to a decision model: pick one of these options."""

    instructions: str
    options: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class Decisions:
    """What a decision model answered, and what the call cost."""

    answers: Mapping[str, ChoiceAnswer]
    model: str
    input_tokens: int
    latency_ms: int
    cost_usd: Decimal


class DecisionModel(Protocol):
    """Takes small typed decisions over a state; writes no text.

    A separate port from :class:`IntentModel` because it does a different job:
    it picks among options Kora defines and says how sure it is, in well under
    a second. Raises ``ExternalServiceUnavailable`` when it cannot answer.
    """

    @property
    def model(self) -> str: ...

    async def decide(
        self, *, state: Mapping[str, Any], questions: Mapping[str, ChoiceQuestion]
    ) -> Decisions: ...


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    """A decision model's reading of a turn, next to what the pipeline did.

    ``decisions`` is ``None`` and ``error`` says why when the model failed: a
    pilot that only kept its successes would overstate how dependable it is.
    """

    message_id: uuid.UUID
    session_id: uuid.UUID
    flow_before: str
    step_before: str
    pending_question: str | None
    decisions: Decisions | None
    error: str | None
    pipeline_intent: str
    pipeline_accepted: bool
    pipeline_used_llm: bool
    pipeline_next_step: str | None
    occurred_at: datetime


class DecisionShadowLog(Protocol):
    async def record(self, entry: ShadowDecision) -> None: ...


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str | None = None
    spent_usd: Decimal = Decimal("0")
    budget_usd: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Daily caps, evaluated before spending anything."""

    daily_usd: Decimal
    per_user_daily_usd: Decimal

    def evaluate(
        self, *, global_spend: Decimal, user_spend: Decimal, has_user: bool
    ) -> BudgetDecision:
        if global_spend >= self.daily_usd:
            return BudgetDecision(
                allowed=False,
                reason="daily_budget_exhausted",
                spent_usd=global_spend,
                budget_usd=self.daily_usd,
            )
        if has_user and user_spend >= self.per_user_daily_usd:
            return BudgetDecision(
                allowed=False,
                reason="per_user_budget_exhausted",
                spent_usd=user_spend,
                budget_usd=self.per_user_daily_usd,
            )
        return BudgetDecision(allowed=True, spent_usd=global_spend, budget_usd=self.daily_usd)


__all__ = [
    "AiUsageLedger",
    "BudgetDecision",
    "BudgetPolicy",
    "ConversationPropertyHit",
    "ConversationSearchQuery",
    "ConversationTurn",
    "ConversationTurnRepository",
    "IntentModel",
    "IntentResult",
    "Prompt",
    "PropertyListing",
    "PropertySearchGateway",
    "ResponseModel",
    "TurnSummary",
    "UsageEntry",
]
