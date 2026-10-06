"""Fraud and trust ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class RiskProfileRow(Base):
    __tablename__ = "risk_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    subject: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    risk_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    risk_band: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")
    recommended_action: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    signals: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    under_manual_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    restricted_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_evaluated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("uq_risk_profile_subject", "subject", "subject_id", unique=True),
        Index("ix_risk_profile_band", "risk_band", "risk_score"),
        Index("ix_risk_profile_review_queue", "under_manual_review", "last_evaluated_at"),
        Index("ix_risk_profile_restricted", "restricted_until"),
    )


class ReportRow(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reporter_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    evidence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_reports_open_queue", "status", "created_at"),
        Index("ix_reports_target", "target_type", "target_id", "status"),
        Index("ix_reports_reporter", "reporter_id", "created_at"),
    )


class TrustProfileRow(Base):
    """Composite trust score. Deliberately not an average of reviews."""

    __tablename__ = "trust_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    landlord_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    band: Mapped[str] = mapped_column(String(16), nullable=False, default="NEW")
    identity_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    account_age_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_rentals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cancelled_applications: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    availability_accuracy: Mapped[float] = mapped_column(nullable=False, default=1.0)
    price_accuracy: Mapped[float] = mapped_column(nullable=False, default=1.0)
    condition_compliance: Mapped[float] = mapped_column(nullable=False, default=1.0)
    report_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    components: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_trust_score", "score"),)


class FraudSignalRow(Base):
    __tablename__ = "fraud_signals"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    subject: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(48), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="rules")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_fraud_signal_active", "subject", "subject_id", "active", "reason"),
        Index("ix_fraud_signal_detected", "detected_at"),
        UniqueConstraint(
            "profile_id", "reason", "detected_at", name="uq_fraud_signal_profile_reason_time"
        ),
    )


class MediaHashRow(Base):
    """Perceptual hashes powering duplicate-photo detection."""

    __tablename__ = "media_hashes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    media_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    landlord_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    perceptual_hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    hamming_band: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_media_hash_lookup", "perceptual_hash", "landlord_id"),)