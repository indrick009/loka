"""Payment context ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class ServiceFeePaymentRow(Base):
    __tablename__ = "service_fee_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    landlord_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    application_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    amount_xaf: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="XAF")
    purpose: Mapped[str] = mapped_column(String(24), nullable=False)
    method: Mapped[str] = mapped_column(String(24), nullable=False, default="MOBILE_MONEY")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="CREATED")
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_reference: Mapped[str | None] = mapped_column(String(180), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # A provider reference must never settle two different payments.
        Index(
            "uq_payment_provider_reference",
            "provider",
            "provider_reference",
            unique=True,
            postgresql_where="provider_reference IS NOT NULL",
        ),
        # One live payment per application: retries reuse the row.
        Index(
            "uq_payment_open_application",
            "application_id",
            unique=True,
            postgresql_where="status IN ('CREATED', 'INITIATED', 'PENDING_CONFIRMATION')",
        ),
        Index("ix_payment_tenant_time", "tenant_id", "created_at"),
        Index("ix_payment_status_time", "status", "created_at"),
    )


class ServiceAccessGrantRow(Base):
    __tablename__ = "service_access_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    landlord_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    application_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    reveal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revocation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("uq_grant_payment", "payment_id", unique=True),
        Index(
            "uq_grant_active_application",
            "application_id",
            unique=True,
            postgresql_where="status = 'ACTIVE'",
        ),
    )


class PaymentCallbackRow(Base):
    """Raw provider callbacks, kept for reconciliation and disputes."""

    __tablename__ = "payment_callbacks"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(180), nullable=False)
    payload_signature: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    accepted: Mapped[bool] = mapped_column(nullable=False, default=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_event_id", name="uq_payment_callback_provider_event"
        ),
        Index("ix_payment_callback_received", "received_at"),
    )