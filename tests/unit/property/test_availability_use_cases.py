"""Asking the landlord, and what his answer releases.

Requesting is idempotent per listing: two tenants interested in the same
lodging join one question instead of sending the landlord the same message
twice. Answering is deliberately not — the first answer already moved the
listing, and replaying it would cost a lodging the landlord never let go.

The payment gate is the reason all of this exists. Taking 1 000 FCFA for a
contact that leads to an already-rented room is the single failure the
availability check is there to prevent (§9).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from loka.bounded_contexts.property.application.use_cases.check_availability import (
    AnswerAvailabilityCheckCommand,
    AnswerAvailabilityCheckUseCase,
    ListingAvailabilityReader,
    RequestAvailabilityCheckCommand,
    RequestAvailabilityCheckUseCase,
)
from loka.bounded_contexts.property.domain.entities.availability_check import (
    AvailabilityCheck,
    CheckStatus,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    ChargingPolicy,
    PropertyStanding,
    PropertyStatus,
    PropertyType,
    RentMonths,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=3)


def a_listing(*, lodgings: int = 1) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)
    prop.set_type(PropertyType.STUDIO, now=NOW)
    prop.set_standing(PropertyStanding.MODERN, now=NOW)
    prop.set_quantity(lodgings, now=NOW)
    prop.set_location(Location(city="Douala", neighbourhood="Bonapriso"), now=NOW)
    prop.set_rent(Money(60_000), now=NOW)
    prop.set_charges(Money(0), ChargingPolicy.INCLUDED, now=NOW)
    prop.set_deposit_months(RentMonths(2), now=NOW)
    prop.set_advance_months(RentMonths(6), now=NOW)
    prop.status = PropertyStatus.AVAILABLE
    prop.mark_persisted(prop.version)
    return prop


class FakePropertyRepository:
    def __init__(self, *listings: Property) -> None:
        self.store = {listing.id: listing for listing in listings}
        self.saved: list[Property] = []

    async def get(self, property_id: Any) -> Property | None:
        return self.store.get(property_id)

    async def add(self, prop: Property) -> None:
        self.store[prop.id] = prop

    async def save(self, prop: Property, *, expected_version: int | None = None) -> None:
        self.store[prop.id] = prop
        self.saved.append(prop)


class FakeCheckRepository:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, AvailabilityCheck] = {}
        self.added: list[AvailabilityCheck] = []

    async def get(self, check_id: Any) -> AvailabilityCheck | None:
        return self.store.get(check_id)

    async def get_open_for_listing(self, property_id: Any) -> AvailabilityCheck | None:
        return next(
            (
                check
                for check in self.store.values()
                if check.property_id == property_id
                and check.status is CheckStatus.PENDING
            ),
            None,
        )

    async def get_open_for_landlord(self, landlord_id: Any) -> AvailabilityCheck | None:
        open_checks = [
            check
            for check in self.store.values()
            if check.landlord_id == landlord_id and check.status is CheckStatus.PENDING
        ]
        open_checks.sort(key=lambda check: check.asked_at, reverse=True)
        return open_checks[0] if open_checks else None

    async def latest_answered_for_listing(
        self, property_id: Any
    ) -> AvailabilityCheck | None:
        answered = [
            check
            for check in self.store.values()
            if check.property_id == property_id
            and check.status is CheckStatus.ANSWERED
            and check.answered_at is not None
        ]
        answered.sort(key=lambda check: check.answered_at or NOW, reverse=True)
        return answered[0] if answered else None

    async def add(self, check: AvailabilityCheck) -> None:
        self.store[check.id] = check
        self.added.append(check)
        check.mark_persisted(check.version)

    async def save(self, check: AvailabilityCheck) -> None:
        self.store[check.id] = check

    def seed(self, check: AvailabilityCheck) -> None:
        self.store[check.id] = check
        check.mark_persisted(check.version)


class FakeUnitOfWork(UnitOfWork):
    def __init__(self, properties: FakePropertyRepository, checks: FakeCheckRepository) -> None:
        self.properties = properties
        self.checks = checks
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
        if name == "availability_check":
            return self.checks
        raise LookupError(name)


def a_unit_of_work(*listings: Property) -> FakeUnitOfWork:
    return FakeUnitOfWork(FakePropertyRepository(*listings), FakeCheckRepository())


class TestAskingTheLandlord:
    @pytest.mark.asyncio
    async def test_a_question_is_created_and_names_the_landlord(self) -> None:
        listing = a_listing()
        uow = a_unit_of_work(listing)

        request = await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )

        assert request.landlord_id == listing.landlord_id
        assert request.question_key == "ask.availability_check"
        assert request.already_pending is False

    @pytest.mark.asyncio
    async def test_a_second_tenant_joins_the_question_already_in_flight(self) -> None:
        """Asking twice because two tenants are interested is noise the landlord
        did not sign up for."""
        listing = a_listing()
        uow = a_unit_of_work(listing)
        use_case = RequestAvailabilityCheckUseCase(uow)

        first = await use_case.execute(
            RequestAvailabilityCheckCommand(
                property_id=listing.id, tenant_id=uuid.uuid4()
            ),
            now=NOW,
        )
        second = await use_case.execute(
            RequestAvailabilityCheckCommand(
                property_id=listing.id, tenant_id=uuid.uuid4()
            ),
            now=LATER,
        )

        assert second.check_id == first.check_id
        assert second.already_pending is True
        assert len(uow.checks.added) == 1

    @pytest.mark.asyncio
    async def test_a_listing_that_does_not_exist_is_not_asked_about(self) -> None:
        uow = a_unit_of_work()

        with pytest.raises(ResourceNotFound):
            await RequestAvailabilityCheckUseCase(uow).execute(
                RequestAvailabilityCheckCommand(property_id=uuid.uuid4()), now=NOW
            )


class TestApplyingTheAnswer:
    @pytest.mark.asyncio
    async def test_one_taken_decrements_the_listing(self) -> None:
        listing = a_listing(lodgings=2)
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )

        outcome = await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id, answer=AvailabilityAnswer.ONE_TAKEN
            ),
            now=LATER,
        )

        assert outcome is not None
        assert outcome.lodgings_left == 1
        assert outcome.confirms_availability is True
        assert outcome.listing_status == PropertyStatus.AVAILABLE.value

    @pytest.mark.asyncio
    async def test_all_taken_takes_the_listing_out_of_search(self) -> None:
        listing = a_listing(lodgings=2)
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )

        outcome = await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id, answer=AvailabilityAnswer.RENTED
            ),
            now=LATER,
        )

        assert outcome is not None
        assert outcome.lodgings_left == 0
        assert outcome.confirms_availability is False
        assert outcome.listing_status == PropertyStatus.RENTED.value

    @pytest.mark.asyncio
    async def test_the_answer_carries_back_who_was_waiting(self) -> None:
        listing = a_listing()
        tenant_id = uuid.uuid4()
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(
                property_id=listing.id, tenant_id=tenant_id
            ),
            now=NOW,
        )

        outcome = await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id,
                answer=AvailabilityAnswer.STILL_AVAILABLE,
            ),
            now=LATER,
        )

        assert outcome is not None
        assert outcome.tenant_id == tenant_id

    @pytest.mark.asyncio
    async def test_an_answer_to_no_question_changes_nothing(self) -> None:
        """A landlord may say "c'est déjà loué" out of the blue. Guessing which
        listing he meant would take a lodging out of search on a coincidence."""
        listing = a_listing()
        uow = a_unit_of_work(listing)

        outcome = await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id, answer=AvailabilityAnswer.RENTED
            ),
            now=LATER,
        )

        assert outcome is None
        assert listing.status is PropertyStatus.AVAILABLE

    @pytest.mark.asyncio
    async def test_the_question_closes_so_a_later_answer_finds_nothing(self) -> None:
        listing = a_listing(lodgings=3)
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )
        use_case = AnswerAvailabilityCheckUseCase(uow)

        await use_case.execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id, answer=AvailabilityAnswer.ONE_TAKEN
            ),
            now=LATER,
        )
        again = await use_case.execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id, answer=AvailabilityAnswer.ONE_TAKEN
            ),
            now=LATER,
        )

        assert again is None
        assert listing.lodgings == 2


class TestWhatPaymentIsAllowedToKnow:
    @pytest.mark.asyncio
    async def test_a_confirmed_listing_releases_the_payment(self) -> None:
        listing = a_listing()
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )
        await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id,
                answer=AvailabilityAnswer.STILL_AVAILABLE,
            ),
            now=LATER,
        )

        assert await ListingAvailabilityReader(uow).is_confirmed_available(listing.id)

    @pytest.mark.asyncio
    async def test_an_unanswered_question_releases_nothing(self) -> None:
        listing = a_listing()
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )

        assert not await ListingAvailabilityReader(uow).is_confirmed_available(listing.id)

    @pytest.mark.asyncio
    async def test_a_listing_never_asked_about_releases_nothing(self) -> None:
        listing = a_listing()
        uow = a_unit_of_work(listing)

        assert not await ListingAvailabilityReader(uow).is_confirmed_available(listing.id)

    @pytest.mark.asyncio
    async def test_a_confirmation_does_not_outlive_the_listing_it_was_about(self) -> None:
        """Confirmed on Monday, rented on Tuesday: the listing has the final
        say, not the old answer."""
        listing = a_listing(lodgings=2)
        uow = a_unit_of_work(listing)
        await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id), now=NOW
        )
        await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id,
                answer=AvailabilityAnswer.STILL_AVAILABLE,
            ),
            now=LATER,
        )
        listing.mark_as_rented(now=LATER)

        assert not await ListingAvailabilityReader(uow).is_confirmed_available(listing.id)
