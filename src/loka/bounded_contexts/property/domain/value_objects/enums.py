"""Property enums and supporting value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from loka.bounded_contexts.property.domain.value_objects.money import Money
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


class PropertyStanding(StrEnum):
    """Whether the place is modern or not.

    In this market the second question after the type is not the bathroom count
    or the surface, it is "moderne ou non": it is how tenants actually filter.
    """

    MODERN = "MODERN"
    NON_MODERN = "NON_MODERN"


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
    # Step 8 of the landlord's form: how many months of rent he wants up front.
    # Separate from the caution because they are separate sums with separate
    # fates — the advance is spent, the caution comes back — and because the
    # advance is the axis the matching negotiates on.
    MISSING_ADVANCE = "MISSING_ADVANCE"
    MISSING_SECURITY = "MISSING_SECURITY"
    MISSING_QUANTITY = "MISSING_QUANTITY"
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
class RentMonths(ValueObject):
    """A number of months of rent, which is how this market counts entry money.

    "Je paye 6 mois de loyer et j'ajoute deux mois comme caution remboursable":
    the advance and the caution are both months, and the caution is added *on
    top of* the advance. Eight months change hands, two of them come back.

    Months rather than francs, because the figure is a multiple of the rent.
    Stored as an amount it silently decouples the day a landlord corrects his
    rent, and the tenant's "how many months can you pay" then needs converting
    before it can be compared with what the landlord asks.

    Not a :class:`Duration`: zero is a legitimate answer here — "aucune caution"
    is ordinary — while a lease of zero months is nonsense.
    """

    months: int

    def __post_init__(self) -> None:
        if not isinstance(self.months, int) or isinstance(self.months, bool):
            self._reject("months must be a whole number", months=self.months)
        if not 0 <= self.months <= 24:
            # A year of advance is the heaviest case the business spec cites.
            # Past two years it is a typo, and a typo stored is a listing the
            # matching can never place.
            self._reject("rent months must be between 0 and 24", months=self.months)

    def amount_at(self, rent: Money) -> Money:
        """What this represents in francs, at a given rent."""
        return Money(amount=rent.amount * self.months, currency=rent.currency)

    @property
    def label(self) -> str:
        if self.months == 0:
            return "aucun"
        return f"{self.months} mois"


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