"""Publish property: business rules, quota, landlord verification, events."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyCommand,
    PublishPropertyUseCase,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.exceptions import PublicationBlocked
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    Duration,
    PropertyStatus,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound


class FakePropertyRepository:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, Property] = {}
        self.saved: list[Property] = []

    async def get(self, property_id: uuid.UUID) -> Property | None:
        return self.store.get(property_id)

    async def add(self, prop: Property) -> None:
        self.store[prop.id] = prop

    async def save(self, prop: Property, *, expected_version: int | None = None) -> None:
        self.store[prop.id] = prop
        self.saved.append(prop)


class FakeQuota:
    def __init__(self, active: int = 0) -> None:
        self._active = active

    async def active_count(
        self, landlord_id: uuid.UUID, *, exclude_property_id: uuid.UUID | None = None
    ) -> int:
        # A draft already counts towards the catalogue in the database; the
        # adapter subtracts it so a listing never blocks itself.
        return self._active - 1 if exclude_property_id is not None else self._active


class FakeLandlords:
    def __init__(
        self,
        verified: bool = True,
        *,
        profile_id: uuid.UUID | None = None,
        caller_user_id: uuid.UUID | None = None,
    ) -> None:
        self._verified = verified
        self._profile_id = profile_id
        self._caller_user_id = caller_user_id

    async def is_verified(self, landlord_id: uuid.UUID) -> bool:
        return self._verified

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        if self._caller_user_id is not None and user_id != self._caller_user_id:
            return None
        return self._profile_id


class FakeUnitOfWork(UnitOfWork):
    def __init__(self, properties: FakePropertyRepository, quota: FakeQuota) -> None:
        self.properties = properties
        self.quota = quota
        self.collected: list[object] = []
        self.commits = 0

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    @property
    def events(self) -> object:
        return None

    def collect(self, aggregate: object) -> None:
        self.collected.append(aggregate)

    def repository(self, name: str) -> object:
        if name == "property":
            return self.properties
        if name == "property_quota":
            return self.quota
        raise LookupError(name)


NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def complete_property(landlord_id: uuid.UUID) -> Property:
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
    return prop


def _use_case(
    landlord_id: uuid.UUID, *, verified: bool = True, active: int = 0, limit: int = 25
) -> tuple[FakeUnitOfWork, PublishPropertyUseCase]:
    properties = FakePropertyRepository()
    uow = FakeUnitOfWork(properties, FakeQuota(active))
    return uow, PublishPropertyUseCase(
        uow,
        landlords=FakeLandlords(verified, profile_id=landlord_id),
        max_active_properties=limit,
    )


@pytest.mark.asyncio
async def test_publish_moves_draft_to_available_and_emits_event() -> None:
    landlord_id = uuid.uuid4()
    prop = complete_property(landlord_id)
    uow, use_case = _use_case(landlord_id)
    await uow.properties.add(prop)

    summary = await use_case.execute(
        PublishPropertyCommand(
            property_id=prop.id, actor_id=landlord_id, confirmed_by_landlord=True
        ),
        now=NOW,
    )

    assert prop.status is PropertyStatus.AVAILABLE
    assert summary.status == "AVAILABLE"
    assert summary.public_price_xaf == 175_000  # rent + extra charges
    assert uow.commits == 1
    assert uow.collected == [prop]
    assert [event.event_type for event in prop.pull_events()] == ["PropertyPublished"]


@pytest.mark.asyncio
async def test_publish_requires_explicit_landlord_confirmation() -> None:
    landlord_id = uuid.uuid4()
    prop = complete_property(landlord_id)
    uow, use_case = _use_case(landlord_id)
    await uow.properties.add(prop)

    with pytest.raises(PublicationBlocked):
        await use_case.execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=landlord_id, confirmed_by_landlord=False
            ),
            now=NOW,
        )

    assert prop.status is PropertyStatus.DRAFT
    assert uow.commits == 0


@pytest.mark.asyncio
async def test_publish_blocked_when_landlord_not_verified() -> None:
    landlord_id = uuid.uuid4()
    prop = complete_property(landlord_id)
    uow, use_case = _use_case(landlord_id, verified=False)
    await uow.properties.add(prop)

    with pytest.raises(PublicationBlocked):
        await use_case.execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=landlord_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )

    assert prop.status is PropertyStatus.DRAFT


@pytest.mark.asyncio
async def test_publish_blocked_when_quota_reached() -> None:
    landlord_id = uuid.uuid4()
    prop = complete_property(landlord_id)
    # 26 active rows in total: the draft being published plus the 25 that would
    # remain, which is one too many.
    uow, use_case = _use_case(landlord_id, active=26, limit=25)
    await uow.properties.add(prop)

    with pytest.raises(PublicationBlocked):
        await use_case.execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=landlord_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_publish_at_the_quota_limit_ignores_the_draft_being_published() -> None:
    """A draft counts towards the catalogue but must not block itself."""
    landlord_id = uuid.uuid4()
    prop = complete_property(landlord_id)
    uow, use_case = _use_case(landlord_id, active=25, limit=25)
    await uow.properties.add(prop)

    summary = await use_case.execute(
        PublishPropertyCommand(
            property_id=prop.id, actor_id=landlord_id, confirmed_by_landlord=True
        ),
        now=NOW,
    )

    assert summary.status == "AVAILABLE"


@pytest.mark.asyncio
async def test_publish_unknown_property_raises_not_found() -> None:
    _, use_case = _use_case(uuid.uuid4())
    with pytest.raises(ResourceNotFound):
        await use_case.execute(
            PublishPropertyCommand(
                property_id=uuid.uuid4(), actor_id=uuid.uuid4(), confirmed_by_landlord=True
            ),
            now=NOW,
        )


@pytest.mark.asyncio
async def test_publish_blocked_when_data_quality_issue_raised() -> None:
    """A draft with a critical missing field must not reach the catalogue."""
    landlord_id = uuid.uuid4()
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=NOW)
    prop.set_type(PropertyType.APARTMENT, now=NOW)
    prop.set_location(Location(city="Douala", neighbourhood="Bali"), now=NOW)
    prop.set_rent(Money(150_000), now=NOW)
    uow, use_case = _use_case(landlord_id)
    await uow.properties.add(prop)

    with pytest.raises(Exception) as excinfo:
        await use_case.execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=landlord_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )
    assert prop.status is PropertyStatus.DRAFT
    assert excinfo.value is not None
