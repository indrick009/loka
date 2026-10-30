"""SQLAlchemy implementation of ``BookingStore``."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.booking.domain.line import Agency, Line
from loka.bounded_contexts.booking.domain.reservation import (
    PAID,
    PENDING_PAYMENT,
    Reservation,
)
from loka.bounded_contexts.booking.infrastructure.models import (
    BookingAgencyRow,
    BookingLineRow,
    BookingReservationRow,
)


def _agency(row: BookingAgencyRow) -> Agency:
    return Agency(row.id, row.name, row.phone, row.key_digest, row.commission_xaf, row.created_at)


def _line(row: BookingLineRow) -> Line:
    return Line(
        row.id,
        row.agency_id,
        row.origin,
        row.destination,
        row.bus_class,
        row.price_xaf,
        row.seats,
        row.layout,
        tuple(row.times),
    )


def _reservation(row: BookingReservationRow) -> Reservation:
    return Reservation(
        reservation_id=row.id,
        line_id=row.line_id,
        agency_id=row.agency_id,
        client_user_id=row.client_user_id,
        departs_at=row.departs_at,
        seats=list(row.seats),
        amount_xaf=row.amount_xaf,
        commission_xaf=row.commission_xaf,
        reference=row.reference,
        created_at=row.created_at,
        held_until=row.held_until,
        status=row.status,
        passengers=list(row.passengers),
        payer_phone=row.payer_phone,
        payment_reference=row.payment_reference,
    )


class SqlAlchemyBookingStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_agency(self, agency: Agency) -> None:
        self._session.add(
            BookingAgencyRow(
                id=agency.agency_id,
                name=agency.name,
                phone=agency.phone,
                key_digest=agency.key_digest,
                commission_xaf=agency.commission_xaf,
                created_at=agency.created_at,
            )
        )
        await self._session.flush()

    async def agency(self, agency_id: uuid.UUID) -> Agency | None:
        row = await self._session.get(BookingAgencyRow, agency_id)
        return _agency(row) if row else None

    async def agency_by_phone(self, phone: str) -> Agency | None:
        row = (
            await self._session.execute(
                select(BookingAgencyRow).where(BookingAgencyRow.phone == phone)
            )
        ).scalar_one_or_none()
        return _agency(row) if row else None

    async def add_line(self, line: Line) -> None:
        self._session.add(
            BookingLineRow(
                id=line.line_id,
                agency_id=line.agency_id,
                origin=line.origin,
                destination=line.destination,
                bus_class=line.bus_class,
                price_xaf=line.price_xaf,
                seats=line.seats,
                layout=line.layout,
                times=list(line.times),
            )
        )
        await self._session.flush()

    async def lines_of(self, agency_id: uuid.UUID) -> list[Line]:
        rows = await self._session.execute(
            select(BookingLineRow).where(BookingLineRow.agency_id == agency_id)
        )
        return [_line(row) for row in rows.scalars()]

    async def line(self, line_id: uuid.UUID) -> Line | None:
        row = await self._session.get(BookingLineRow, line_id)
        return _line(row) if row else None

    async def lines_between(self, origin: str, destination: str) -> list[Line]:
        rows = await self._session.execute(
            select(BookingLineRow).where(
                BookingLineRow.origin == origin, BookingLineRow.destination == destination
            )
        )
        return [_line(row) for row in rows.scalars()]

    async def taken_seats(
        self, line_id: uuid.UUID, departs_at: datetime, *, now: datetime
    ) -> set[int]:
        rows = await self._session.execute(
            select(BookingReservationRow).where(
                BookingReservationRow.line_id == line_id,
                BookingReservationRow.departs_at == departs_at,
            )
        )
        taken: set[int] = set()
        for row in rows.scalars():
            if _reservation(row).is_live(now):
                taken.update(int(seat) for seat in row.seats)
        return taken

    async def save(self, reservation: Reservation) -> None:
        row = await self._session.get(BookingReservationRow, reservation.reservation_id)
        values = {
            "line_id": reservation.line_id,
            "agency_id": reservation.agency_id,
            "client_user_id": reservation.client_user_id,
            "departs_at": reservation.departs_at,
            "seats": list(reservation.seats),
            "passengers": list(reservation.passengers),
            "amount_xaf": reservation.amount_xaf,
            "commission_xaf": reservation.commission_xaf,
            "reference": reservation.reference,
            "status": reservation.status,
            "held_until": reservation.held_until,
            "payer_phone": reservation.payer_phone,
            "payment_reference": reservation.payment_reference,
            "created_at": reservation.created_at,
        }
        if row is None:
            self._session.add(BookingReservationRow(id=reservation.reservation_id, **values))
        else:
            for name, value in values.items():
                setattr(row, name, value)
        await self._session.flush()

    async def reservation(self, reservation_id: uuid.UUID) -> Reservation | None:
        row = await self._session.get(BookingReservationRow, reservation_id)
        return _reservation(row) if row else None

    async def awaiting_payment(self) -> list[Reservation]:
        rows = await self._session.execute(
            select(BookingReservationRow).where(BookingReservationRow.status == PENDING_PAYMENT)
        )
        return [_reservation(row) for row in rows.scalars()]

    async def reservations_of(self, agency_id: uuid.UUID) -> list[Reservation]:
        rows = await self._session.execute(
            select(BookingReservationRow)
            .where(
                BookingReservationRow.agency_id == agency_id,
                BookingReservationRow.status.in_((PAID, PENDING_PAYMENT)),
            )
            .order_by(BookingReservationRow.departs_at)
        )
        return [_reservation(row) for row in rows.scalars()]
