"""SQLAlchemy implementation of ``FoodStore``."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.food.domain.menu import DailyMenu, MenuDish
from loka.bounded_contexts.food.domain.restaurant import Restaurant
from loka.bounded_contexts.food.infrastructure.models import (
    FoodDailyMenuRow,
    FoodRequestRow,
    FoodRestaurantRow,
)


def _restaurant(row: FoodRestaurantRow) -> Restaurant:
    return Restaurant(row.id, row.owner_user_id, row.name, dict(row.place), row.created_at)


def _menu(row: FoodDailyMenuRow) -> DailyMenu:
    return DailyMenu(
        menu_id=row.id,
        restaurant_id=row.restaurant_id,
        day=row.day,
        dishes=[MenuDish.from_dict(item) for item in row.dishes],
        published_at=row.published_at,
        closed_at=row.closed_at,
    )


class SqlAlchemyFoodStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def restaurant_of(self, owner_user_id: uuid.UUID) -> Restaurant | None:
        row = (
            await self._session.execute(
                select(FoodRestaurantRow).where(FoodRestaurantRow.owner_user_id == owner_user_id)
            )
        ).scalar_one_or_none()
        return _restaurant(row) if row else None

    async def add_restaurant(self, restaurant: Restaurant) -> None:
        self._session.add(
            FoodRestaurantRow(
                id=restaurant.restaurant_id,
                owner_user_id=restaurant.owner_user_id,
                name=restaurant.name,
                place=restaurant.place,
                created_at=restaurant.created_at,
            )
        )
        await self._session.flush()

    async def menu_of(self, restaurant_id: uuid.UUID, day: date) -> DailyMenu | None:
        row = (
            await self._session.execute(
                select(FoodDailyMenuRow).where(
                    FoodDailyMenuRow.restaurant_id == restaurant_id, FoodDailyMenuRow.day == day
                )
            )
        ).scalar_one_or_none()
        return _menu(row) if row else None

    async def last_menu(self, restaurant_id: uuid.UUID) -> DailyMenu | None:
        row = (
            await self._session.execute(
                select(FoodDailyMenuRow)
                .where(FoodDailyMenuRow.restaurant_id == restaurant_id)
                .order_by(FoodDailyMenuRow.day.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _menu(row) if row else None

    async def save_menu(self, menu: DailyMenu) -> None:
        await self._session.execute(
            delete(FoodDailyMenuRow).where(
                FoodDailyMenuRow.restaurant_id == menu.restaurant_id,
                FoodDailyMenuRow.day == menu.day,
                FoodDailyMenuRow.id != menu.menu_id,
            )
        )
        row = await self._session.get(FoodDailyMenuRow, menu.menu_id)
        dishes = [dish.as_dict() for dish in menu.dishes]
        if row is None:
            self._session.add(
                FoodDailyMenuRow(
                    id=menu.menu_id,
                    restaurant_id=menu.restaurant_id,
                    day=menu.day,
                    dishes=dishes,
                    published_at=menu.published_at,
                    closed_at=menu.closed_at,
                )
            )
        else:
            row.dishes = dishes
            row.closed_at = menu.closed_at
        await self._session.flush()

    async def open_menus(self, day: date) -> list[tuple[Restaurant, DailyMenu]]:
        rows = (
            await self._session.execute(
                select(FoodRestaurantRow, FoodDailyMenuRow)
                .join(FoodDailyMenuRow, FoodDailyMenuRow.restaurant_id == FoodRestaurantRow.id)
                .where(FoodDailyMenuRow.day == day, FoodDailyMenuRow.closed_at.is_(None))
            )
        ).all()
        return [(_restaurant(restaurant), _menu(menu)) for restaurant, menu in rows]

    async def record_request(
        self,
        *,
        restaurant_id: uuid.UUID,
        client_user_id: uuid.UUID,
        dish: dict[str, Any],
        now: datetime,
    ) -> None:
        self._session.add(
            FoodRequestRow(
                id=uuid.uuid4(),
                restaurant_id=restaurant_id,
                client_user_id=client_user_id,
                dish=dish,
                created_at=now,
            )
        )
