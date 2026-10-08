"""Visit aggregate.

A visit is the physical checkpoint of the trust loop. It is kept separate from
the rental application because a tenant may visit several properties before
applying, and the same listing may be visited by many tenants.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition


class VisitStatus(StrEnum):
    REQUESTED = "REQUESTED"
    SCHEDULED = "SCHEDULED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class Visit(AggregateRoot):
    __slots__ = (
        "cancellation_reason",
        "cancelled_at",
        "completed_at",
        "created_at",
        "landlord_id",
        "message",
        "preferred_date",
        "property_id",
        "scheduled_for",
        "status",
        "tenant_id",
        "updated_at",
        "visit_id",
    )

    def __init__(
        self,
        *,
        visit_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        tenant_id: uuid.UUID,
        preferred_date: date | None,
        message: str | None,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="Visit")
        self._assign_id(visit_id)
        self.visit_id = visit_id
        self.property_id = property_id
        self.landlord_id = landlord_id
        self.tenant_id = tenant_id
        self.status = VisitStatus.REQUESTED
        self.preferred_date = preferred_date
        self.message = (message or "").strip() or None
        self.scheduled_for: datetime | None = None
        self.completed_at: datetime | None = None
        self.cancelled_at: datetime | None = None
        self.cancellation_reason: str | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self.record(
            "VisitRequested",
            occurred_at=now,
            payload={
                "visit_id": str(self.visit_id),
                "property_id": str(self.property_id),
                "tenant_id": str(self.tenant_id),
            },
        )

    @property
    def is_open(self) -> bool:
        return self.status in (VisitStatus.REQUESTED, VisitStatus.SCHEDULED)

    def schedule(self, *, when: datetime, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if not self.is_open:
            raise InvalidStateTransition(
                f"cannot schedule a {self.status.value} visit",
                context={"visit_id": str(self.visit_id)},
            )
        moment = ensure_utc(when)
        if moment <= ensure_utc(now):
            self._reject("a visit must be scheduled in the future", when=moment.isoformat())
        is_reschedule = self.status is VisitStatus.SCHEDULED
        self.scheduled_for = moment
        self.status = VisitStatus.SCHEDULED
        self._touch(now)
        self.record(
            "VisitRescheduled" if is_reschedule else "VisitScheduled",
            occurred_at=now,
            actor_id=actor_id,
            payload={
                "visit_id": str(self.visit_id),
                "property_id": str(self.property_id),
                "scheduled_for": moment.isoformat(),
            },
        )

    def cancel(
        self,
        *,
        reason: str | None = None,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        if not self.is_open:
            raise InvalidStateTransition(
                f"cannot cancel a {self.status.value} visit",
                context={"visit_id": str(self.visit_id)},
            )
        self.status = VisitStatus.CANCELLED
        self.cancellation_reason = (reason or "").strip() or None
        self.cancelled_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "VisitCancelled",
            occurred_at=now,
            actor_id=actor_id,
            payload={"visit_id": str(self.visit_id), "reason": self.cancellation_reason},
        )

    def complete(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is not VisitStatus.SCHEDULED:
            raise InvalidStateTransition(
                "only a scheduled visit can be completed",
                context={"visit_id": str(self.visit_id), "status": self.status.value},
            )
        self.status = VisitStatus.COMPLETED
        self.completed_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "VisitCompleted",
            occurred_at=now,
            actor_id=actor_id,
            payload={
                "visit_id": str(self.visit_id),
                "property_id": str(self.property_id),
                "tenant_id": str(self.tenant_id),
            },
        )

    def mark_no_show(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is not VisitStatus.SCHEDULED:
            raise InvalidStateTransition(
                "only a scheduled visit can be marked as a no-show",
                context={"visit_id": str(self.visit_id), "status": self.status.value},
            )
        self.status = VisitStatus.NO_SHOW
        self._touch(now)
        self.record(
            "VisitNoShow",
            occurred_at=now,
            actor_id=actor_id,
            payload={"visit_id": str(self.visit_id), "tenant_id": str(self.tenant_id)},
        )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()