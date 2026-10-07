"""Landlord verification ORM models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class LandlordProfileRow(Base):
    __tablename__ = "landlord_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, unique=True
    )
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    verification_status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trust_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_rentals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_property_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_landlord_status", "verification_status", "created_at"),)


class VerificationRequestRow(Base):
    __tablename__ = "verification_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    landlord_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING")
    review_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="AUTOMATIC")
    rejection_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    risk_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    selfie_object_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    selfie_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_verification_queue", "status", "submitted_at"),
        Index("ix_verification_landlord_status", "landlord_id", "status"),
    )


class VerificationDocumentRow(Base):
    __tablename__ = "verification_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ocr_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("ix_verification_doc_request", "request_id", "kind"),)