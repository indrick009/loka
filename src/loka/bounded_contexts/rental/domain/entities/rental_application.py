"""Rental application aggregate.

The application is the consistency boundary for a tenant's intent to rent one
specific property. It owns the negotiation history (successive counter-offers)
and the final decision, but never the property itself: reserving or renting the
property is a cross-context effect the application layer triggers explicitly.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition


class RentalApplicationStatus(StrEnum):
    """Lifecycle of a rental application.

    ``INTERESTED`` records the tenant's intent, ``NEGOTIATING`` covers any
    number of counter-offers, ``ACCEPTED`` means the landlord agreed to the
    terms, and ``CONFIRMED`` closes the loop once the rental is finalised.
    """

    INTERESTED = "INTERESTED"
    NEGOTIATING = "NEGOTIATING"
    ACCEPTED = "ACCEPTED"
    CONFIRMED = "CONFIRMED"
    REFUSED = "REFUSED"
    WITHDRAWN = "WITHDRAWN"


_TERMINAL = frozenset(
    {
        RentalApplicationStatus.CONFIRMED,
        RentalApplicationStatus.REFUSED,
        RentalApplicationStatus.WITHDRAWN,
    }
)

_OPEN = frozenset(
    {RentalApplicationStatus.INTERESTED, RentalApplicationStatus.NEGOTIATING}
)


class RentalApplication(AggregateRoot):
    __slots__ = (
        "application_id",
        "confirmed_at",
        "created_at",
        "currency",
        "decided_at",
        "decision_reason",
        "landlord_id",
        "message",
        "offer_count",
        "property_id",
        "proposed_deposit",
        "proposed_rent",
        "status",
        "tenant_id",
        "updated_at",
        "withdrawn_at",
    )

    def __init__(
        self,
        *,
        application_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        tenant_id: uuid.UUID,
        message: str | None = None,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="RentalApplication")
        self._assign_id(application_id)
        self.application_id = application_id
        self.property_id = property_id
        self.landlord_id = landlord_id
        self.tenant_id = tenant_id
        self.message = (message or "").strip() or None
        self.status = RentalApplicationStatus.INTERESTED
        self.proposed_rent: Money | None = None
        self.proposed_deposit: Money | None = None
        self.currency = "XAF"
        self.offer_count = 0
        self.decision_reason: str | None = None
        self.decided_at: datetime | None = None
        self.confirmed_at: datetime | None = None
        self.withdrawn_at: datetime | None = None
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self.record(
            "RentalApplicationCreated",
            occurred_at=now,
            payload={
                "application_id": str(self.application_id),
                "property_id": str(self.property_id),
                "landlord_id": str(self.landlord_id),
                "tenant_id": str(self.tenant_id),
            },
        )

    @property
    def is_open(self) -> bool:
        return self.status in _OPEN

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def propose_terms(
        self,
        *,
        rent: Money,
        deposit: Money | None = None,
        message: str | None = None,
        actor_id: uuid.UUID | None = None,
        now: datetime,
    ) -> None:
        """Record a rent proposal (either party may counter-offer)."""
        self._assert_open()
        self.proposed_rent = rent.require_positive(reason="proposed rent must be positive")
        self.proposed_deposit = (
            deposit.require_non_negative(reason="deposit cannot be negative")
            if deposit is not None
            else None
        )
        self.message = (message or "").strip() or self.message
        self.currency = self.proposed_rent.currency
        self.offer_count += 1
        self.status = RentalApplicationStatus.NEGOTIATING
        self._touch(now)
        self.record(
            "RentalOfferProposed",
            occurred_at=now,
            actor_id=actor_id,
            payload={
                "application_id": str(self.application_id),
                "rent": self.proposed_rent.amount,
                "deposit": self.proposed_deposit.amount if self.proposed_deposit else None,
                "offer_count": self.offer_count,
            },
        )

    def accept(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if not self.is_open:
            raise InvalidStateTransition(
                f"cannot accept a {self.status.value} application",
                context={"application_id": str(self.application_id)},
            )
        self.status = RentalApplicationStatus.ACCEPTED
        self.decided_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "RentalApplicationAccepted",
            occurred_at=now,
            actor_id=actor_id,
            payload={
                "application_id": str(self.application_id),
                "property_id": str(self.property_id),
                "tenant_id": str(self.tenant_id),
            },
        )

    def refuse(
        self,
        *,
        reason: str | None = None,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        if self.status in (RentalApplicationStatus.CONFIRMED, RentalApplicationStatus.REFUSED):
            raise InvalidStateTransition(
                f"cannot refuse a {self.status.value} application",
                context={"application_id": str(self.application_id)},
            )
        self.status = RentalApplicationStatus.REFUSED
        self.decision_reason = (reason or "").strip() or None
        self.decided_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "RentalApplicationRefused",
            occurred_at=now,
            actor_id=actor_id,
            payload={"application_id": str(self.application_id), "reason": self.decision_reason},
        )

    def confirm(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is not RentalApplicationStatus.ACCEPTED:
            raise InvalidStateTransition(
                "only an accepted application can be confirmed",
                context={"application_id": str(self.application_id), "status": self.status.value},
            )
        self.status = RentalApplicationStatus.CONFIRMED
        self.confirmed_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "RentalConfirmed",
            occurred_at=now,
            actor_id=actor_id,
            payload={
                "application_id": str(self.application_id),
                "property_id": str(self.property_id),
                "tenant_id": str(self.tenant_id),
            },
        )

    def withdraw(self, *, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.is_terminal:
            raise InvalidStateTransition(
                f"cannot withdraw a {self.status.value} application",
                context={"application_id": str(self.application_id)},
            )
        self.status = RentalApplicationStatus.WITHDRAWN
        self.withdrawn_at = ensure_utc(now)
        self._touch(now)
        self.record(
            "RentalApplicationWithdrawn",
            occurred_at=now,
            actor_id=actor_id,
            payload={"application_id": str(self.application_id)},
        )

    def _assert_open(self) -> None:
        if not self.is_open:
            raise InvalidStateTransition(
                f"cannot negotiate a {self.status.value} application",
                context={"application_id": str(self.application_id)},
            )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()