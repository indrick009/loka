"""Immutable audit log.

Append-only: no update path, no delete path. Every security-relevant or
money-relevant action lands here with enough context to reconstruct it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

RETENTION_DAYS = 365 * 3


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(48), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_audit_subject", "subject_type", "subject_id", "occurred_at"),
        Index("ix_audit_action_time", "action", "occurred_at"),
    )


AUDITED_ACTIONS = frozenset(
    {
        "landlord_verified",
        "landlord_verification_rejected",
        "landlord_verification_suspended",
        "property_created",
        "property_updated",
        "property_published",
        "property_unpublished",
        "property_marked_rented",
        "property_suspended",
        "payment_initiated",
        "payment_completed",
        "payment_refunded",
        "phone_revealed",
        "fraud_detected",
        "fraud_risk_updated",
        "fraud_restriction_lifted",
        "account_suspended",
        "account_reinstated",
        "report_created",
        "report_actioned",
        "report_dismissed",
    }
)


def is_audited(action: str) -> bool:
    return action in AUDITED_ACTIONS