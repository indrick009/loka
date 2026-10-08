"""Rental application <-> row mapper."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
    RentalApplicationStatus,
)
from loka.bounded_contexts.rental.infrastructure.persistence.models import (
    RentalApplicationRow,
)


def to_row(application: RentalApplication) -> dict[str, Any]:
    rent = application.proposed_rent
    deposit = application.proposed_deposit
    return {
        "id": application.application_id,
        "property_id": application.property_id,
        "landlord_id": application.landlord_id,
        "tenant_id": application.tenant_id,
        "status": application.status.value,
        "message": application.message,
        "proposed_rent_xaf": rent.amount if rent else None,
        "proposed_deposit_xaf": deposit.amount if deposit else None,
        "currency": application.currency,
        "offer_count": application.offer_count,
        "decision_reason": application.decision_reason,
        "decided_at": application.decided_at,
        "confirmed_at": application.confirmed_at,
        "withdrawn_at": application.withdrawn_at,
        "revision": application.version,
        "created_at": application.created_at,
        "updated_at": application.updated_at,
    }


def from_row(row: RentalApplicationRow) -> RentalApplication:
    application = RentalApplication(
        application_id=row.id,
        property_id=row.property_id,
        landlord_id=row.landlord_id,
        tenant_id=row.tenant_id,
        message=row.message,
        now=row.created_at,
    )
    application.status = RentalApplicationStatus(row.status)
    application.proposed_rent = (
        Money(row.proposed_rent_xaf, row.currency)
        if row.proposed_rent_xaf is not None
        else None
    )
    application.proposed_deposit = (
        Money(row.proposed_deposit_xaf, row.currency)
        if row.proposed_deposit_xaf is not None
        else None
    )
    application.currency = row.currency
    application.offer_count = row.offer_count
    application.decision_reason = row.decision_reason
    application.decided_at = row.decided_at
    application.confirmed_at = row.confirmed_at
    application.withdrawn_at = row.withdrawn_at
    application.created_at = row.created_at
    application.updated_at = row.updated_at
    application.mark_persisted(row.revision)
    return application