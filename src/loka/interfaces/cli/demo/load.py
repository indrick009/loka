"""DEMO ONLY — fill Yaoundé with restaurants, rentals, members, found documents
and bus lines, so every domain has something to answer with.

    python -m loka.interfaces.cli.demo.load           # create what is missing
    python -m loka.interfaces.cli.demo.load --menus   # republish today's menus

Idempotent: an owner whose number already exists is skipped. A menu of the day
ends at midnight, so ``--menus`` is meant for a daily cron. To remove the demo,
delete this package; its rows all belong to +237 600 000 xxx numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from loka.bounded_contexts.auth.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.auth.infrastructure.persistence.models import UserRow
from loka.bounded_contexts.booking.domain.line import Agency, Line
from loka.bounded_contexts.booking.infrastructure.store import SqlAlchemyBookingStore
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.infrastructure.store import SqlAlchemyDatingStore
from loka.bounded_contexts.food.domain.menu import DailyMenu, MenuDish, menu_day
from loka.bounded_contexts.food.domain.restaurant import Restaurant
from loka.bounded_contexts.food.infrastructure.store import SqlAlchemyFoodStore
from loka.bounded_contexts.found.domain.documents import Declaration
from loka.bounded_contexts.found.infrastructure.store import SqlAlchemyFoundStore
from loka.bounded_contexts.housing.domain.entities.property import Property
from loka.bounded_contexts.housing.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.housing.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    Duration,
    PropertyStanding,
    PropertyType,
    RentMonths,
)
from loka.bounded_contexts.housing.domain.value_objects.location import Coordinates, Location
from loka.bounded_contexts.housing.domain.value_objects.money import Money
from loka.bounded_contexts.housing.infrastructure.composition import (
    PROPERTY_REPOSITORY,
    PROPERTY_SEARCH_PROJECTOR,
)
from loka.interfaces.cli.demo import catalogue, pictures
from loka.interfaces.cli.seed import _upsert_landlord
from loka.shared.domain.knowledge.where import place_from
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings, get_settings
from loka.shared.infrastructure.crypto.blind_index import blind_index, derive_blind_index_key
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.object_storage.media import (
    CHAT_MEDIA_PREFIX,
    PROPERTY_MEDIA_PREFIX,
    S3MediaStorage,
)

RESTAURANT_BASE, LANDLORD_BASE, DATING_BASE, FINDER_BASE, AGENCY_BASE = 1, 20, 40, 60, 80


class DemoLoader:
    def __init__(self, settings: Settings, database: Database) -> None:
        self._settings = settings
        self._database = database
        self._media = S3MediaStorage(settings.object_storage)
        self._index_key = derive_blind_index_key(settings.encryption_key.get_secret_value())
        self._now = datetime.now(UTC)

    async def everything(self) -> dict[str, int]:
        return {
            "restaurants": await self.restaurants(),
            "listings": await self.listings(),
            "members": await self.members(),
            "documents": await self.documents(),
            "agencies": await self.agencies(),
        }

    async def _picture(self, owner: uuid.UUID, prefix: str, title: str, subtitle: str) -> str:
        stored = await self._media.put(
            pictures.card(title, subtitle), owner_id=owner, prefix=prefix,
            content_type="image/jpeg",
        )
        return stored.object_key

    async def _user(self, phone: str, name: str, roles: list[str]) -> uuid.UUID | None:
        """A new demo account, or ``None`` when that number already has one."""
        digest = blind_index(phone, key=self._index_key)
        async with self._database.session() as session:
            found = await session.scalar(
                select(UserRow.id).where(UserRow.phone_e164_hash == digest)
            )
            if found is not None:
                return None
            user_id = uuid.uuid4()
            session.add(UserRow(
                id=user_id, phone_e164=phone, phone_e164_hash=digest, display_name=name,
                roles=roles, status="ACTIVE", phone_verified_at=self._now, locale="fr",
                created_at=self._now, updated_at=self._now,
            ))
        return user_id

    async def restaurants(self) -> int:
        made = 0
        for index, (name, district, dishes) in enumerate(catalogue.RESTAURANTS):
            phone = catalogue.demo_phone(RESTAURANT_BASE + index)
            owner = await self._user(phone, name, ["TENANT"])
            if owner is None:
                continue
            place = _place(district)
            restaurant = Restaurant.register(
                owner_user_id=owner, name=name, place=place, now=self._now
            )
            menu: list[MenuDish] = []
            for dish, price, photographed in dishes:
                item = MenuDish.of(dish, price)
                if photographed:
                    key = await self._picture(owner, CHAT_MEDIA_PREFIX, dish, name)
                    item = replace(item, media=(key,))
                menu.append(item)
            async with self._database.session() as session:
                store = SqlAlchemyFoodStore(session)
                await store.add_restaurant(restaurant)
                today = DailyMenu.publish(restaurant.restaurant_id, menu, now=self._now)
                await store.save_menu(today)
            made += 1
        return made

    async def menus(self) -> int:
        """Today's menu for every demo restaurant that has none: yesterday's dishes."""
        published = 0
        async with self._database.session() as session:
            store = SqlAlchemyFoodStore(session)
            for index in range(len(catalogue.RESTAURANTS)):
                owner = await session.scalar(select(UserRow.id).where(
                    UserRow.phone_e164 == catalogue.demo_phone(RESTAURANT_BASE + index)
                ))
                restaurant = await store.restaurant_of(owner) if owner else None
                if restaurant is None:
                    continue
                rid = restaurant.restaurant_id
                if await store.menu_of(rid, menu_day(self._now)) is not None:
                    continue
                last = await store.last_menu(rid)
                if last is None:
                    continue
                dishes = [replace(dish, available=True) for dish in last.dishes]
                await store.save_menu(DailyMenu.publish(rid, dishes, now=self._now))
                published += 1
        return published

    async def listings(self) -> int:
        made = 0
        for index, (district, kind, standing, rent, rooms, lodgings, words) in enumerate(
            catalogue.LISTINGS
        ):
            phone = catalogue.demo_phone(LANDLORD_BASE + index)
            if await self._exists(phone):
                continue
            user_id, landlord_id = await _upsert_landlord(
                self._database, normalized=PhoneNumber.normalize(phone), index_key=self._index_key,
                display_name=f"Bailleur démo {index + 1}", now=self._now,
            )
            prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=self._now)
            place = _place(district)
            prop.set_type(PropertyType(kind), now=self._now)
            prop.set_standing(PropertyStanding(standing), now=self._now)
            prop.set_location(Location(
                city="Yaoundé", neighbourhood=place["neighbourhood"],
                coordinates=Coordinates(place["latitude"], place["longitude"]),
            ), now=self._now)
            prop.set_rent(Money(rent), now=self._now)
            prop.set_charges(Money(0), ChargingPolicy.INCLUDED, now=self._now)
            prop.set_deposit_months(RentMonths(1), now=self._now)
            prop.set_advance_months(RentMonths(3 if rent < 100_000 else 6), now=self._now)
            prop.set_quantity(lodgings, now=self._now)
            prop.set_rooms(BedroomCount(rooms, 1), None, now=self._now)
            prop.set_minimum_duration(Duration(3), now=self._now)
            prop.set_availability(AvailabilityWindow(self._now.date()), now=self._now)
            prop.set_conditions(words, now=self._now)
            for shot in ("Façade", "Intérieur"):
                key = await self._picture(prop.id, PROPERTY_MEDIA_PREFIX, shot, f"{words[:34]}")
                prop.add_media(PropertyMedia(
                    media_id=uuid.uuid4(), kind=MediaKind.PHOTO, object_key=key,
                    status=MediaStatus.UPLOADED,
                ), now=self._now)
            prop.publish(now=self._now, actor_id=user_id)
            prop.mark_verified(now=self._now)
            async with unit_of_work(self._database) as uow:
                await uow.repository(PROPERTY_REPOSITORY).add(prop)
                await uow.commit()
            async with unit_of_work(self._database) as uow:
                await uow.repository(PROPERTY_SEARCH_PROJECTOR).project(prop.id)
                await uow.commit()
            made += 1
        return made

    async def members(self) -> int:
        made = 0
        for index, fields in enumerate(catalogue.DATING):
            name = str(fields["first_name"])
            phone = catalogue.demo_phone(DATING_BASE + index)
            user = await self._user(phone, name, ["TENANT"])
            if user is None:
                continue
            photo = await self._picture(user, CHAT_MEDIA_PREFIX, name, f"{fields['age']} ans")
            profile = Profile.create(user_id=user, now=self._now, fields={
                **fields, "country": "Cameroun", "photo_key": photo,
            })
            async with self._database.session() as session:
                await SqlAlchemyDatingStore(session).add(profile)
            made += 1
        return made

    async def documents(self) -> int:
        made = 0
        for index, (kind, district, when, words) in enumerate(catalogue.FOUND):
            finder = await self._user(
                catalogue.demo_phone(FINDER_BASE + index), f"Trouveur démo {index + 1}", ["TENANT"]
            )
            if finder is None:
                continue
            photo = await self._picture(finder, CHAT_MEDIA_PREFIX, kind, "document flouté")
            declaration = Declaration(
                declaration_id=uuid.uuid4(), finder_user_id=finder, document_type=kind,
                place=_place(district), found_on=when, description=words, photos=[photo],
                created_at=self._now - timedelta(days=1),
            )
            async with self._database.session() as session:
                await SqlAlchemyFoundStore(session).add_declaration(declaration)
            made += 1
        return made

    async def agencies(self) -> int:
        made = 0
        for index, (name, lines) in enumerate(catalogue.AGENCIES):
            phone = catalogue.demo_phone(AGENCY_BASE + index)
            async with self._database.session() as session:
                store = SqlAlchemyBookingStore(session)
                if await store.agency_by_phone(phone) is not None:
                    continue
                agency = Agency(
                    uuid.uuid4(), name, phone, hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                    500, self._now,
                )
                await store.add_agency(agency)
                for origin, destination, kind, price, seats, layout, times in lines:
                    await store.add_line(Line.create(
                        agency_id=agency.agency_id, origin=origin, destination=destination,
                        bus_class=kind, price_xaf=price, seats=seats, layout=layout, times=times,
                    ))
            made += 1
        return made

    async def _exists(self, phone: str) -> bool:
        digest = blind_index(phone, key=self._index_key)
        async with self._database.session() as session:
            found = await session.scalar(
                select(UserRow.id).where(UserRow.phone_e164_hash == digest)
            )
        return found is not None


def _place(district: str) -> dict[str, Any]:
    place = place_from({"city": "Yaoundé", "neighbourhood": district})
    if place is None or "latitude" not in place:
        raise RuntimeError(f"unknown Yaoundé district: {district}")
    return place


async def run(*, menus_only: bool) -> dict[str, int]:
    settings = get_settings()
    database = Database(settings.database, application_name="kora-demo")
    await database.connect()
    try:
        loader = DemoLoader(settings, database)
        if menus_only:
            return {"menus": await loader.menus()}
        return await loader.everything()
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="DEMO ONLY: fill Yaoundé with demo data.")
    parser.add_argument("--menus", action="store_true", help="only republish today's menus")
    print(asyncio.run(run(menus_only=parser.parse_args().menus)))


if __name__ == "__main__":
    main()
