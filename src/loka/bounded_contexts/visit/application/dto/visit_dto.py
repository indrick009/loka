"""Visit DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from loka.bounded_contexts.visit.domain.entities.visit import Visit


@dataclass(frozen=True, slots=True)
class VisitView:
    visit_id: str
    property_id: str
    landlord_id: str
    tenant_id: str
    status: str
    preferred_date: date | None
    scheduled_for: datetime | None
    message: str | None
    cancellation_reason: str | None

    @classmethod
    def from_aggregate(cls, visit: Visit) -> VisitView:
        return cls(
            visit_id=str(visit.visit_id),
            property_id=str(visit.property_id),
            landlord_id=str(visit.landlord_id),
            tenant_id=str(visit.tenant_id),
            status=visit.status.value,
            preferred_date=visit.preferred_date,
            scheduled_for=visit.scheduled_for,
            message=visit.message,
            cancellation_reason=visit.cancellation_reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "visit_id": self.visit_id,
            "property_id": self.property_id,
            "landlord_id": self.landlord_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "preferred_date": self.preferred_date.isoformat() if self.preferred_date else None,
            "scheduled_for": self.scheduled_for.isoformat() if self.scheduled_for else None,
            "message": self.message,
            "cancellation_reason": self.cancellation_reason,
        }