"""Publication flow through the real composition root.

Everything here is production wiring: the unit of work resolves the SQLAlchemy
adapters, the quota and the verification lookup share one transaction, and the
resulting event lands in the outbox of that same transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from loka.bounded_contexts.landlord.domain.entities.verification_request import VerificationStatus
from loka.bounded_contexts.landlord.infrastructure.persistence.models import LandlordProfileRow
from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyCommand,
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
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.property_repository import (
    SqlAlchemyPropertyRepository,
)
from loka.shared.domain.errors import AuthorizationDenied, ResourceNotFound
from loka.shared.infrastructure.composition import (
    publish_property_use_case,
    unit_of_work,
)
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


async def _landlord(database: Database, *, verified: bool = True) -> tuple[uuid.UUID, uuid.UUID]:
    """Return the authenticated user id and the landlord profile id.

    They are different identifiers: the API authenticates a user, the listing
    belongs to a landlord profile, and only the directory links the two.
    """
    landlord_id = uuid.uuid4()
    user_id = uuid.uuid4()
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


async def _store_draft(database: Database, prop: Property) -> None:
    async with unit_of_work(database) as uow:
        await uow.repository("property").add(prop)
        await uow.commit()


async def test_publishing_a_complete_listing_commits_state_and_event(
    database: Database,
) -> None:
    user_id, landlord_id = await _landlord(database)
    prop = _complete_property(landlord_id)
    await _store_draft(database, prop)

    async with unit_of_work(database) as uow:
        summary = await publish_property_use_case(uow).execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=user_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )

    assert summary.status == "AVAILABLE"
    assert summary.public_price_xaf == 175_000

    async with database.session() as session:
        events = (
            await session.execute(
                select(OutboxRecord).where(OutboxRecord.aggregate_id == prop.id)
            )
        ).scalars()
        assert [event.event_type for event in events] == ["PropertyPublished"]
        assert all(event.correlation_id for event in events)


async def test_a_second_save_of_the_same_aggregate_does_not_conflict(
    database: Database,
) -> None:
    """Regression: the repository must move the baseline version forward."""
    user_id, landlord_id = await _landlord(database)
    prop = _complete_property(landlord_id)
    await _store_draft(database, prop)
    version_after_add = prop.persisted_version

    async with unit_of_work(database) as uow:
        await publish_property_use_case(uow).execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=user_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )

    async with database.session() as session:
        repository = SqlAlchemyPropertyRepository(session)
        reloaded = await repository.get(prop.id)
        assert reloaded is not None
        assert reloaded.version > version_after_add
        assert reloaded.persisted_version == reloaded.version
        assert reloaded.status.value == "AVAILABLE"
        assert reloaded.published_at == NOW

        # A second save of a freshly loaded aggregate must not conflict with
        # itself: the write above moved the baseline version forward.
        await repository.save(reloaded)
        await session.commit()


async def test_another_actor_cannot_publish_someone_elses_listing(
    database: Database,
) -> None:
    _, landlord_id = await _landlord(database)
    prop = _complete_property(landlord_id)
    await _store_draft(database, prop)

    async with unit_of_work(database) as uow:
        with pytest.raises(AuthorizationDenied):
            await publish_property_use_case(uow).execute(
                PublishPropertyCommand(
                    property_id=prop.id,
                    actor_id=uuid.uuid4(),
                    confirmed_by_landlord=True,
                ),
                now=NOW,
            )


async def test_a_user_owning_another_profile_cannot_publish(
    database: Database,
) -> None:
    """A real caller authenticated as a user, but not this listing's landlord."""
    _, landlord_id = await _landlord(database)
    intruder_user_id, _ = await _landlord(database)
    prop = _complete_property(landlord_id)
    await _store_draft(database, prop)

    async with unit_of_work(database) as uow:
        with pytest.raises(AuthorizationDenied):
            await publish_property_use_case(uow).execute(
                PublishPropertyCommand(
                    property_id=prop.id,
                    actor_id=intruder_user_id,
                    confirmed_by_landlord=True,
                ),
                now=NOW,
            )


async def test_an_unverified_landlord_cannot_publish(database: Database) -> None:
    user_id, landlord_id = await _landlord(database, verified=False)
    prop = _complete_property(landlord_id)
    await _store_draft(database, prop)

    async with unit_of_work(database) as uow:
        with pytest.raises(PublicationBlocked):
            await publish_property_use_case(uow).execute(
                PublishPropertyCommand(
                    property_id=prop.id, actor_id=user_id, confirmed_by_landlord=True
                ),
                now=NOW,
            )


async def test_a_blocked_publication_leaves_no_event_and_no_version_bump(
    database: Database,
) -> None:
    user_id, landlord_id = await _landlord(database)
    prop = _complete_property(landlord_id, with_photo=False)
    await _store_draft(database, prop)
    version_before = prop.version

    async with unit_of_work(database) as uow:
        with pytest.raises(PublicationBlocked):
            await publish_property_use_case(uow).execute(
                PublishPropertyCommand(
                    property_id=prop.id, actor_id=user_id, confirmed_by_landlord=True
                ),
                now=NOW,
            )

    async with database.session() as session:
        events = (
            await session.execute(
                select(OutboxRecord).where(OutboxRecord.aggregate_id == prop.id)
            )
        ).scalars()
        assert list(events) == []

    async with unit_of_work(database) as uow:
        reloaded = await uow.repository("property").get(prop.id)
        assert reloaded is not None
        assert reloaded.version == version_before, (
            "a rolled back publication must not bump the version"
        )


async def test_publishing_an_unknown_property_is_not_found(database: Database) -> None:
    async with unit_of_work(database) as uow:
        with pytest.raises(ResourceNotFound):
            await publish_property_use_case(uow).execute(
                PublishPropertyCommand(
                    property_id=uuid.uuid4(),
                    actor_id=uuid.uuid4(),
                    confirmed_by_landlord=True,
                ),
                now=NOW,
            )
