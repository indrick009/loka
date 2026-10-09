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
from typing import TYPE_CHECKING, Any, Protocol

from loka.bounded_contexts.ai.domain.services.ai_provider import (
    IntentResult,
    LlmResponse,
    Prompt,
)

if TYPE_CHECKING:
    from loka.bounded_contexts.property.application.use_cases.create_property_from_conversation import (  # noqa: E501
        ConversationListingResult,
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


@dataclass(frozen=True, slots=True)
class ConversationSearchQuery:
    """One tenant search, stated in the facts the conversation accepted.

    Deliberately not the Property context's search criteria: the pipeline knows
    a city, a budget and a type, and a widening of the query language (a sort,
    a cursor, a date filter) must not silently change what a conversation can
    ask for. The adapter translates.
    """

    city: str
    neighbourhoods: tuple[str, ...] = ()
    property_types: tuple[str, ...] = ()
    max_price_xaf: int | None = None
    min_bedrooms: int | None = None


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
