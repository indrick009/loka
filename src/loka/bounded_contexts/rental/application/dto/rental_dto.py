"""Rental application DTOs.

A projection of the aggregate for transports; it never exposes ORM rows and
keeps money in XAF integers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
    RentalApplicationStatus,
)


@dataclass(frozen=True, slots=True)
class RentalApplicationView:
    application_id: str
    property_id: str
    landlord_id: str
    tenant_id: str
    status: str
    message: str | None
    proposed_rent_xaf: int | None
    proposed_deposit_xaf: int | None
    offer_count: int
    decision_reason: str | None
    decided_at: datetime | None
    confirmed_at: datetime | None

    @classmethod
    def from_aggregate(cls, application: RentalApplication) -> RentalApplicationView:
        return cls(
            application_id=str(application.application_id),
            property_id=str(application.property_id),
            landlord_id=str(application.landlord_id),
            tenant_id=str(application.tenant_id),
            status=application.status.value,
            message=application.message,
            proposed_rent_xaf=(
                application.proposed_rent.amount if application.proposed_rent else None
            ),
            proposed_deposit_xaf=(
                application.proposed_deposit.amount
                if application.proposed_deposit
                else None
            ),
            offer_count=application.offer_count,
            decision_reason=application.decision_reason,
            decided_at=application.decided_at,
            confirmed_at=application.confirmed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "property_id": self.property_id,
            "landlord_id": self.landlord_id,
            "tenant_id": self.tenant_id,
            "status": self.status,
            "message": self.message,
            "proposed_rent_xaf": self.proposed_rent_xaf,
            "proposed_deposit_xaf": self.proposed_deposit_xaf,
            "offer_count": self.offer_count,
            "decision_reason": self.decision_reason,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
        }


__all__ = ["RentalApplicationStatus", "RentalApplicationView"]