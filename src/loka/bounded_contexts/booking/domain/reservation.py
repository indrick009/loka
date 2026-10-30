"""A reservation: seats held on one departure, then paid.

Seats are held from the moment the traveller picks them, for a short while,
so two people cannot pay for the same seat; an unpaid hold simply lapses.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from loka.shared.domain.errors import ValidationFailed

HOLD = timedelta(minutes=15)

HELD = "HELD"
PENDING_PAYMENT = "PENDING_PAYMENT"
PAID = "PAID"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
LIVE = (HELD, PENDING_PAYMENT, PAID)


def reference() -> str:
    """Short enough to read at a counter: KB-7Q4M2X."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "KB-" + "".join(secrets.choice(alphabet) for _ in range(6))


@dataclass(slots=True)
class Reservation:
    reservation_id: uuid.UUID
    line_id: uuid.UUID
    agency_id: uuid.UUID
    client_user_id: uuid.UUID
    departs_at: datetime
    seats: list[int]
    amount_xaf: int
    commission_xaf: int
    reference: str
    created_at: datetime
    held_until: datetime
    status: str = HELD
    passengers: list[str] = field(default_factory=list)
    payer_phone: str | None = None
    payment_reference: str | None = None

    @classmethod
    def hold(
        cls,
        *,
        line_id: uuid.UUID,
        agency_id: uuid.UUID,
        client_user_id: uuid.UUID,
        departs_at: datetime,
        seats: list[int],
        price_xaf: int,
        commission_xaf: int,
        now: datetime,
    ) -> Reservation:
        if not seats:
            raise ValidationFailed("pick at least one seat")
        return cls(
            reservation_id=uuid.uuid4(),
            line_id=line_id,
            agency_id=agency_id,
            client_user_id=client_user_id,
            departs_at=departs_at,
            seats=sorted(seats),
            amount_xaf=price_xaf * len(seats),
            commission_xaf=commission_xaf * len(seats),
            reference=reference(),
            created_at=now,
            held_until=now + HOLD,
        )

    def is_live(self, now: datetime) -> bool:
        if self.status == PAID:
            return True
        return self.status in (HELD, PENDING_PAYMENT) and self.held_until > now

    def awaiting_payment(self, *, payer_phone: str, payment_reference: str, now: datetime) -> None:
        self.payer_phone = payer_phone
        self.payment_reference = payment_reference
        self.status = PENDING_PAYMENT
        # The withdrawal takes a moment to be validated on the phone.
        self.held_until = max(self.held_until, now + HOLD)

    def paid(self) -> None:
        self.status = PAID
