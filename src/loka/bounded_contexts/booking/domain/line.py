"""A bus agency, and the lines it runs.

Agencies here leave at fixed hours every day (5 h, 7 h 30, 9 h 30…), so a line
is a route with its daily times, its price, its class and the bus it runs:
how many seats and how they are laid out. A departure is a line, a date and
one of its times; nothing is stored for it until a seat is booked.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from loka.shared.domain.errors import ValidationFailed

DOUALA = timezone(timedelta(hours=1))
LAYOUTS = {"2+2": (2, 2), "2+1": (2, 1)}
CLASSES = ("CLASSIQUE", "VIP")
_TIME = re.compile(r"^([01]?\d|2[0-3])[:hH]([0-5]\d)$")


@dataclass(frozen=True, slots=True)
class Agency:
    agency_id: uuid.UUID
    name: str
    phone: str
    key_digest: str
    commission_xaf: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Line:
    line_id: uuid.UUID
    agency_id: uuid.UUID
    origin: str
    destination: str
    bus_class: str
    price_xaf: int
    seats: int
    layout: str
    times: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        agency_id: uuid.UUID,
        origin: str,
        destination: str,
        bus_class: str,
        price_xaf: int,
        seats: int,
        layout: str,
        times: list[str],
    ) -> Line:
        if origin == destination:
            raise ValidationFailed("a line joins two different cities")
        if bus_class not in CLASSES or layout not in LAYOUTS:
            raise ValidationFailed("unknown class or layout")
        if not 500 <= price_xaf <= 100_000 or not 8 <= seats <= 90:
            raise ValidationFailed("price or seat count out of range")
        cleaned = sorted({_clock(raw) for raw in times})
        if not cleaned:
            raise ValidationFailed("a line needs at least one departure time")
        return cls(
            uuid.uuid4(),
            agency_id,
            origin,
            destination,
            bus_class,
            price_xaf,
            seats,
            layout,
            tuple(cleaned),
        )

    def departures(self, day: date, *, now: datetime) -> list[datetime]:
        """The departures of that day still ahead of ``now``."""
        moments = [
            datetime.combine(day, time(int(at[:2]), int(at[3:])), tzinfo=DOUALA)
            for at in self.times
        ]
        return [moment for moment in moments if moment > now]


def _clock(raw: str) -> str:
    match = _TIME.match(raw.strip())
    if match is None:
        raise ValidationFailed("a time is written like 07:30", context={"time": raw})
    return f"{int(match.group(1)):02d}:{match.group(2)}"
