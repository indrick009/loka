"""Visit <-> row mapper."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.visit.domain.entities.visit import Visit, VisitStatus
from loka.bounded_contexts.visit.infrastructure.persistence.models import VisitRow


def to_row(visit: Visit) -> dict[str, Any]:
    return {
        "id": visit.visit_id,
        "property_id": visit.property_id,
        "landlord_id": visit.landlord_id,
        "tenant_id": visit.tenant_id,
        "status": visit.status.value,
        "preferred_date": visit.preferred_date,
        "scheduled_for": visit.scheduled_for,
        "message": visit.message,
        "cancellation_reason": visit.cancellation_reason,
        "completed_at": visit.completed_at,
        "cancelled_at": visit.cancelled_at,
        "revision": visit.version,
        "created_at": visit.created_at,
        "updated_at": visit.updated_at,
    }


def from_row(row: VisitRow) -> Visit:
    visit = Visit(
        visit_id=row.id,
        property_id=row.property_id,
        landlord_id=row.landlord_id,
        tenant_id=row.tenant_id,
        preferred_date=row.preferred_date,
        message=row.message,
        now=row.created_at,
    )
    visit.status = VisitStatus(row.status)
    visit.scheduled_for = row.scheduled_for
    visit.cancellation_reason = row.cancellation_reason
    visit.completed_at = row.completed_at
    visit.cancelled_at = row.cancelled_at
    visit.created_at = row.created_at
    visit.updated_at = row.updated_at
    visit.mark_persisted(row.revision)
    return visit