"""Rental and visit endpoints over HTTP.

Goes through the real app, bearer authenticator and use cases. The purpose is
to pin the contract: the actor is the authenticated principal, the property
must be published to receive interest, and the same access rules apply as on
the domain path.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from loka.bounded_contexts.identity.infrastructure.persistence.models import (
    RefreshTokenRow,
)
from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationStatus,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
)
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

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def client(integration_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(integration_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as http:
            yield http


async def _issue_token(database: Database, user_id: uuid.UUID, value: str) -> None:
    issued_at = datetime.now(UTC)
    async with database.session() as session:
        session.add(
            RefreshTokenRow(
                id=uuid.uuid4(),
                user_id=user_id,
                token_hash=hash_token(value),
                device_label="pytest",
                issued_at=issued_at,
                expires_at=issued_at + timedelta(days=30),
                revoked_at=None,
                is_admin_session=False,
            )
        )


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _landlord_with_property(database: Database) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a verified landlord and a published listing; return ids."""
    landlord_user = uuid.uuid4()
    landlord_id = uuid.uuid4()
    async with database.session() as session:
        session.add(
            LandlordProfileRow(
                id=landlord_id,
                user_id=landlord_user,
                display_name="Awa N.",
                verification_status=VerificationStatus.VERIFIED.value,
                verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )

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
    prop.add_media(
        PropertyMedia(
            media_id=uuid.uuid4(),
            object_key=f"landlords/{landlord_id}/1.jpg",
            kind=MediaKind.PHOTO,
            status=MediaStatus.UPLOADED,
        ),
        now=NOW,
    )
    async with unit_of_work(database) as uow:
        await uow.repository("property").add(prop)
        await uow.commit()
    async with unit_of_work(database) as uow:
        from loka.bounded_contexts.property.application.use_cases.publish_property import (
            PublishPropertyCommand,
        )
        from loka.shared.infrastructure.composition import publish_property_use_case

        await publish_property_use_case(uow).execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=landlord_user, confirmed_by_landlord=True
            ),
            now=NOW,
        )
    return landlord_user, prop.id


async def test_a_rental_journey_over_http(
    client: httpx.AsyncClient, database: Database
) -> None:
    landlord_user, property_id = await _landlord_with_property(database)
    tenant_id = uuid.uuid4()
    tenant_token = "rental-tenant-token-0123456789"
    landlord_token = "rental-landlord-token-0123456789"
    await _issue_token(database, tenant_id, tenant_token)
    await _issue_token(database, landlord_user, landlord_token)

    created = await client.post(
        "/rentals/applications",
        json={"property_id": str(property_id), "message": "Bonjour"},
        headers=_auth(tenant_token),
    )
    assert created.status_code == 201, created.text
    application_id = created.json()["application_id"]
    assert created.json()["status"] == "INTERESTED"

    offered = await client.post(
        f"/rentals/applications/{application_id}/offers",
        json={"rent_xaf": 150_000, "deposit_xaf": 300_000},
        headers=_auth(landlord_token),
    )
    assert offered.status_code == 200, offered.text
    assert offered.json()["status"] == "NEGOTIATING"

    decided = await client.post(
        f"/rentals/applications/{application_id}/decision",
        json={"decision": "ACCEPT"},
        headers=_auth(landlord_token),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "ACCEPTED"

    confirmed = await client.post(
        f"/rentals/applications/{application_id}/confirm",
        headers=_auth(landlord_token),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["status"] == "CONFIRMED"

    fetched = await client.get(
        f"/rentals/applications/{application_id}", headers=_auth(tenant_token)
    )
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "CONFIRMED"


async def test_a_tenant_cannot_decide_over_http(
    client: httpx.AsyncClient, database: Database
) -> None:
    landlord_user, property_id = await _landlord_with_property(database)
    tenant_id = uuid.uuid4()
    tenant_token = "rental-tenant-token-9876543210"
    landlord_token = "rental-landlord-token-9876543210"
    await _issue_token(database, tenant_id, tenant_token)
    await _issue_token(database, landlord_user, landlord_token)

    created = await client.post(
        "/rentals/applications",
        json={"property_id": str(property_id)},
        headers=_auth(tenant_token),
    )
    application_id = created.json()["application_id"]

    denied = await client.post(
        f"/rentals/applications/{application_id}/decision",
        json={"decision": "ACCEPT"},
        headers=_auth(tenant_token),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "authorization_denied"


async def test_the_visit_journey_over_http(
    client: httpx.AsyncClient, database: Database
) -> None:
    landlord_user, property_id = await _landlord_with_property(database)
    tenant_id = uuid.uuid4()
    tenant_token = "visit-tenant-token-0123456789"
    landlord_token = "visit-landlord-token-0123456789"
    await _issue_token(database, tenant_id, tenant_token)
    await _issue_token(database, landlord_user, landlord_token)

    requested = await client.post(
        "/visits",
        json={"property_id": str(property_id), "preferred_date": NOW.date().isoformat()},
        headers=_auth(tenant_token),
    )
    assert requested.status_code == 201, requested.text
    visit_id = requested.json()["visit_id"]
    assert requested.json()["status"] == "REQUESTED"

    when = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    scheduled = await client.post(
        f"/visits/{visit_id}/schedule",
        json={"scheduled_for": when},
        headers=_auth(landlord_token),
    )
    assert scheduled.status_code == 200, scheduled.text
    assert scheduled.json()["status"] == "SCHEDULED"

    completed = await client.post(
        f"/visits/{visit_id}/complete",
        json={"attended": True},
        headers=_auth(landlord_token),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "COMPLETED"

    fetched = await client.get(f"/visits/{visit_id}", headers=_auth(tenant_token))
    assert fetched.status_code == 200
    assert fetched.json()["visit_id"] == visit_id