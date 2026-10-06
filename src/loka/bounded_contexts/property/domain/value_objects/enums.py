"""Property enums and supporting value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from loka.shared.domain.value_object import ValueObject


class PropertyType(StrEnum):
    APARTMENT = "APARTMENT"
    HOUSE = "HOUSE"
    STUDIO = "STUDIO"
    ROOM = "ROOM"
    DUPLEX = "DUPLEX"
    LOFT = "LOFT"
    COMMERCIAL = "COMMERCIAL"
    LAND = "LAND"


class PropertyStatus(StrEnum):
    DRAFT = "DRAFT"
    AVAILABLE = "AVAILABLE"
    RESERVED = "RESERVED"
    RENTED = "RENTED"
    UNAVAILABLE = "UNAVAILABLE"
    SUSPENDED = "SUSPENDED"
    ARCHIVED = "ARCHIVED"


class DataQualityIssue(StrEnum):
    MISSING_CHARGES = "MISSING_CHARGES"
    MISSING_PHOTOS = "MISSING_PHOTOS"
    MISSING_MINIMUM_DURATION = "MISSING_MINIMUM_DURATION"
    MISSING_AVAILABILITY_DATE = "MISSING_AVAILABILITY_DATE"
    VAGUE_LOCATION = "VAGUE_LOCATION"
    SUSPICIOUS_PRICE = "SUSPICIOUS_PRICE"
    MISSING_DEPOSIT = "MISSING_DEPOSIT"
    AMBIGUOUS_CONDITIONS = "AMBIGUOUS_CONDITIONS"

class ChargingPolicy(StrEnum):
    INCLUDED = "INCLUDED"
    EXTRA = "EXTRA"
    UNKNOWN = "UNKNOWN"

class PropertyDraftData(StrEnum):
    TYPE = "TYPE"
    LOCATION = "LOCATION"
    PRICE = "PRICE"
    CHARGES = "CHARGES"
    FEATURES = "FEATURES"
    MEDIA = "MEDIA"
    DEPOSIT = "DEPOSIT"
    MINIMUM_DURATION = "MINIMUM_DURATION"
    CONDITIONS = "CONDITIONS"
    AVAILABILITY = "AVAILABILITY"
    SUMMARY_CONFIRMATION = "SUMMARY_CONFIRMATION"


class AvailabilityAnswer(StrEnum):
    """Answers a landlord may give when availability is re-confirmed."""

    STILL_AVAILABLE = "STILL_AVAILABLE"
    RENTED = "RENTED"
    TEMPORARILY_UNAVAILABLE = "TEMPORARILY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class Duration(ValueObject):
    """Minimum lease length; always expressed in whole months."""


    months: int

    def __post_init__(self) -> None:
        if not 1 <= self.months <= 120:
            self._reject("minimum duration must be between 1 and 120 months", months=self.months)

    @property
    def label(self) -> str:
        if self.months == 12:
            return "1 an"
        return f"{self.months} mois"

    def months_from(self, start: date) -> date:
        if self.months == 12:
            return start.replace(year=start.year + 1)
        total = start.month - 1 + self.months
        year = start.year + total // 12
        month = total % 12 + 1
        day = min(start.day, _days_in_month(year, month))
        return date(year, month, day)


@dataclass(frozen=True, slots=True)
class AvailabilityWindow(ValueObject):
    """When the property becomes occupiable. Never inferred from a status flag."""


    available_from: date

    def is_immediate(self) -> bool:
        return self.available_from <= date.today()

    def label(self) -> str:
        if self.is_immediate():
            return "disponible immédiatement"
        return f"disponible à partir du {self.available_from.strftime('%d/%m/%Y')}"


@dataclass(frozen=True, slots=True)
class BedroomCount(ValueObject):

    count: int
    bathrooms: int = 1

    def __post_init__(self) -> None:
        if not 0 <= self.count <= 50:
            self._reject("bedroom count out of range", count=self.count)
        if not 0 <= self.bathrooms <= 50:
            self._reject("bathroom count out of range", bathrooms=self.bathrooms)


@dataclass(frozen=True, slots=True)
class SurfaceArea(ValueObject):

    square_metres: int

    def __post_init__(self) -> None:
        if not 5 <= self.square_metres <= 10_000:
            self._reject("surface area out of realistic bounds", square_metres=self.square_metres)


def _days_in_month(year: int, month: int) -> int:
    import calendar

    return calendar.monthrange(year, month)[1]