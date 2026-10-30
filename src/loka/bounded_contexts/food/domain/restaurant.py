"""A restaurant: registered once, by its owner, with a name and a place."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.shared.domain.errors import ValidationFailed


@dataclass(frozen=True, slots=True)
class Restaurant:
    restaurant_id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    place: dict[str, Any]
    created_at: datetime

    @classmethod
    def register(
        cls, *, owner_user_id: uuid.UUID, name: str, place: dict[str, Any], now: datetime
    ) -> Restaurant:
        cleaned = " ".join(name.split())
        if not 2 <= len(cleaned) <= 80:
            raise ValidationFailed("a restaurant name is 2 to 80 characters")
        if not place.get("city"):
            raise ValidationFailed("a restaurant needs a city")
        return cls(uuid.uuid4(), owner_user_id, cleaned, dict(place), now)


@dataclass(frozen=True, slots=True)
class DishOffer:
    """One dish cooking today somewhere, as a search returns it."""

    restaurant: Restaurant
    menu_id: uuid.UUID
    dish_name: str
    price_xaf: int
    distance_km: float | None
