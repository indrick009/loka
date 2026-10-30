"""AI usage ledger.

Every model call is attributed to a user, a conversation and a use case so
cost can be explained rather than guessed.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class AiUsageLedgerRow(Base):
    __tablename__ = "ai_usage_ledger"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    use_case: Mapped[str] = mapped_column(String(48), nullable=False)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )
    was_cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_ai_usage_user_day", "user_id", "occurred_on"),
        Index("ix_ai_usage_use_case_day", "use_case", "occurred_on"),
        Index("ix_ai_usage_model_day", "model", "occurred_on"),
    )


class DecisionShadowRow(Base):
    """A decision model's reading of one turn, next to what the pipeline did."""

    __tablename__ = "decision_shadows"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    flow_before: Mapped[str] = mapped_column(String(32), nullable=False)
    step_before: Mapped[str] = mapped_column(String(48), nullable=False)
    pending_question: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    answers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 8), nullable=True)
    error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pipeline_intent: Mapped[str] = mapped_column(String(48), nullable=False)
    pipeline_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pipeline_used_llm: Mapped[bool] = mapped_column(Boolean, nullable=False)
    pipeline_next_step: Mapped[str | None] = mapped_column(String(48), nullable=True)

    __table_args__ = (Index("ix_decision_shadows_occurred_at", "occurred_at"),)