"""The record of a turn and of what its model calls cost."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from loka.bounded_contexts.ai.domain.ai_provider import LlmUsage
from loka.bounded_contexts.conversation.application.ports import (
    AiUsageLedger,
    BudgetDecision,
    BudgetPolicy,
    ConversationTurn,
    ConversationTurnRepository,
    UsageEntry,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    USE_CASE,
    TurnCost,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.shared.infrastructure.metrics import (
    llm_calls_total,
    llm_cost_usd_total,
    llm_latency_seconds,
    llm_tokens_total,
)


@dataclass(frozen=True, slots=True)
class CallUsage:
    """The sum of a turn's model calls: the ledger records one entry per turn."""

    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: Decimal

    @classmethod
    def of(cls, call: LlmUsage | None, reply: LlmUsage | None, *, model: str) -> CallUsage:
        """Both calls are one turn, so reply traffic cannot spend past a budget
        that only counted intent calls."""
        parts = [usage for usage in (call, reply) if usage is not None]
        return cls(
            model=call.model if call is not None else model,
            input_tokens=sum(usage.input_tokens for usage in parts),
            output_tokens=sum(usage.output_tokens for usage in parts),
            latency_ms=sum(usage.latency_ms for usage in parts),
            cost_usd=sum(
                (Decimal(str(usage.estimated_cost_usd)) for usage in parts), Decimal("0")
            ),
        )

    def observe(self) -> None:
        llm_calls_total.labels(use_case=USE_CASE, model=self.model, outcome="ok").inc()
        llm_tokens_total.labels(model=self.model, direction="input").inc(self.input_tokens)
        llm_tokens_total.labels(model=self.model, direction="output").inc(self.output_tokens)
        llm_latency_seconds.labels(use_case=USE_CASE, model=self.model).observe(
            self.latency_ms / 1000
        )
        if self.cost_usd > 0:
            llm_cost_usd_total.labels(use_case=USE_CASE).inc(float(self.cost_usd))


class TurnLedger:
    def __init__(
        self,
        turns: ConversationTurnRepository,
        usage: AiUsageLedger,
        *,
        provider: str,
        budget: BudgetPolicy | None = None,
    ) -> None:
        self._turns = turns
        self._usage = usage
        self._provider = provider
        self._budget = budget

    async def record(
        self,
        session: ConversationSession,
        *,
        intent: str,
        cost: TurnCost,
        call: CallUsage | None = None,
    ) -> None:
        """The turn always; its usage only when a model was paid for it."""
        await self._turns.add(
            ConversationTurn(
                session_id=session.id,
                message_id=cost.message_id,
                direction="INBOUND",
                intent=intent,
                confidence=cost.confidence,
                used_llm=cost.used_llm,
                latency_ms=cost.latency_ms,
                occurred_at=cost.now,
            )
        )
        if not cost.used_llm:
            return
        await self._usage.append(
            UsageEntry(
                use_case=USE_CASE,
                provider=self._provider,
                model=call.model if call is not None else "unknown",
                input_tokens=call.input_tokens if call is not None else 0,
                output_tokens=call.output_tokens if call is not None else 0,
                latency_ms=cost.latency_ms,
                cost_usd=cost.cost_usd,
                user_id=session.user_id,
                conversation_id=session.id,
                correlation_id=None,
                occurred_at=cost.now,
            )
        )

    async def budget(self, session: ConversationSession, *, now: datetime) -> BudgetDecision:
        """Today's spend against the global and per-user ceilings."""
        if self._budget is None:
            return BudgetDecision(allowed=True)
        user_id = session.user_id
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
