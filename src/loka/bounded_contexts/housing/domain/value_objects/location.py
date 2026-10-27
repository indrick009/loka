"""Location value object.

Deliberately captures precision. A listing without at least a neighbourhood
is not publishable: "Douala" alone cannot be searched by a tenant who wants
a specific area, and vague localisation is a known fraud signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from loka.shared.domain.value_object import ValueObject


class LocationPrecision(StrEnum):
    CITY = "CITY"
    NEIGHBOURHOOD = "NEIGHBOURHOOD"
    ADDRESS = "ADDRESS"
    COORDINATES = "COORDINATES"


@dataclass(frozen=True, slots=True)
class Coordinates(ValueObject):

    latitude: float
    longitude: float

    def __post_init__(self) -> None:
        if not -90 <= self.latitude <= 90:
            self._reject("latitude out of range", latitude=self.latitude)
        if not -180 <= self.longitude <= 180:
            self._reject("longitude out of range", longitude=self.longitude)

    @property
    def geohash_precision(self) -> float:
        return 5


@dataclass(frozen=True, slots=True)
class Location(ValueObject):

    city: str
    neighbourhood: str | None = None
    address_hint: str | None = None
    coordinates: Coordinates | None = None

    def __post_init__(self) -> None:
        city = self.city.strip()
        if len(city) < 2:
            self._reject("city is required", city=self.city)
        object.__setattr__(self, "city", city)
        if self.neighbourhood is not None:
            cleaned = self.neighbourhood.strip()
            if len(cleaned) < 2:
                self._reject("neighbourhood is too vague", value=self.neighbourhood)
            object.__setattr__(self, "neighbourhood", cleaned)
        if self.address_hint is not None:
            cleaned_hint = self.address_hint.strip()
            if len(cleaned_hint) < 4:
                self._reject("address hint is too vague", value=self.address_hint)
            object.__setattr__(
                self, "address_hint", re.sub(r"\s+", " ", cleaned_hint)
            )

    @property
    def precision(self) -> LocationPrecision:
        if self.coordinates is not None:
            return LocationPrecision.COORDINATES
        if self.address_hint:
            return LocationPrecision.ADDRESS
        if self.neighbourhood:
            return LocationPrecision.NEIGHBOURHOOD
        return LocationPrecision.CITY

    def is_publishable(self) -> bool:
        """A listing must be locatable at neighbourhood level or better."""
        return self.precision in (
            LocationPrecision.NEIGHBOURHOOD,
            LocationPrecision.ADDRESS,
            LocationPrecision.COORDINATES,
        )

    def matches_neighbourhood(self, needle: str) -> bool:
        normalized = needle.strip().casefold()
        return bool(self.neighbourhood) and normalized in str(self.neighbourhood).casefold()

    def city_slug(self) -> str:
        return re.sub(r"[^a-z0-9]+", "-", self.city.casefold()).strip("-")