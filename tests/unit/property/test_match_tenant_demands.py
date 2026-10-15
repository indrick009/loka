"""Coming back to a tenant: the promise "je reviens vers vous" is kept.

Live, a tenant's demand stayed OPEN while a lodging matching it was published,
and when he asked again he was told nothing matched. Both directions are pinned
here: a demand finds what the catalogue already holds, and a publication finds
the demands already waiting.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from loka.bounded_contexts.property.application.queries.search_properties import (
    PropertySearchCriteria,
    PropertySearchResult,
)
from loka.bounded_contexts.property.application.use_cases.match_tenant_demands import (
    MatchTenantDemandsUseCase,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.tenant_demand import (
    DemandStatus,
    TenantDemand,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    ChargingPolicy,
    PropertyStatus,
    PropertyType,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def a_demand(
    *, neighbourhoods: tuple[str, ...] = ("Biyem-Assi", "Jouvence"), budget: int = 45_000
) -> TenantDemand:
    demand = TenantDemand(
        demand_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        property_type="STUDIO",
        city="Yaoundé",
        neighbourhoods=neighbourhoods,
        max_rent=Money(budget),
        now=NOW,
    )
    demand.pull_events()
    demand.mark_persisted(demand.version)
    return demand


def a_listing(*, neighbourhood: str = "Jouvence", rent: int = 40_000) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)
    prop.set_type(PropertyType.STUDIO, now=NOW)
    prop.set_location(Location(city="Yaoundé", neighbourhood=neighbourhood), now=NOW)
    prop.set_rent(Money(rent), now=NOW)
    prop.set_charges(Money(0), ChargingPolicy.INCLUDED, now=NOW)
    prop.status = PropertyStatus.AVAILABLE
    return prop


class FakeDemands:
    def __init__(self, *demands: TenantDemand) -> None:
        self.store = {demand.id: demand for demand in demands}

    async def get(self, demand_id: Any) -> TenantDemand | None:
        return self.store.get(demand_id)

    async def open_demands(self, *, limit: int) -> list[TenantDemand]:
        return [d for d in self.store.values() if d.status is DemandStatus.OPEN][:limit]

    async def save(self, demand: TenantDemand) -> None:
        return None


class FakeProperties:
    def __init__(self, *listings: Property) -> None:
        self.store = {listing.id: listing for listing in listings}

    async def get(self, property_id: Any) -> Property | None:
        return self.store.get(property_id)


class FakeSearch:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.criteria: PropertySearchCriteria | None = None

    async def search(
        self, criteria: PropertySearchCriteria, *, cursor: str | None = None, limit: int = 20
    ) -> PropertySearchResult:
        self.criteria = criteria
        return PropertySearchResult(items=self.items[:limit], next_cursor=None)


class FakeUnitOfWork(UnitOfWork):
    def __init__(
        self, demands: FakeDemands, properties: FakeProperties, search: FakeSearch
    ) -> None:
        self._repos: dict[str, object] = {
            "tenant_demand": demands,
            "property": properties,
            "property_search": search,
        }
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
    def events(self) -> Any:
        return None

    def collect(self, aggregate: Any) -> None:
        return None

    def repository(self, name: str) -> Any:
        return self._repos[name]


def build(
    *demands: TenantDemand,
    listings: tuple[Property, ...] = (),
    hits: list[dict[str, Any]] | None = None,
) -> tuple[MatchTenantDemandsUseCase, FakeUnitOfWork, FakeSearch]:
    search = FakeSearch(hits or [])
    uow = FakeUnitOfWork(FakeDemands(*demands), FakeProperties(*listings), search)
    return MatchTenantDemandsUseCase(uow), uow, search


def answered_proposals(demand: TenantDemand) -> list[dict[str, Any]]:
    events = [e for e in demand.pull_events() if e.event_type == "TenantDemandAnswered"]
    assert len(events) == 1
    return list(events[0].metadata["proposals"])


class TestDemandAccepts:
    def test_one_of_his_neighbourhoods_within_budget(self) -> None:
        assert a_demand().accepts(
            property_type="STUDIO", city="Yaoundé", neighbourhood="Jouvence", rent_xaf=40_000
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("property_type", "ROOM"),
            ("city", "Douala"),
            ("neighbourhood", "Bastos"),
            ("rent_xaf", 60_000),  # a gap to negotiate, not a match
            ("rent_xaf", 20_000),  # far below what he is looking for
        ],
    )
    def test_anything_off_his_criteria_is_refused(self, field: str, value: Any) -> None:
        listing: dict[str, Any] = {
            "property_type": "STUDIO",
            "city": "Yaoundé",
            "neighbourhood": "Jouvence",
            "rent_xaf": 40_000,
        }
        listing[field] = value
        assert not a_demand().accepts(**listing)

    def test_no_neighbourhood_named_means_the_whole_city(self) -> None:
        assert a_demand(neighbourhoods=()).accepts(
            property_type="STUDIO", city="Yaoundé", neighbourhood=None, rent_xaf=40_000
        )


class TestAPublicationFindsTheDemandsWaiting:
    async def test_a_matching_listing_answers_the_demand(self) -> None:
        demand, listing = a_demand(), a_listing()
        use_case, uow, _ = build(demand, listings=(listing,))

        answered = await use_case.for_listing(listing.id, now=NOW)

        assert answered == 1
        assert demand.status is DemandStatus.ANSWERED
        assert uow.commits == 1
        assert answered_proposals(demand) == [
            {
                "property_id": str(listing.id),
                "property_type": "STUDIO",
                "city": "Yaoundé",
                "neighbourhood": "Jouvence",
                "rent_xaf": 40_000,
            }
        ]

    async def test_a_listing_elsewhere_leaves_him_waiting(self) -> None:
        demand, listing = a_demand(), a_listing(neighbourhood="Bastos")
        use_case, uow, _ = build(demand, listings=(listing,))

        assert await use_case.for_listing(listing.id, now=NOW) == 0
        assert demand.status is DemandStatus.OPEN
        assert uow.commits == 0

    async def test_a_listing_not_published_answers_nobody(self) -> None:
        demand, listing = a_demand(), a_listing()
        listing.status = PropertyStatus.DRAFT
        use_case, _, _ = build(demand, listings=(listing,))

        assert await use_case.for_listing(listing.id, now=NOW) == 0
        assert demand.status is DemandStatus.OPEN


class TestADemandFindsWhatIsAlreadyPublished:
    async def test_the_catalogue_answers_with_every_hit(self) -> None:
        demand = a_demand()
        hit = {
            "property_id": str(uuid.uuid4()),
            "type": "STUDIO",
            "city": "Yaoundé",
            "neighbourhood": "Jouvence",
            "price_xaf": 40_000,
        }
        use_case, _, search = build(demand, hits=[hit])

        assert await use_case.for_demand(demand.id, now=NOW) == 1
        assert demand.status is DemandStatus.ANSWERED
        assert answered_proposals(demand)[0]["property_id"] == hit["property_id"]
        # Verification ranks a landlord, it does not hide his listing.
        assert search.criteria is not None and search.criteria.verified_only is False
        assert search.criteria.neighbourhoods == ("Biyem-Assi", "Jouvence")
        assert (search.criteria.min_price_xaf, search.criteria.max_price_xaf) == (
            36_000,
            47_250,
        )

    async def test_nothing_found_keeps_the_demand_open(self) -> None:
        demand = a_demand()
        use_case, uow, _ = build(demand, hits=[])

        assert await use_case.for_demand(demand.id, now=NOW) == 0
        assert demand.status is DemandStatus.OPEN
        assert uow.commits == 0

    async def test_an_answered_demand_is_not_answered_twice(self) -> None:
        demand = a_demand()
        demand.mark_answered(now=NOW)
        use_case, _, search = build(demand, hits=[{"property_id": str(uuid.uuid4())}])

        assert await use_case.for_demand(demand.id, now=NOW) == 0
        assert search.criteria is None
