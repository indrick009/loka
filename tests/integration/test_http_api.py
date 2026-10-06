"""HTTP surface: authentication, authorisation and error translation.

Requests go through the real app, the real authenticator and the real use case,
so what is asserted here is what a client would experience.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from loka.bounded_contexts.identity.infrastructure.persistence.models import RefreshTokenRow
from loka.bounded_contexts.landlord.domain.entities.verification_request import VerificationStatus
from loka.bounded_contexts.landlord.infrastructure.persistence.models import LandlordProfileRow
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
from loka.interfaces.http.app import create_app
from loka.interfaces.http.auth import hash_token
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
TOKEN = "test-bearer-token-0123456789"


@pytest_asyncio.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http


async def _landlord(database: Database, *, verified: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    landlord_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with database.session() as session:
        session.add(
            LandlordProfileRow(
                id=landlord_id,
                user_id=user_id,
                display_name="Awa N.",
                verification_status=(
                    VerificationStatus.VERIFIED.value
                    if verified
                    else VerificationStatus.PENDING.value
                ),
                verified_at=NOW if verified else None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return user_id, landlord_id


async def _token_for(
    database: Database,
    user_id: uuid.UUID,
    *,
    revoked: bool = False,
    ttl: timedelta = timedelta(days=30),
) -> str:
    # Credentials are checked against the real clock, so they cannot use the
    # fixed NOW the property fixtures rely on.
    issued_at = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            RefreshTokenRow(
                id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_token(TOKEN),
                device_label="pytest",
                issued_at=issued_at,
                expires_at=issued_at + ttl,
                revoked_at=issued_at if revoked else None,
                is_admin_session=False,
            )
        )
    return TOKEN


def _complete_property(landlord_id: uuid.UUID, *, with_photo: bool = True) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=NOW)
    prop.set_type(PropertyType.APARTMENT, now=NOW)
    prop.set_location(Location(city="Douala", neighbourhood="Bali"), now=NOW)
    prop.set_rent(Money(150_000), now=NOW)
    prop.set_charges(Money(25_000), ChargingPolicy.EXTRA, now=NOW)
    prop.set_deposit(Money(300_000), now=NOW)
    prop.set_rooms(BedroomCount(2, 1), SurfaceArea(65), now=NOW)
    prop.set_availability(AvailabilityWindow(NOW.date() + timedelta(days=7)), now=NOW)
    prop.set_minimum_duration(Duration(6), now=NOW)
    prop.set_conditions("Refreshing", now=NOW)
    if with_photo:
        prop.add_media(
            PropertyMedia(
                media_id=uuid.uuid4(),
                object_key=f"landlords/{landlord_id}/1.jpg",
                kind=MediaKind.PHOTO,
                status=MediaStatus.UPLOADED,
            ),
            now=NOW,
        )
    return prop


async def _seed(database: Database, landlord_id: uuid.UUID, *, with_photo: bool = True) -> Property:
    prop = _complete_property(landlord_id, with_photo=with_photo)
    async with unit_of_work(database) as uow:
        await uow.repository("property").add(prop)
        await uow.commit()
    return prop


def _auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_publishing_over_http_returns_the_summary(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["property_id"] == str(prop.id)
    assert body["status"] == "AVAILABLE"
    assert body["public_price_xaf"] == 175_000
    assert response.headers["x-request-id"]

    async with database.session() as session:
        events = (
            await session.execute(
                select(OutboxRecord).where(OutboxRecord.aggregate_id == prop.id)
            )
        ).scalars()
        assert [event.event_type for event in events] == ["PropertyPublished"]


async def test_a_missing_token_is_unauthorized(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish", json={"confirmed_by_landlord": True}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_an_unknown_token_is_unauthorized(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth("not-a-real-token"),
    )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


async def test_a_revoked_token_is_unauthorized(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id, revoked=True)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 401


async def test_publishing_another_landlords_listing_is_forbidden(
    client: httpx.AsyncClient, database: Database
) -> None:
    _, landlord_id = await _landlord(database)
    intruder_user_id, _ = await _landlord(database)
    await _token_for(database, intruder_user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "authorization_denied"


async def test_without_landlord_confirmation_the_server_refuses(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": False},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "publication_blocked"


async def test_an_incomplete_listing_reports_what_is_missing(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id, with_photo=False)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "publication_blocked"
    assert "MISSING_PHOTOS" in body["context"]["missing"]


async def test_an_unverified_landlord_is_refused(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database, verified=False)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "publication_blocked"


async def test_an_unknown_property_is_not_found(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, _ = await _landlord(database)
    await _token_for(database, user_id)

    response = await client.post(
        f"/properties/{uuid.uuid4()}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


async def test_a_malformed_confirmation_body_is_rejected(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id)
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": "yes"},
        headers=_auth(),
    )

    assert response.status_code == 422


async def test_an_expired_token_is_unauthorized(
    client: httpx.AsyncClient, database: Database
) -> None:
    user_id, landlord_id = await _landlord(database)
    await _token_for(database, user_id, ttl=timedelta(seconds=-1))
    prop = await _seed(database, landlord_id)

    response = await client.post(
        f"/properties/{prop.id}/publish",
        json={"confirmed_by_landlord": True},
        headers=_auth(),
    )

    assert response.status_code == 401
