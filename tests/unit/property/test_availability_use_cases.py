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
from loka.bounded_contexts.property.domain.entities.tenant_demand import TenantDemand
from loka.bounded_contexts.property.domain.services.negotiation import (
    Offer,
    OfferOutcome,
    OfferReply,
)
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

    async def asked_since(self, property_id: Any, *, since: datetime) -> int:
        return sum(
            1
            for check in self.store.values()
            if check.property_id == property_id and check.asked_at >= since
        )

    async def offers_made(self, property_id: Any) -> list[Offer]:
        return [
            check.offer
            for check in self.store.values()
            if check.property_id == property_id and check.offer is not None
        ]

    async def add(self, check: AvailabilityCheck) -> None:
        self.store[check.id] = check
        self.added.append(check)
        check.mark_persisted(check.version)

    async def save(self, check: AvailabilityCheck) -> None:
        self.store[check.id] = check

    def seed(self, check: AvailabilityCheck) -> None:
        self.store[check.id] = check
        check.mark_persisted(check.version)


class FakeDemands:
    def __init__(self) -> None:
        self.demands: list[TenantDemand] = []

    async def watching_for_tenant(self, tenant_id: Any) -> list[TenantDemand]:
        return [demand for demand in self.demands if demand.tenant_id == tenant_id]


class FakeUnitOfWork(UnitOfWork):
    def __init__(self, properties: FakePropertyRepository, checks: FakeCheckRepository) -> None:
        self.properties = properties
        self.checks = checks
        self.demands = FakeDemands()
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
        if name == "tenant_demand":
            return self.demands
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


def a_demand_for(tenant_id: uuid.UUID, *, budget: int, advance: int | None) -> TenantDemand:
    return TenantDemand(
        demand_id=uuid.uuid4(),
        tenant_id=tenant_id,
        property_type="STUDIO",
        city="Douala",
        neighbourhoods=("Bonapriso",),
        max_rent=Money(budget),
        now=NOW,
        max_advance_months=advance,
    )


class TestTheTenantsTermsArePut:
    """§8: one offer, riding on the availability question, when demand is low."""

    async def _ask(self, uow: FakeUnitOfWork, listing: Property, tenant: uuid.UUID) -> Offer | None:
        request = await RequestAvailabilityCheckUseCase(uow).execute(
            RequestAvailabilityCheckCommand(property_id=listing.id, tenant_id=tenant), now=NOW
        )
        return request.offer

    async def test_a_dearer_lodging_carries_his_budget_and_his_months(self) -> None:
        listing, tenant = a_listing(), uuid.uuid4()
        uow = a_unit_of_work(listing)
        uow.demands.demands.append(a_demand_for(tenant, budget=55_000, advance=4))

        offer = await self._ask(uow, listing, tenant)

        assert offer == Offer(
            rent_xaf=55_000, asked_rent_xaf=60_000, advance_months=4, asked_advance_months=6
        )
        assert uow.checks.added[0].offer == offer

    async def test_a_lodging_within_his_means_is_only_asked_about(self) -> None:
        listing, tenant = a_listing(), uuid.uuid4()
        uow = a_unit_of_work(listing)
        uow.demands.demands.append(a_demand_for(tenant, budget=65_000, advance=6))

        assert await self._ask(uow, listing, tenant) is None

    async def test_a_lodging_in_demand_is_not_negotiated(self) -> None:
        """Four people asked this week: the price is right."""
        listing, tenant = a_listing(), uuid.uuid4()
        uow = a_unit_of_work(listing)
        uow.demands.demands.append(a_demand_for(tenant, budget=55_000, advance=6))
        for _ in range(4):
            answered = AvailabilityCheck(
                check_id=uuid.uuid4(),
                property_id=listing.id,
                landlord_id=listing.landlord_id,
                tenant_id=uuid.uuid4(),
                now=NOW - timedelta(days=2),
            )
            answered.record_answer(AvailabilityAnswer.STILL_AVAILABLE, now=NOW)
            uow.checks.seed(answered)

        assert await self._ask(uow, listing, tenant) is None

    async def test_the_same_offer_is_never_made_twice(self) -> None:
        listing, tenant = a_listing(), uuid.uuid4()
        uow = a_unit_of_work(listing)
        uow.demands.demands.append(a_demand_for(tenant, budget=55_000, advance=6))
        earlier = AvailabilityCheck(
            check_id=uuid.uuid4(),
            property_id=listing.id,
            landlord_id=listing.landlord_id,
            tenant_id=uuid.uuid4(),
            now=NOW - timedelta(days=20),
            offer=Offer(
                rent_xaf=55_000, asked_rent_xaf=60_000, advance_months=None,
                asked_advance_months=None,
            ),
        )
        earlier.record_answer(AvailabilityAnswer.STILL_AVAILABLE, now=NOW)
        uow.checks.seed(earlier)

        assert await self._ask(uow, listing, tenant) is None

    async def test_his_counter_offer_travels_to_the_tenant(self) -> None:
        listing, tenant = a_listing(), uuid.uuid4()
        uow = a_unit_of_work(listing)
        uow.demands.demands.append(a_demand_for(tenant, budget=55_000, advance=6))
        await self._ask(uow, listing, tenant)

        outcome = await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id,
                answer=AvailabilityAnswer.STILL_AVAILABLE,
                offer_reply=OfferReply(outcome=OfferOutcome.COUNTERED, rent_xaf=58_000),
            ),
            now=LATER,
        )

        assert outcome is not None
        assert outcome.confirms_availability
        assert outcome.offer is not None and outcome.offer["rent_xaf"] == 55_000
        assert outcome.offer_reply == {
            "outcome": "COUNTERED", "rent_xaf": 58_000, "advance_months": None
        }

    async def test_a_lodging_gone_says_nothing_of_the_offer(self) -> None:
        listing, tenant = a_listing(), uuid.uuid4()
        uow = a_unit_of_work(listing)
        uow.demands.demands.append(a_demand_for(tenant, budget=55_000, advance=6))
        await self._ask(uow, listing, tenant)

        outcome = await AnswerAvailabilityCheckUseCase(uow).execute(
            AnswerAvailabilityCheckCommand(
                landlord_id=listing.landlord_id,
                answer=AvailabilityAnswer.RENTED,
                offer_reply=OfferReply(outcome=OfferOutcome.ACCEPTED),
            ),
            now=LATER,
        )

        assert outcome is not None and outcome.offer_reply is None
