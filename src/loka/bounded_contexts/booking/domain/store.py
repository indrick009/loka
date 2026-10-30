"""The port Booking persists through."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.booking.domain.line import Agency, Line
from loka.bounded_contexts.booking.domain.reservation import Reservation


class BookingStore(Protocol):
    async def add_agency(self, agency: Agency) -> None: ...

    async def agency(self, agency_id: uuid.UUID) -> Agency | None: ...

    async def agency_by_phone(self, phone: str) -> Agency | None: ...

    async def add_line(self, line: Line) -> None: ...

    async def lines_of(self, agency_id: uuid.UUID) -> list[Line]: ...

    async def line(self, line_id: uuid.UUID) -> Line | None: ...

    async def lines_between(self, origin: str, destination: str) -> list[Line]: ...

    async def taken_seats(
        self, line_id: uuid.UUID, departs_at: datetime, *, now: datetime
    ) -> set[int]:
        """Seats paid for, or held and not yet lapsed."""

    async def save(self, reservation: Reservation) -> None: ...

    async def reservation(self, reservation_id: uuid.UUID) -> Reservation | None: ...

    async def awaiting_payment(self) -> list[Reservation]: ...

    async def reservations_of(self, agency_id: uuid.UUID) -> list[Reservation]: ...
