"""Transactional outbox.

Domain events are written in the same transaction as the aggregate. A
separate dispatcher then forwards them to the broker. This removes the
dual-write problem: either both the state change and the event land, or
neither does.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, String, Text, delete, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.application.context import current_context
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.events import DomainEvent, IntegrationEvent
from loka.shared.infrastructure.db.base import Base

_TOPIC_BY_EVENT = {
    "UserPhoneVerified": "identity.events",
    "LandlordVerified": "landlord.events",
    "PropertyPublished": "property.events",
    "PropertyRented": "property.events",
    "PropertyMarkedUnavailable": "property.events",
    "RentalApplicationCreated": "rental.events",
    "VisitCompleted": "visit.events",
    "PaymentSucceeded": "payments.events",
    "ContactInformationUnlocked": "payment.events",
    "FraudRiskScoreUpdated": "fraud.events",
    "FraudRiskDetected": "fraud.events",
    "FraudRiskCleared": "fraud.events",
    "FraudRestrictionLifted": "fraud.events",
    "ReportInvestigating": "fraud.events",
    "ReportDismissed": "fraud.events",
    "ReportActioned": "fraud.events",
    "TenantFeedbackSubmitted": "trust.events",
    "WhatsAppMessageIngested": "whatsapp.events",
    "ConversationAnalysisRequested": "ai.requests",
    "AiUsageRecorded": "analytics.events",
}


def topic_for(event_type: str) -> str:
    return _TOPIC_BY_EVENT.get(event_type, "domain.events")


class OutboxRecord(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False)
    topic: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: ensure_utc(datetime.now())
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_outbox_unpublished", "occurred_at", postgresql_where=published_at.is_(None)),
    )


class OutboxStore:
    """Append-only writer plus a batched claim for the dispatcher."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, events: list[DomainEvent], *, correlation_id: str) -> None:
        context = current_context()
        for event in events:
            self._session.add(
                OutboxRecord(
                    aggregate_type=event.aggregate_type,
                    aggregate_id=event.aggregate_id,
                    aggregate_version=event.aggregate_version,
                    event_type=event.event_type,
                    topic=topic_for(event.event_type),
                    correlation_id=correlation_id,
                    causation_id=context.get("causation_id"),
                    payload=event.to_payload(),
                    occurred_at=event.occurred_at,
                    attempts=0,
                )
            )

    async def claim_batch(self, limit: int = 200) -> list[OutboxRecord]:
        """Single-dispatcher-consumes is fine; SKIP LOCKED allows more."""
        result = await self._session.execute(
            select(OutboxRecord)
            .where(OutboxRecord.published_at.is_(None))
            .order_by(OutboxRecord.occurred_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(result.scalars())

    async def mark_published(self, record: OutboxRecord) -> None:
        record.published_at = ensure_utc(datetime.now())
        record.attempts += 1
        record.last_error = None

    async def mark_failed(self, record: OutboxRecord, error: str) -> None:
        record.attempts += 1
        record.last_error = error[:2000]

    async def purge_published(self, older_than_days: int = 7) -> int:
        cutoff = ensure_utc(datetime.now()).timestamp() - older_than_days * 86_400
        result = await self._session.execute(
            delete(OutboxRecord).where(
                OutboxRecord.published_at.isnot(None),
                OutboxRecord.published_at < _from_timestamp(cutoff),
            )
        )
        return _rowcount(result)


def _rowcount(result: Any) -> int:
    """DML row count; the ORM result object does not expose it directly."""
    context = getattr(result, "context", None)
    rowcount = getattr(context, "rowcount", None)
    return int(rowcount) if isinstance(rowcount, int) and rowcount > 0 else 0


def _from_timestamp(ts: float) -> datetime:
    from datetime import UTC
    from datetime import datetime as dt

    return dt.fromtimestamp(ts, tz=UTC)


def to_integration_event(record: OutboxRecord) -> IntegrationEvent:
    return IntegrationEvent(
        message_id=record.id,
        event_type=record.event_type,
        occurred_at=record.occurred_at,
        correlation_id=record.correlation_id,
        causation_id=record.causation_id,
        payload=record.payload,
    )