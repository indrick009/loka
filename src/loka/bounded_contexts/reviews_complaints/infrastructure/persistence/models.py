"""Reviews & Complaints ORM models.

Three tables, one per aggregate. Nothing here references another context's
tables: the provider, the offer and the introduction are carried as a kind plus
an opaque id, because the same complaint shape has to serve a landlord, a
restaurateur, a finder and a dating profile, and a foreign key could only point
at one of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class ProviderReviewRow(Base):
    """One avis. No text column, by decision: §5 leaves text to complaints."""

    __tablename__ = "provider_reviews"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    offer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    introduction_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    author_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One introduction, one mark. The database enforces it as well as the
        # use case because two concurrent submissions would both pass the
        # existence check and both insert, and the ranking would then carry one
        # person's opinion twice.
        UniqueConstraint(
            "domain", "introduction_id", name="uq_provider_reviews_domain_introduction"
        ),
        # The ranking's grouped read: average and count per provider.
        Index("ix_provider_reviews_provider", "provider_id"),
        Index("ix_provider_reviews_offer", "offer_id"),
    )


class ProviderComplaintRow(Base):
    """One plainte, with the account a human reads before deciding."""

    __tablename__ = "provider_complaints"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    offer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    introduction_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    complainant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    account: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One introduction, one complaint — the rule that keeps the hide
        # threshold meaningful. Without it a single furious tenant files three
        # complaints and an honest landlord disappears from search.
        UniqueConstraint(
            "domain", "introduction_id", name="uq_provider_complaints_domain_introduction"
        ),
        # Counting what a provider is carrying, by status.
        Index("ix_provider_complaints_provider_status", "provider_id", "status"),
        # The queue, oldest first, optionally filtered by status.
        Index("ix_provider_complaints_queue", "status", "filed_at"),
    )


class ProviderStandingRow(Base):
    """Whether Kora still puts this provider forward, and who decided that.

    One row per provider, created the first time someone complains. Providers
    with no row are visible, which is the common case and must not cost a write
    on every search.
    """

    __tablename__ = "provider_standings"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="VISIBLE")
    pending_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upheld_complaints: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # Exactly one standing per provider: two rows would let a search read
        # the visible one while the team had hidden the other.
        UniqueConstraint(
            "provider_kind", "provider_id", name="uq_provider_standings_kind_provider"
        ),
        Index("ix_provider_standings_status", "status"),
    )
