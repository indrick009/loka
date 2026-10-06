"""AI read-model persistence.

The ledger is append-only and queried by (day, user): the budget guard reads
spend *before* spending, so the number it reads has to come from the database
rather than from a counter that a crash could lose. ``SUM`` over an indexed
``(user_id, occurred_on)`` pair is cheap and, unlike a Prometheus counter,
survives a restart.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.ai.application.ports import (
    ConversationTurn,
    TurnSummary,
    UsageEntry,
)
from loka.bounded_contexts.ai.infrastructure.persistence.models import AiUsageLedgerRow
from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationTurnRow,
)
from loka.shared.domain.clock import ensure_utc


class SqlAlchemyConversationTurnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, turn: ConversationTurn) -> uuid.UUID:
        row = ConversationTurnRow(
            id=turn.turn_id or uuid.uuid4(),
            session_id=turn.session_id,
            direction=turn.direction,
            message_id=turn.message_id,
            intent=turn.intent,
            confidence=turn.confidence,
            used_llm=turn.used_llm,
            latency_ms=turn.latency_ms,
            created_at=ensure_utc(turn.occurred_at),
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def recent(self, session_id: uuid.UUID, *, limit: int = 5) -> list[TurnSummary]:
        # Newest first, then reversed: the prompt wants the last turns in the
        # order they happened.
        result = await self._session.execute(
            select(
                ConversationTurnRow.direction,
                ConversationTurnRow.intent,
                ConversationTurnRow.confidence,
                ConversationTurnRow.used_llm,
            )
            .where(ConversationTurnRow.session_id == session_id)
            .order_by(ConversationTurnRow.created_at.desc(), ConversationTurnRow.id.desc())
            .limit(limit)
        )
        return [
            TurnSummary(
                direction=row[0],
                intent=row[1] or "UNKNOWN",
                confidence=float(row[2] or 0.0),
                used_llm=bool(row[3]),
            )
            for row in result.all()
        ][::-1]


class SqlAlchemyAiUsageLedger:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, entry: UsageEntry) -> uuid.UUID:
        occurred_at = ensure_utc(entry.occurred_at)
        row = AiUsageLedgerRow(
            id=uuid.uuid4(),
            occurred_at=occurred_at,
            # Denormalised on purpose: every budget query filters on the day,
            # and `ensure_utc` has already fixed the offset, so this stays the
            # day the platform charged for, not the client's local midnight.
            occurred_on=occurred_at.date(),
            user_id=entry.user_id,
            conversation_id=entry.conversation_id,
            use_case=entry.use_case,
            provider=entry.provider,
            model=entry.model,
            input_tokens=entry.input_tokens,
            output_tokens=entry.output_tokens,
            latency_ms=entry.latency_ms,
            cost_usd=entry.cost_usd,
            was_cached=False,
            correlation_id=entry.correlation_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def total_cost_usd(
        self, *, on: date, user_id: uuid.UUID | None = None
    ) -> Decimal:
        statement = select(func.coalesce(func.sum(AiUsageLedgerRow.cost_usd), 0)).where(
            AiUsageLedgerRow.occurred_on == on
        )
        if user_id is not None:
            statement = statement.where(AiUsageLedgerRow.user_id == user_id)
        total = (await self._session.execute(statement)).scalar_one()
        return Decimal(str(total or 0))
