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
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.entities.tenant_demand import (
    NEAR_RADIUS_KM,
    DemandStatus,
    TenantDemand,
    distance_km,
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


def a_listing(
    *,
    neighbourhood: str = "Jouvence",
    rent: int = 40_000,
    photos: tuple[str, ...] = (),
    documents: tuple[str, ...] = (),
) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)
    prop.set_type(PropertyType.STUDIO, now=NOW)
    prop.set_location(Location(city="Yaoundé", neighbourhood=neighbourhood), now=NOW)
    prop.set_rent(Money(rent), now=NOW)
    prop.set_charges(Money(0), ChargingPolicy.INCLUDED, now=NOW)
    prop.status = PropertyStatus.AVAILABLE
    for key in photos:
        prop.add_media(
            PropertyMedia(
                media_id=uuid.uuid4(),
                object_key=key,
                kind=MediaKind.PHOTO,
                status=MediaStatus.UPLOADED,
            ),
            now=NOW,
        )
    for key in documents:
        prop.add_media(
            PropertyMedia(
                media_id=uuid.uuid4(),
                object_key=key,
                kind=MediaKind.DOCUMENT,
                status=MediaStatus.UPLOADED,
            ),
            now=NOW,
        )
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


# A tenant's pin in Odza, and two lodgings: one a short ride away in a district
# he never named, one across town in Bastos.
ODZA = (3.7905, 11.5370)
NEAR_ODZA = (3.8020, 11.5400)  # about 1.3 km
BASTOS = (3.8905, 11.5139)  # about 11 km


class TestASharedLocation:
    """A pin used to be read as "the whole city": it now measures distance."""

    def _pinned(self, *, neighbourhoods: tuple[str, ...] = ()) -> TenantDemand:
        demand = a_demand(neighbourhoods=neighbourhoods)
        demand.near = ODZA
        return demand

    def test_a_lodging_a_short_ride_away_qualifies_whatever_its_district(self) -> None:
        assert distance_km(ODZA, NEAR_ODZA) < NEAR_RADIUS_KM
        assert self._pinned().is_around(neighbourhood="Ekounou", point=NEAR_ODZA)

    def test_a_lodging_across_town_does_not(self) -> None:
        assert not self._pinned().is_around(neighbourhood="Bastos", point=BASTOS)

    def test_a_district_he_named_always_counts(self) -> None:
        demand = self._pinned(neighbourhoods=("Bastos",))
        assert demand.is_around(neighbourhood="Bastos", point=BASTOS)

    def test_a_lodging_with_no_position_is_not_shut_out_by_a_pin_alone(self) -> None:
        assert self._pinned().is_around(neighbourhood="Mvan", point=None)

    def test_the_rule_applies_to_a_freshly_published_lodging(self) -> None:
        assert self._pinned().accepts(
            property_type="STUDIO",
            city="Yaoundé",
            neighbourhood="Ekounou",
            rent_xaf=40_000,
            point=NEAR_ODZA,
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
                "latitude": None,
                "longitude": None,
                "advance_months": None,
                "photo_object_keys": [],
            }
        ]

    async def test_the_published_photos_travel_with_the_proposal(self) -> None:
        # A tenant sent a line and a rent but no picture has to take the
        # landlord's word for it: the photos he published are what he is shown.
        demand = a_demand()
        listing = a_listing(
            photos=("properties/p/1/a.jpg", "properties/p/1/b.jpg"),
            documents=("properties/p/1/plan.pdf",),
        )
        use_case, _, _ = build(demand, listings=(listing,))

        await use_case.for_listing(listing.id, now=NOW)

        assert answered_proposals(demand)[0]["photo_object_keys"] == [
            "properties/p/1/a.jpg",
            "properties/p/1/b.jpg",
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

    async def test_the_hit_photos_are_carried_to_the_proposal(self) -> None:
        demand = a_demand()
        hit = {
            "property_id": str(uuid.uuid4()),
            "type": "STUDIO",
            "city": "Yaoundé",
            "neighbourhood": "Jouvence",
            "price_xaf": 40_000,
            "media_object_keys": ["properties/p/9/a.jpg", "properties/p/9/b.jpg"],
        }
        use_case, _, _ = build(demand, hits=[hit])

        await use_case.for_demand(demand.id, now=NOW)

        assert answered_proposals(demand)[0]["photo_object_keys"] == [
            "properties/p/9/a.jpg",
            "properties/p/9/b.jpg",
        ]

    async def test_a_hit_without_media_carries_no_photo(self) -> None:
        demand = a_demand()
        hit = {
            "property_id": str(uuid.uuid4()),
            "type": "STUDIO",
            "city": "Yaoundé",
            "neighbourhood": "Jouvence",
            "price_xaf": 40_000,
        }
        use_case, _, _ = build(demand, hits=[hit])

        await use_case.for_demand(demand.id, now=NOW)

        assert answered_proposals(demand)[0]["photo_object_keys"] == []

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



class _NothingExact(FakeSearch):
    """The catalogue holds only a near miss: the exact search finds nothing."""

    def __init__(self, items: list[dict[str, Any]]) -> None:
        super().__init__(items)
        self.calls: list[PropertySearchCriteria] = []

    async def search(
        self, criteria: PropertySearchCriteria, *, cursor: str | None = None, limit: int = 20
    ) -> PropertySearchResult:
        self.calls.append(criteria)
        items = self.items if len(self.calls) > 1 else []
        return PropertySearchResult(items=items[:limit], next_cursor=None)


class TestAGapIsProposedAsAGap:
    """§8: a little dearer or a little further is proposed, and said so."""

    def test_a_rent_a_little_above_is_tolerated_not_accepted(self) -> None:
        listing = {
            "property_type": "STUDIO",
            "city": "Yaoundé",
            "neighbourhood": "Jouvence",
            "rent_xaf": 50_000,  # 45 000 asked: +11 %
        }
        assert not a_demand().accepts(**listing)
        assert a_demand().tolerates(**listing)
        assert not a_demand().tolerates(**{**listing, "rent_xaf": 55_000})  # +22 %

    def test_a_typed_district_measures_a_gap_but_never_widens_a_match(self) -> None:
        demand = a_demand(neighbourhoods=("Odza",))
        demand.near, demand.near_exact = ODZA, False

        assert not demand.is_around(neighbourhood="Ekounou", point=NEAR_ODZA)
        assert demand.tolerates(
            property_type="STUDIO",
            city="Yaoundé",
            neighbourhood="Ekounou",
            rent_xaf=45_000,
            point=NEAR_ODZA,
        )

    async def test_nothing_exact_falls_back_to_a_wider_search_marked_as_gaps(self) -> None:
        demand = a_demand(neighbourhoods=("Odza",))
        demand.near, demand.near_exact = ODZA, False
        hit = {
            "property_id": str(uuid.uuid4()),
            "type": "STUDIO",
            "city": "Yaoundé",
            "neighbourhood": "Ekounou",
            "price_xaf": 50_000,
            "latitude": NEAR_ODZA[0],
            "longitude": NEAR_ODZA[1],
        }
        search = _NothingExact([hit])
        uow = FakeUnitOfWork(FakeDemands(demand), FakeProperties(), search)

        proposed = await MatchTenantDemandsUseCase(uow).for_demand(demand.id, now=NOW)

        assert proposed == 1
        exact, wider = search.calls
        assert exact.near is None  # a district's centre never widens a match
        assert wider.near == ODZA and wider.near_radius_km == 6.0
        assert wider.max_price_xaf == int(45_000 * 1.15)
        gap = answered_proposals(demand)[0]["gap"]
        assert gap == {"over_budget_xaf": 5_000, "distance_km": 1.3}


LISTING_AT_JOUVENCE: dict[str, Any] = {
    "property_type": "STUDIO",
    "city": "Yaoundé",
    "neighbourhood": "Jouvence",
    "rent_xaf": 40_000,
}


class TestTheAdvanceHeCanPay:
    """§3's fourth question, held against the advance the landlord asks."""

    LISTING = LISTING_AT_JOUVENCE

    def _can_pay(self, months: int | None) -> TenantDemand:
        demand = a_demand()
        demand.max_advance_months = months
        return demand

    def test_an_advance_within_his_means_matches(self) -> None:
        assert self._can_pay(4).accepts(**self.LISTING, advance_months=4)

    def test_a_heavier_advance_is_a_gap_not_a_match(self) -> None:
        demand = self._can_pay(4)

        assert not demand.accepts(**self.LISTING, advance_months=6)
        assert demand.tolerates(**self.LISTING, advance_months=6)
        assert demand.gap_with(rent_xaf=40_000, point=None, advance_months=6) == {
            "over_advance_months": 2
        }

    def test_a_year_against_five_months_is_not_put_to_him(self) -> None:
        assert not self._can_pay(5).tolerates(**self.LISTING, advance_months=12)

    @pytest.mark.parametrize(("can_pay", "asked"), [(None, 12), (3, None)])
    def test_nothing_to_compare_holds_nothing_back(
        self, can_pay: int | None, asked: int | None
    ) -> None:
        assert self._can_pay(can_pay).accepts(**self.LISTING, advance_months=asked)

    async def test_the_search_is_capped_at_what_he_can_pay(self) -> None:
        demand = self._can_pay(3)
        search = _NothingExact([])
        uow = FakeUnitOfWork(FakeDemands(demand), FakeProperties(), search)

        await MatchTenantDemandsUseCase(uow).for_demand(demand.id, now=NOW)

        exact, wider = search.calls
        assert exact.max_advance_months == 3
        assert wider.max_advance_months == 5
