"""Public property search over HTTP.

The endpoint reads the search projection, never the write model, and is
anonymous on purpose: browsing a marketplace is not an authenticated act and
the projection only exposes fields that are already public on a listing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    Duration,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.property_repository import (
    SqlAlchemyPropertyRepository,
)
from loka.bounded_contexts.property.infrastructure.persistence.search_projector import (
    SqlAlchemySearchProjector,
)
from loka.interfaces.http.app import create_app
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http


def _published_listing(
    landlord_id: uuid.UUID,
    *,
    now: datetime = NOW,
    price: int = 150_000,
    city: str = "Douala",
    neighbourhood: str = "Bali",
    verified: bool = True,
) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=now)
    prop.set_type(PropertyType.APARTMENT, now=now)
    prop.set_location(Location(city=city, neighbourhood=neighbourhood), now=now)
    prop.set_rent(Money(price), now=now)
    prop.set_charges(Money(25_000), ChargingPolicy.EXTRA, now=now)
    prop.set_deposit(Money(price * 2), now=now)
    prop.set_rooms(BedroomCount(2, 1), SurfaceArea(65), now=now)
    prop.set_minimum_duration(Duration(6), now=now)
    prop.set_availability(AvailabilityWindow(now.date() + timedelta(days=5)), now=now)
    prop.set_conditions("Recently renovated", now=now)
    prop.add_media(
        PropertyMedia(
            media_id=uuid.uuid4(),
            object_key=f"landlords/{landlord_id}/{prop.id}/1.jpg",
            kind=MediaKind.PHOTO,
            status=MediaStatus.UPLOADED,
            checksum="abc123",
        ),
        now=now,
    )
    prop.publish(now=now, actor_id=landlord_id)
    if verified:
        prop.mark_verified(now=now)
    return prop


async def _store_and_project(database: Database, properties: list[Property]) -> None:
    """Write the aggregates, then feed the projection the way the consumer would."""
    async with database.session() as session:
        repository = SqlAlchemyPropertyRepository(session)
        for prop in properties:
            await repository.add(prop)
        await session.commit()
    async with database.session() as session:
        projector = SqlAlchemySearchProjector(session)
        for prop in properties:
            await projector.project(prop.id)
        await session.commit()


async def test_search_is_anonymous_and_returns_published_listings(
    client: httpx.AsyncClient, database: Database
) -> None:
    landlord_id = uuid.uuid4()
    listing = _published_listing(landlord_id, price=150_000)
    await _store_and_project(database, [listing])

    response = await client.get("/properties/search")

    assert response.status_code == 200
    body = response.json()
    assert [item["property_id"] for item in body["items"]] == [str(listing.id)]
    assert body["next_cursor"] is None
    item = body["items"][0]
    assert item["price_xaf"] == 175_000
    assert item["city"] == "Douala"
    assert item["is_verified"] is True
    assert item["media_object_keys"] == [
        f"landlords/{landlord_id}/{listing.id}/1.jpg"
    ]


async def test_search_excludes_unverified_listings_by_default(
    client: httpx.AsyncClient, database: Database
) -> None:
    landlord_id = uuid.uuid4()
    verified = _published_listing(landlord_id, verified=True)
    unverified = _published_listing(landlord_id, verified=False)
    await _store_and_project(database, [verified, unverified])

    default = await client.get("/properties/search")
    assert [item["property_id"] for item in default.json()["items"]] == [str(verified.id)]

    inclusive = await client.get("/properties/search", params={"verified_only": "false"})
    returned = {item["property_id"] for item in inclusive.json()["items"]}
    assert returned == {str(verified.id), str(unverified.id)}


async def test_search_filters_by_city_and_price(
    client: httpx.AsyncClient, database: Database
) -> None:
    douala = _published_listing(uuid.uuid4(), city="Douala", price=100_000)
    yaounde = _published_listing(uuid.uuid4(), city="Yaounde", price=400_000)
    await _store_and_project(database, [douala, yaounde])

    by_city = await client.get("/properties/search", params={"city": "Douala"})
    assert [item["property_id"] for item in by_city.json()["items"]] == [str(douala.id)]

    by_price = await client.get("/properties/search", params={"min_price_xaf": 200_000})
    assert [item["property_id"] for item in by_price.json()["items"]] == [str(yaounde.id)]


async def test_search_paginates_and_marks_the_next_cursor(
    client: httpx.AsyncClient, database: Database
) -> None:
    landlord_id = uuid.uuid4()
    listings = [
        _published_listing(
            landlord_id,
            now=NOW + timedelta(minutes=index),
            price=100_000 * (index + 1),
        )
        for index in range(3)
    ]
    await _store_and_project(database, listings)

    first = await client.get("/properties/search", params={"limit": 2, "sort": "price_asc"})
    body = first.json()
    assert [item["price_xaf"] for item in body["items"]] == [125_000, 225_000]
    assert body["next_cursor"] is not None

    second = await client.get(
        "/properties/search",
        params={"limit": 2, "sort": "price_asc", "cursor": body["next_cursor"]},
    )
    assert [item["price_xaf"] for item in second.json()["items"]] == [325_000]
    assert second.json()["next_cursor"] is None