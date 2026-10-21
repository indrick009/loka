"""An answered search keeps watching, and is asked after a month.

Observed in the spec review: a tenant whose chosen lodging was taken was told
"décrivez une nouvelle recherche" and then never heard from Kora again, even
when the right studio was published the next morning.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from tests.unit.property.test_match_tenant_demands import (
    NOW,
    FakeDemands,
    FakeProperties,
    FakeSearch,
    FakeUnitOfWork,
    a_demand,
    a_listing,
    build,
)

from loka.bounded_contexts.property.application.use_cases.follow_up_tenant_demands import (
    FollowUpTenantDemandsUseCase,
)
from loka.bounded_contexts.property.application.use_cases.record_tenant_demand import (
    RecordTenantDemandCommand,
    RecordTenantDemandUseCase,
)
from loka.bounded_contexts.property.domain.entities.tenant_demand import (
    DemandStatus,
    TenantDemand,
)

pytestmark = pytest.mark.unit


def answered(demand: TenantDemand, *property_ids: str) -> TenantDemand:
    demand.mark_answered(now=NOW, proposals=[{"property_id": pid} for pid in property_ids])
    demand.pull_events()
    return demand


def follow_up(*demands: TenantDemand) -> tuple[FollowUpTenantDemandsUseCase, FakeUnitOfWork]:
    uow = FakeUnitOfWork(FakeDemands(*demands), FakeProperties(), FakeSearch([]))
    return FollowUpTenantDemandsUseCase(uow), uow


class TestAnAnsweredSearchKeepsWatching:
    async def test_a_listing_published_later_is_still_sent(self) -> None:
        demand = answered(a_demand(), "an-earlier-lodging")
        listing = a_listing()
        use_case, _, _ = build(demand, listings=(listing,))

        assert await use_case.for_listing(listing.id, now=NOW) == 1
        assert demand.has_seen(str(listing.id))

    async def test_a_lodging_already_shown_is_never_sent_twice(self) -> None:
        listing = a_listing()
        demand = answered(a_demand(), str(listing.id))
        use_case, _, _ = build(demand, listings=(listing,))

        assert await use_case.for_listing(listing.id, now=NOW) == 0

    async def test_a_search_he_stopped_watches_nothing(self) -> None:
        demand = answered(a_demand())
        demand.withdraw(reason="no longer looking", now=NOW)
        listing = a_listing()
        use_case, _, _ = build(demand, listings=(listing,))

        assert await use_case.for_listing(listing.id, now=NOW) == 0

    async def test_a_new_search_replaces_the_watch(self) -> None:
        earlier = answered(a_demand())
        demands = FakeDemands(earlier)
        uow = FakeUnitOfWork(demands, FakeProperties(), FakeSearch([]))

        await RecordTenantDemandUseCase(uow).execute(
            RecordTenantDemandCommand(tenant_id=earlier.tenant_id, city="Douala"), now=NOW
        )

        assert earlier.status is DemandStatus.WITHDRAWN
        assert [d.city for d in demands.store.values() if d.is_watching] == ["Douala"]


class TestTheMonthOnReminder:
    async def test_a_month_of_silence_brings_the_question(self) -> None:
        demand = answered(a_demand())
        use_case, _ = follow_up(demand)

        outcome = await use_case.sweep(now=NOW + timedelta(days=30))

        assert outcome.reminded == 1
        [event] = demand.pull_events()
        assert event.event_type == "TenantDemandReminderDue"
        # Asked once: the next hourly sweep leaves him alone.
        assert (await use_case.sweep(now=NOW + timedelta(days=30, hours=1))).reminded == 0

    async def test_a_young_search_is_not_disturbed(self) -> None:
        use_case, _ = follow_up(a_demand())

        assert (await use_case.sweep(now=NOW + timedelta(days=29))).reminded == 0

    async def test_a_week_without_an_answer_ends_the_watch(self) -> None:
        demand = answered(a_demand())
        use_case, _ = follow_up(demand)
        await use_case.sweep(now=NOW + timedelta(days=30))

        outcome = await use_case.sweep(now=NOW + timedelta(days=37))

        assert outcome.lapsed == 1
        assert demand.status is DemandStatus.WITHDRAWN

    async def test_yes_watches_another_month(self) -> None:
        demand = answered(a_demand())
        use_case, _ = follow_up(demand)
        await use_case.sweep(now=NOW + timedelta(days=30))

        assert await use_case.renew(demand.tenant_id, now=NOW + timedelta(days=31))
        assert (await use_case.sweep(now=NOW + timedelta(days=40))).lapsed == 0
        assert demand.is_watching
        assert (await use_case.sweep(now=NOW + timedelta(days=61))).reminded == 1

    async def test_no_ends_it_at_once(self) -> None:
        demand = answered(a_demand())
        use_case, _ = follow_up(demand)

        assert await use_case.stop(demand.tenant_id, now=NOW)
        assert demand.status is DemandStatus.WITHDRAWN
        assert not await use_case.stop(demand.tenant_id, now=NOW)
