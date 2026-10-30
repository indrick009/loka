"""The menu of the day: the only thing a client's search ever reads.

Availability is not a property of a dish, it is a daily publication. A menu
belongs to one restaurant and one day in Douala time, so it stops existing at
midnight whatever the restaurateur forgot. Closing is his call: one dish
("le ndolè est fini"), or the whole day ("c'est fini pour aujourd'hui").
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.knowledge.dishes import dish_key
from loka.shared.domain.knowledge.market import normalize

DOUALA = timezone(timedelta(hours=1))
MIN_DISH_PRICE_XAF = 100
MAX_DISH_PRICE_XAF = 100_000


def menu_day(now: datetime) -> date:
    return now.astimezone(DOUALA).date()


@dataclass(frozen=True, slots=True)
class MenuDish:
    name: str
    price_xaf: int
    key: str | None = None
    available: bool = True

    @classmethod
    def of(cls, name: Any, price: Any) -> MenuDish:
        text = " ".join(str(name or "").split())[:60]
        if len(text) < 2:
            raise ValidationFailed("a dish needs a name", context={"name": text})
        if isinstance(price, bool) or not isinstance(price, int):
            raise ValidationFailed("a dish needs a price in FCFA", context={"name": text})
        if not MIN_DISH_PRICE_XAF <= price <= MAX_DISH_PRICE_XAF:
            raise ValidationFailed("dish price out of range", context={"price": price})
        return cls(name=text[0].upper() + text[1:], price_xaf=price, key=dish_key(text))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "price": self.price_xaf,
            "key": self.key,
            "available": self.available,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MenuDish:
        return cls(
            name=str(raw["name"]),
            price_xaf=int(raw["price"]),
            key=raw.get("key"),
            available=bool(raw.get("available", True)),
        )

    def is_named(self, said: str) -> bool:
        key = dish_key(said)
        if key is not None and key == self.key:
            return True
        folded, own = normalize(said), normalize(self.name)
        return bool(folded) and (folded in own or own in folded)


@dataclass(slots=True)
class DailyMenu:
    menu_id: uuid.UUID
    restaurant_id: uuid.UUID
    day: date
    dishes: list[MenuDish]
    published_at: datetime
    closed_at: datetime | None = None

    @classmethod
    def publish(
        cls, restaurant_id: uuid.UUID, dishes: list[MenuDish], *, now: datetime
    ) -> DailyMenu:
        if not dishes:
            raise ValidationFailed("a menu of the day needs at least one dish")
        return cls(
            menu_id=uuid.uuid4(),
            restaurant_id=restaurant_id,
            day=menu_day(now),
            dishes=list(dishes),
            published_at=now,
        )

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def open_dishes(self) -> list[MenuDish]:
        return [dish for dish in self.dishes if dish.available] if self.is_open else []

    def close_dishes(self, said: list[str], *, now: datetime) -> list[str]:
        """Take the named dishes off today's menu; the names actually closed."""
        closed: list[str] = []
        updated: list[MenuDish] = []
        for dish in self.dishes:
            if dish.available and any(dish.is_named(name) for name in said):
                closed.append(dish.name)
                updated.append(MenuDish(dish.name, dish.price_xaf, dish.key, available=False))
            else:
                updated.append(dish)
        self.dishes = updated
        if not any(dish.available for dish in updated):
            self.close(now=now)
        return closed

    def close(self, *, now: datetime) -> None:
        self.closed_at = self.closed_at or now
