"""Rental and visit flows through the real composition root.

These exercise the Phase 7 loop end to end: a tenant expresses interest, the
parties negotiate, the landlord accepts (reserving the listing), confirms the
rental (marking it rented), and a visit is requested, scheduled and completed.
Everything runs through the production unit of work, so the rental aggregate,
the property aggregate and their outbox events share one transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from loka.bounded_contexts.landlord.domain.entities.verification_request import (
    VerificationStatus,
)
from loka.bounded_contexts.landlord.infrastructure.persistence.models import (
    LandlordProfileRow,
)
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
from loka.bounded_contexts.property.infrastructure.persistence.models import PropertyRow
from loka.bounded_contexts.rental.application.use_cases.confirm_rental import (
    ConfirmRentalCommand,
)
from loka.bounded_contexts.rental.application.use_cases.decide_rental_application import (
    DecideRentalApplicationCommand,
    RentalDecision,
)
from loka.bounded_contexts.rental.application.use_cases.express_rental_interest import (
    ExpressRentalInterestCommand,
)
from loka.bounded_contexts.rental.application.use_cases.get_rental_application import (
    GetRentalApplicationQuery,
)
from loka.bounded_contexts.rental.application.use_cases.propose_rental_terms import (
    ProposeRentalTermsCommand,
)
from loka.bounded_contexts.rental.application.use_cases.withdraw_rental_application import (
    WithdrawRentalApplicationCommand,
)
from loka.bounded_contexts.visit.application.use_cases.complete_visit import (
    CompleteVisitCommand,
)
from loka.bounded_contexts.visit.application.use_cases.get_visit import GetVisitQuery
from loka.bounded_contexts.visit.application.use_cases.request_visit import (
    RequestVisitCommand,
)
from loka.bounded_contexts.visit.application.use_cases.schedule_visit import (
    ScheduleVisitCommand,
)
from loka.shared.domain.errors import AuthorizationDenied
from loka.shared.infrastructure.composition import (
    complete_visit_use_case,
    confirm_rental_use_case,
    decide_rental_application_use_case,
    express_rental_interest_use_case,
    get_rental_application_use_case,
    get_visit_use_case,
    propose_rental_terms_use_case,
    publish_property_use_case,
    request_visit_use_case,
    schedule_visit_use_case,
    unit_of_work,
    withdraw_rental_application_use_case,
)
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


async def _landlord(database: Database) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (user_id, landlord_profile_id) for a verified landlord."""
    user_id = uuid.uuid4()
    landlord_id = uuid.uuid4()
    async with database.session() as session:
        session.add(
            LandlordProfileRow(
                id=landlord_id,
                user_id=user_id,
                display_name="Awa N.",
                verification_status=VerificationStatus.VERIFIED.value,
                verified_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    return user_id, landlord_id


def _complete_property(landlord_id: uuid.UUID) -> Property:
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


async def _published_property(
    database: Database, landlord_user_id: uuid.UUID, landlord_id: uuid.UUID
) -> Property:
    prop = _complete_property(landlord_id)
    async with unit_of_work(database) as uow:
        await uow.repository("property").add(prop)
        await uow.commit()
    async with unit_of_work(database) as uow:
        use_case: PublishPropertyUseCase = publish_property_use_case(uow)
        await use_case.execute(
            PublishPropertyCommand(
                property_id=prop.id, actor_id=landlord_user_id, confirmed_by_landlord=True
            ),
            now=NOW,
        )
    return prop


async def _property_status(database: Database, property_id: uuid.UUID) -> str:
    async with database.session() as session:
        row = await session.get(PropertyRow, property_id)
        assert row is not None
        return row.status


async def _events(database: Database, aggregate_id: uuid.UUID) -> list[str]:
    async with database.session() as session:
        rows = (
            await session.execute(
                select(OutboxRecord.event_type).where(
                    OutboxRecord.aggregate_id == aggregate_id
                )
            )
        ).scalars()
        return list(rows)


async def test_the_full_rental_journey(database: Database) -> None:
    landlord_user, landlord_id = await _landlord(database)
    prop = await _published_property(database, landlord_user, landlord_id)
    tenant_id = uuid.uuid4()

    async with unit_of_work(database) as uow:
        application = await express_rental_interest_use_case(uow).execute(
            ExpressRentalInterestCommand(
                property_id=prop.id, tenant_id=tenant_id, message="Bonjour"
            ),
            now=NOW,
        )
    assert application.status == "INTERESTED"
    application_id = uuid.UUID(application.application_id)

    async with unit_of_work(database) as uow:
        negotiated = await propose_rental_terms_use_case(uow).execute(
            ProposeRentalTermsCommand(
                application_id=application_id,
                actor_id=landlord_user,
                rent=Money(150_000),
                deposit=Money(300_000),
            ),
            now=NOW,
        )
    assert negotiated.status == "NEGOTIATING"
    assert negotiated.proposed_rent_xaf == 150_000

    async with unit_of_work(database) as uow:
        decided = await decide_rental_application_use_case(uow).execute(
            DecideRentalApplicationCommand(
                application_id=application_id,
                actor_id=landlord_user,
                decision=RentalDecision.ACCEPT,
            ),
            now=NOW,
        )
    assert decided.status == "ACCEPTED"
    assert await _property_status(database, prop.id) == "RESERVED"

    async with unit_of_work(database) as uow:
        confirmed = await confirm_rental_use_case(uow).execute(
            ConfirmRentalCommand(application_id=application_id, actor_id=landlord_user),
            now=NOW,
        )
    assert confirmed.status == "CONFIRMED"
    assert await _property_status(database, prop.id) == "RENTED"

    application_events = await _events(database, application_id)
    assert "RentalApplicationCreated" in application_events
    assert "RentalApplicationAccepted" in application_events
    assert "RentalConfirmed" in application_events
    assert "PropertyRented" in await _events(database, prop.id)


async def test_a_tenant_can_withdraw_an_accepted_application_and_release_the_property(
    database: Database,
) -> None:
    landlord_user, landlord_id = await _landlord(database)
    prop = await _published_property(database, landlord_user, landlord_id)
    tenant_id = uuid.uuid4()

    async with unit_of_work(database) as uow:
        application = await express_rental_interest_use_case(uow).execute(
            ExpressRentalInterestCommand(property_id=prop.id, tenant_id=tenant_id), now=NOW
        )
    application_id = uuid.UUID(application.application_id)

    async with unit_of_work(database) as uow:
        await decide_rental_application_use_case(uow).execute(
            DecideRentalApplicationCommand(
                application_id=application_id,
                actor_id=landlord_user,
                decision=RentalDecision.ACCEPT,
            ),
            now=NOW,
        )
    assert await _property_status(database, prop.id) == "RESERVED"

    async with unit_of_work(database) as uow:
        withdrawn = await withdraw_rental_application_use_case(uow).execute(
            WithdrawRentalApplicationCommand(
                application_id=application_id, actor_id=tenant_id
            ),
            now=NOW,
        )
    assert withdrawn.status == "WITHDRAWN"
    assert await _property_status(database, prop.id) == "AVAILABLE"


async def test_a_tenant_cannot_decide_on_their_own_application(database: Database) -> None:
    landlord_user, landlord_id = await _landlord(database)
    prop = await _published_property(database, landlord_user, landlord_id)
    tenant_id = uuid.uuid4()

    async with unit_of_work(database) as uow:
        application = await express_rental_interest_use_case(uow).execute(
            ExpressRentalInterestCommand(property_id=prop.id, tenant_id=tenant_id), now=NOW
        )
    application_id = uuid.UUID(application.application_id)

    async with unit_of_work(database) as uow:
        with pytest.raises(AuthorizationDenied):
            await decide_rental_application_use_case(uow).execute(
                DecideRentalApplicationCommand(
                    application_id=application_id,
                    actor_id=tenant_id,
                    decision=RentalDecision.ACCEPT,
                ),
                now=NOW,
            )


async def test_an_application_can_be_read_back_by_a_party(database: Database) -> None:
    landlord_user, landlord_id = await _landlord(database)
    prop = await _published_property(database, landlord_user, landlord_id)
    tenant_id = uuid.uuid4()

    async with unit_of_work(database) as uow:
        application = await express_rental_interest_use_case(uow).execute(
            ExpressRentalInterestCommand(property_id=prop.id, tenant_id=tenant_id), now=NOW
        )
    application_id = uuid.UUID(application.application_id)

    async with unit_of_work(database) as uow:
        fetched = await get_rental_application_use_case(uow).execute(
            GetRentalApplicationQuery(
                application_id=application_id, requester_id=tenant_id
            )
        )
    assert fetched.application_id == str(application_id)


async def test_the_visit_journey(database: Database) -> None:
    landlord_user, landlord_id = await _landlord(database)
    prop = await _published_property(database, landlord_user, landlord_id)
    tenant_id = uuid.uuid4()

    async with unit_of_work(database) as uow:
        requested = await request_visit_use_case(uow).execute(
            RequestVisitCommand(
                property_id=prop.id, tenant_id=tenant_id, preferred_date=NOW.date()
            ),
            now=NOW,
        )
    assert requested.status == "REQUESTED"
    visit_id = uuid.UUID(requested.visit_id)

    scheduled_for = NOW + timedelta(days=1)
    async with unit_of_work(database) as uow:
        scheduled = await schedule_visit_use_case(uow).execute(
            ScheduleVisitCommand(
                visit_id=visit_id, actor_id=landlord_user, scheduled_for=scheduled_for
            ),
            now=NOW,
        )
    assert scheduled.status == "SCHEDULED"

    async with unit_of_work(database) as uow:
        completed = await complete_visit_use_case(uow).execute(
            CompleteVisitCommand(
                visit_id=visit_id, actor_id=landlord_user, attended=True
            ),
            now=scheduled_for + timedelta(hours=1),
        )
    assert completed.status == "COMPLETED"
    assert "VisitCompleted" in await _events(database, visit_id)

    async with unit_of_work(database) as uow:
        fetched = await get_visit_use_case(uow).execute(
            GetVisitQuery(visit_id=visit_id, requester_id=tenant_id)
        )
    assert fetched.visit_id == str(visit_id)