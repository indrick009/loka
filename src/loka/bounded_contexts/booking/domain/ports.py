"""What Booking needs from outside: a payment provider and a drawn bus plan."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.booking.domain.line import Line


class TicketPayments(Protocol):
    async def request(
        self, *, reservation_id: uuid.UUID, amount_xaf: int, payer_phone: str, label: str
    ) -> str | None:
        """The provider's reference for a Mobile Money withdrawal, ``None`` if refused."""

    async def status(self, reference: str) -> str | None:
        """SUCCEEDED, FAILED, EXPIRED or PENDING; ``None`` when unknown yet."""


class SeatMaps(Protocol):
    async def draw(
        self,
        *,
        line: Line,
        agency: str,
        departs_at: datetime,
        taken: set[int],
        chosen: set[int] | None = None,
    ) -> str | None:
        """The stored image's object key, ``None`` if it could not be stored."""
