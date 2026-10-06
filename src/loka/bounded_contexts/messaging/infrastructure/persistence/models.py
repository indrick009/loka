"""Messaging context ORM models.

``external_message_id`` carries the unique constraint that makes WhatsApp
delivery at-least-once safe to process exactly once.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class ConversationSessionRow(Base):
    __tablename__ = "conversation_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    phone_e164: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    flow: Mapped[str] = mapped_column(String(40), nullable=False, default="SUPPORT")
    step: Mapped[str] = mapped_column(String(40), nullable=False, default="START")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    context_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="fr")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_property_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    last_step_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_session_phone_active", "phone_e164", "status", "updated_at"),
        Index("ix_session_flow_step", "flow", "step", "status"),
    )


class InboundMessageRow(Base):
    __tablename__ = "inbound_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    external_message_id: Mapped[str] = mapped_column(String(180), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="baileys")
    conversation_ref: Mapped[str] = mapped_column(String(96), nullable=False)
    sender_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    message_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provider_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        # The idempotency guarantee: one row per WhatsApp message id.
        Index("uq_inbound_external_message", "provider", "external_message_id", unique=True),
        Index("ix_inbound_unprocessed", "received_at", postgresql_where=processed.is_(False)),
        Index("ix_inbound_conversation_time", "conversation_ref", "provider_timestamp"),
    )


class OutboundMessageRow(Base):
    __tablename__ = "outbound_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    conversation_ref: Mapped[str] = mapped_column(String(96), nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    message_kind: Mapped[str] = mapped_column(String(24), nullable=False, default="TEXT")
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="baileys")
    provider_message_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_outbound_pending",
            "queued_at",
            postgresql_where=sql_text("status IN ('QUEUED', 'SENT')"),
        ),
        Index("ix_outbound_status_time", "status", "queued_at"),
    )


class ConversationTurnRow(Base):
    """Append-only transcript used for context and for fraud analysis."""

    __tablename__ = "conversation_turns"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(48), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    used_llm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_turn_session_time", "session_id", "created_at"),)


class ConversationMetricsRow(Base):
    __tablename__ = "conversation_metrics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    flow: Mapped[str] = mapped_column(String(40), nullable=False)
    step: Mapped[str] = mapped_column(String(40), nullable=False)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dropouts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("uq_conversation_metrics_bucket", "occurred_on", "flow", "step", unique=True),
    )