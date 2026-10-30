"""The port Food persists through."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Protocol

from loka.bounded_contexts.food.domain.menu import DailyMenu
from loka.bounded_contexts.food.domain.restaurant import Restaurant


class FoodStore(Protocol):
    async def restaurant_of(self, owner_user_id: uuid.UUID) -> Restaurant | None: ...

    async def add_restaurant(self, restaurant: Restaurant) -> None: ...

    async def menu_of(self, restaurant_id: uuid.UUID, day: date) -> DailyMenu | None: ...

    async def last_menu(self, restaurant_id: uuid.UUID) -> DailyMenu | None: ...

    async def save_menu(self, menu: DailyMenu) -> None:
        """Insert or replace the restaurant's menu for that day."""

    async def open_menus(self, day: date) -> list[tuple[Restaurant, DailyMenu]]: ...

    async def record_request(
        self,
        *,
        restaurant_id: uuid.UUID,
        client_user_id: uuid.UUID,
        dish: dict[str, Any],
        now: datetime,
    ) -> None: ...
