"""Property context ORM models.

Indexes target the two hot read paths: public search filters and landlord
dashboard listings. Money is stored as bigint XAF, never as float.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class PropertyRow(Base):
    __tablename__ = "properties"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    landlord_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    property_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    standing: Mapped[str | None] = mapped_column(String(16), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    neighbourhood: Mapped[str | None] = mapped_column(String(96), nullable=True)
    address_hint: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)

    rent_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    charges_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    public_price_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    charging_policy: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN")
    deposit_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="XAF")

    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    surface_m2: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)

    amenities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    landlord_rules: Mapped[str | None] = mapped_column(Text, nullable=True)

    data_quality_issues: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    completeness_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_availability_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    search_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Public search: status + city + price, always ordered by recency.
        Index("ix_properties_public_search", "status", "city", "public_price_xaf", "created_at"),
        Index("ix_properties_type_status", "property_type", "status"),
        Index("ix_properties_landlord_status", "landlord_id", "status", "created_at"),
        Index(
            "ix_properties_available_from",
            "available_from",
            postgresql_where=status.in_(["AVAILABLE"]),
        ),
        Index(
            "ix_properties_unpublished",
            "created_at",
            postgresql_where=status.in_(["DRAFT"]),
        ),
        Index(
            "ix_properties_stale_availability",
            "last_availability_confirmed_at",
            postgresql_where=status.in_(["AVAILABLE"]),
        ),
        Index("ix_properties_search_document", "search_document"),
    )


class PropertySearchDocumentRow(Base):
    """Read model behind public search.

    Denormalised on purpose: a search page must never join the write model.
    Rows are written from ``property.events`` by the search projection consumer
    and carry the write-model ``source_version`` so a redelivered or reordered
    event cannot overwrite a fresher document.
    """

    __tablename__ = "property_search_documents"

    property_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    landlord_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    property_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    neighbourhood: Mapped[str | None] = mapped_column(String(96), nullable=True)

    public_price_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rent_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    charges_xaf: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    charging_policy: Mapped[str] = mapped_column(String(16), nullable=False)

    bedrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bathrooms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    surface_m2: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minimum_duration_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_from: Mapped[date | None] = mapped_column(Date, nullable=True)

    amenities: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    media_object_keys: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    projected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Keyset pagination: one index per supported sort, status first so the
        # AVAILABLE filter is always part of the scan.
        Index("ix_search_documents_recent", "status", "source_created_at", "property_id"),
        Index("ix_search_documents_price", "status", "public_price_xaf", "property_id"),
        Index("ix_search_documents_city_price", "status", "city", "public_price_xaf"),
        Index("ix_search_documents_type", "status", "property_type"),
        Index("ix_search_documents_verified", "status", "is_verified"),
    )


class PropertyMediaRow(Base):
    __tablename__ = "property_media"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PENDING_UPLOAD")
    object_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantine_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_property_media_order", "property_id", "position"),
        Index("ix_property_media_duplicate", "perceptual_hash", "property_id"),
    )


class AvailabilityCheckRow(Base):
    __tablename__ = "property_availability_checks"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    landlord_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answer: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_ask_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_availability_due", "next_ask_at", "answered_at"),)


class PropertyDataQualityIssueRow(Base):
    __tablename__ = "property_data_quality_issues"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    issue: Mapped[str] = mapped_column(String(48), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="MEDIUM")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    landlord_answer: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_quality_open_issues",
            "property_id",
            "issue",
            postgresql_where=resolved_at.is_(None),
        ),
    )