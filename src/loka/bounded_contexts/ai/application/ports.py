"""Ports of the AI context.

``IntentModel`` is intentionally narrower than
:class:`~loka.bounded_contexts.ai.domain.services.ai_provider.AIProvider`: the
analysis pipeline needs intent understanding and nothing else. Depending on the
narrow port keeps every test double honest about what is actually used.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from loka.bounded_contexts.ai.domain.services.ai_provider import IntentResult, Prompt


class IntentModel(Protocol):
    """Intent understanding, and its own identity for cost attribution."""

    @property
    def model(self) -> str: ...

    async def understand_intent(self, prompt: Prompt) -> IntentResult: ...


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
    "ConversationTurn",
    "ConversationTurnRepository",
    "IntentModel",
    "IntentResult",
    "Prompt",
    "TurnSummary",
    "UsageEntry",
]
