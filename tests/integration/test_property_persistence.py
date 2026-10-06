"""Property write model + search read model against real PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    PropertyStatus,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertyMediaRow,
    PropertyRow,
)
from loka.bounded_contexts.property.infrastructure.persistence.property_quota import (
    SqlAlchemyPropertyQuota,
)
from loka.bounded_contexts.property.infrastructure.persistence.property_repository import (
    SqlAlchemyPropertyRepository,
)
from loka.bounded_contexts.property.infrastructure.persistence.property_search_repository import (
    PropertySearchCriteria,
    PropertySearchRepository,
)
from loka.shared.domain.errors import ConcurrencyConflict

pytestmark = pytest.mark.integration


def _listing(landlord_id: uuid.UUID, *, now: datetime, price: int = 150_000) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=now)
    prop.set_type(PropertyType.APARTMENT, now=now)
    prop.set_location(Location(city="Douala", neighbourhood="Bali"), now=now)
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
    return prop


async def test_add_and_reload_round_trips_the_aggregate(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    prop = _listing(landlord_id, now=now)

    repository = SqlAlchemyPropertyRepository(session)
    await repository.add(prop)
    await session.commit()

    loaded = await repository.get(prop.id)
    assert loaded is not None
    assert loaded.id == prop.id
    assert loaded.landlord_id == landlord_id
    assert loaded.status is PropertyStatus.DRAFT
    assert loaded.rent == Money(150_000)
    assert loaded.charges == Money(25_000)
    assert loaded.charging_policy is ChargingPolicy.EXTRA
    assert loaded.bedrooms == BedroomCount(2, 1)
    assert loaded.surface_area == SurfaceArea(65)
    assert loaded.location.city == "Douala"
    assert loaded.location.neighbourhood == "Bali"
    assert [media.object_key for media in loaded.media] == [
        f"landlords/{landlord_id}/{prop.id}/1.jpg"
    ]
    assert loaded.completeness_score() == prop.completeness_score()


async def test_save_replaces_media_and_keeps_version(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    prop = _listing(landlord_id, now=now)
    repository = SqlAlchemyPropertyRepository(session)
    await repository.add(prop)
    await session.commit()

    prop.remove_media(prop.media[0].id, now=now)
    prop.add_media(
        PropertyMedia(
            media_id=uuid.uuid4(),
            object_key=f"landlords/{landlord_id}/{prop.id}/2.jpg",
            kind=MediaKind.PHOTO,
            status=MediaStatus.UPLOADED,
            checksum="def456",
        ),
        now=now,
    )
    await repository.save(prop)
    await session.commit()

    loaded = await repository.get(prop.id)
    assert loaded is not None
    assert [media.object_key.rsplit("/", 1)[-1] for media in loaded.media] == ["2.jpg"]

    media_count = await session.execute(
        select(func.count())
        .select_from(PropertyMediaRow)
        .where(PropertyMediaRow.property_id == prop.id)
    )
    assert media_count.scalar_one() == 1


async def test_save_rejects_a_stale_version(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    prop = _listing(landlord_id, now=now)
    repository = SqlAlchemyPropertyRepository(session)
    await repository.add(prop)
    await session.commit()

    other = await repository.get(prop.id)
    assert other is not None
    other.set_landlord_rules("No pets", now=now)
    await repository.save(other)
    await session.commit()

    stale = await repository.get(prop.id)
    assert stale is not None
    stale.set_landlord_rules("No parties", now=now)
    with pytest.raises(ConcurrencyConflict):
        await repository.save(stale, expected_version=1)

    await session.rollback()
    reloaded = await repository.get(prop.id)
    assert reloaded is not None
    assert reloaded.landlord_rules == "No pets"


async def test_quota_counts_only_active_statuses(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    quota = SqlAlchemyPropertyQuota(session)
    assert await quota.active_count(landlord_id) == 0

    drafts = [_listing(landlord_id, now=now) for _ in range(3)]
    repository = SqlAlchemyPropertyRepository(session)
    for prop in drafts:
        await repository.add(prop)
    await session.commit()
    assert await quota.active_count(landlord_id) == 3

    drafts[0].publish(now=now, actor_id=landlord_id)
    drafts[1].publish(now=now, actor_id=landlord_id)
    drafts[1].reserve(now=now, actor_id=landlord_id)
    drafts[2].publish(now=now, actor_id=landlord_id)
    drafts[2].archive(now=now)
    for prop in drafts:
        await repository.save(prop)
    await session.commit()

    assert await quota.active_count(landlord_id) == 2


async def test_search_paginates_with_a_stable_cursor(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    repository = SqlAlchemyPropertyRepository(session)
    listing_ids: list[uuid.UUID] = []
    for index in range(5):
        created = now + timedelta(minutes=index)
        prop = _listing(landlord_id, now=created, price=100_000 * (index + 1))
        prop.publish(now=created, actor_id=landlord_id)
        prop.mark_verified(now=created)
        await repository.add(prop)
        listing_ids.append(prop.id)
    await session.commit()

    search = PropertySearchRepository(session)
    criteria = PropertySearchCriteria(city="Douala", sort="price_asc", verified_only=True)

    first = await search.search(criteria, limit=2)
    assert [item["price_xaf"] for item in first.items] == [125_000, 225_000]
    assert first.next_cursor is not None
    assert first.items[0]["media_object_keys"] == [
        f"landlords/{landlord_id}/{first.items[0]['property_id']}/1.jpg"
    ]

    second = await search.search(criteria, cursor=first.next_cursor, limit=2)
    assert [item["price_xaf"] for item in second.items] == [325_000, 425_000]

    third = await search.search(criteria, cursor=second.next_cursor, limit=2)
    assert [item["price_xaf"] for item in third.items] == [525_000]
    assert third.next_cursor is None

    seen = [item["property_id"] for page in (first, second, third) for item in page.items]
    assert len(set(seen)) == 5
    assert set(seen) == {str(pid) for pid in listing_ids}
    assert await search.count(criteria) == 5


async def test_search_descending_sort_and_filters(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    repository = SqlAlchemyPropertyRepository(session)
    for index, price in enumerate((120_000, 90_000, 300_000)):
        prop = _listing(landlord_id, now=now + timedelta(minutes=index), price=price)
        prop.publish(now=now + timedelta(minutes=index), actor_id=landlord_id)
        if index != 1:
            prop.mark_verified(now=now + timedelta(minutes=index))
        await repository.add(prop)
    await session.commit()

    search = PropertySearchRepository(session)
    verified = await search.search(
        PropertySearchCriteria(city="Douala", verified_only=True, sort="price_desc")
    )
    assert [item["price_xaf"] for item in verified.items] == [325_000, 145_000]

    everything = await search.search(
        PropertySearchCriteria(city="Douala", verified_only=False, sort="price_desc")
    )
    assert [item["price_xaf"] for item in everything.items] == [325_000, 145_000, 115_000]

    # Price filters apply to the public price (rent + extra charges), not the rent.
    filtered = await search.search(
        PropertySearchCriteria(
            city="Douala", verified_only=False, min_price_xaf=100_000, min_bedrooms=2
        )
    )
    assert [item["price_xaf"] for item in filtered.items] == [325_000, 115_000, 145_000]

    expensive_only = await search.search(
        PropertySearchCriteria(city="Douala", verified_only=False, min_price_xaf=150_000)
    )
    assert [item["price_xaf"] for item in expensive_only.items] == [325_000]


async def test_search_excludes_archived_and_other_cities(session: AsyncSession) -> None:
    landlord_id = uuid.uuid4()
    now = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
    repository = SqlAlchemyPropertyRepository(session)

    archived = _listing(landlord_id, now=now)
    archived.publish(now=now, actor_id=landlord_id)
    archived.mark_verified(now=now)
    archived.archive(now=now)

    yaounde = _listing(landlord_id, now=now)
    yaounde.set_location(Location(city="Yaounde", neighbourhood="Bastos"), now=now)
    yaounde.publish(now=now, actor_id=landlord_id)
    yaounde.mark_verified(now=now)

    for prop in (archived, yaounde):
        await repository.add(prop)
    await session.commit()

    rows = await session.execute(
        select(PropertyRow.status).where(PropertyRow.id.in_([archived.id, yaounde.id]))
    )
    assert set(rows.scalars()) == {PropertyStatus.ARCHIVED.value, PropertyStatus.AVAILABLE.value}

    search = PropertySearchRepository(session)
    result = await search.search(PropertySearchCriteria(city="Douala"))
    assert result.items == []
