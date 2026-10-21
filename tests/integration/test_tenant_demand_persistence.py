"""A tenant's watched search against real PostgreSQL: what it has seen, and
when it is due a reminder."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.domain.entities.tenant_demand import TenantDemand
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.tenant_demand_repository import (
    SqlAlchemyTenantDemandRepository,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def _demand(tenant_id: uuid.UUID, *, at: datetime) -> TenantDemand:
    return TenantDemand(
        demand_id=uuid.uuid4(),
        tenant_id=tenant_id,
        property_type="STUDIO",
        city="Yaoundé",
        neighbourhoods=("Odza",),
        max_rent=Money(60_000),
        now=at,
        max_advance_months=3,
    )


async def test_an_answered_search_keeps_its_watch(session: AsyncSession) -> None:
    repository = SqlAlchemyTenantDemandRepository(session)
    await repository.add(stored := _demand(uuid.uuid4(), at=NOW))
    await session.commit()
    demand = await repository.get(stored.id)
    assert demand is not None
    seen = str(uuid.uuid4())
    demand.mark_answered(now=NOW, proposals=[{"property_id": seen}])
    await repository.save(demand)
    await session.commit()

    [reloaded] = await repository.watching_for_tenant(demand.tenant_id)

    assert reloaded.has_seen(seen)
    assert reloaded.max_advance_months == 3
    assert [d.id for d in await repository.watching_demands(limit=10)] == [demand.id]


async def test_reminders_fall_due_and_lapse(session: AsyncSession) -> None:
    repository = SqlAlchemyTenantDemandRepository(session)
    old = _demand(uuid.uuid4(), at=NOW - timedelta(days=31))
    young = _demand(uuid.uuid4(), at=NOW - timedelta(days=3))
    for demand in (old, young):
        await repository.add(demand)
    await session.commit()

    assert [d.id for d in await repository.due_reminders(now=NOW, limit=10)] == [old.id]

    reloaded = await repository.get(old.id)
    assert reloaded is not None
    reloaded.remind(now=NOW)
    await repository.save(reloaded)
    await session.commit()

    assert await repository.due_reminders(now=NOW, limit=10) == []
    assert await repository.lapsed_reminders(now=NOW + timedelta(days=6), limit=10) == []
    lapsed = await repository.lapsed_reminders(now=NOW + timedelta(days=7), limit=10)
    assert [d.id for d in lapsed] == [old.id]
