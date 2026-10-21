"""SQLAlchemy adapter for tenant demands.

The partial unique index on ``(tenant_id) WHERE status = 'OPEN'`` is the real
guard against owing one person several answers: two turns racing on the same
tenant both see no open demand, and the second insert is the one that fails.
``add`` turns that into a domain error, so the caller can pick up the demand
already in flight instead of returning a 500 to a tenant who did nothing wrong.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.domain.entities.tenant_demand import (
    DemandStatus,
    TenantDemand,
)
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    TenantDemandRow,
)
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyTenantDemandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, demand_id: Any) -> TenantDemand | None:
        row = await self._session.get(TenantDemandRow, demand_id)
        return _from_row(row) if row is not None else None

    async def get_open_for_tenant(self, tenant_id: Any) -> TenantDemand | None:
        result = await self._session.execute(
            select(TenantDemandRow)
            .where(
                TenantDemandRow.tenant_id == tenant_id,
                TenantDemandRow.status == DemandStatus.OPEN.value,
            )
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return _from_row(row) if row is not None else None

    async def open_demands(self, *, limit: int) -> list[TenantDemand]:
        """The answers still owed, in the order the tenants have been waiting."""
        result = await self._session.execute(
            select(TenantDemandRow)
            .where(TenantDemandRow.status == DemandStatus.OPEN.value)
            .order_by(TenantDemandRow.received_at.asc())
            .limit(limit)
        )
        return [_from_row(row) for row in result.scalars().all()]

    async def add(self, demand: TenantDemand) -> None:
        self._session.add(TenantDemandRow(**_to_row(demand)))
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflict(
                "this tenant already has a demand awaiting an answer",
                context={"tenant_id": str(demand.tenant_id)},
            ) from exc
        demand.mark_persisted(demand.version)

    async def save(self, demand: TenantDemand) -> None:
        """Optimistic: an answer must not land on a demand someone else closed."""
        expected = demand.persisted_version
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(TenantDemandRow)
                .where(
                    TenantDemandRow.id == demand.id,
                    TenantDemandRow.version == expected,
                )
                .values(**_to_row(demand), version=expected + 1)
            ),
        )
        if result.rowcount == 0:
            raise ConcurrencyConflict(
                "this demand changed while it was being answered",
                context={"demand_id": str(demand.id), "expected_version": expected},
            )
        demand.mark_persisted(expected + 1)


def _to_row(demand: TenantDemand) -> dict[str, Any]:
    return {
        "id": demand.id,
        "tenant_id": demand.tenant_id,
        "status": demand.status.value,
        "property_type": demand.property_type,
        "city": demand.city,
        "neighbourhoods": list(demand.neighbourhoods),
        "latitude": demand.near[0] if demand.near else None,
        "longitude": demand.near[1] if demand.near else None,
        "near_is_exact": demand.near_exact,
        "max_rent_xaf": demand.max_rent.amount if demand.max_rent else None,
        "max_advance_months": demand.max_advance_months,
        "currency": demand.max_rent.currency if demand.max_rent else "XAF",
        "received_at": demand.received_at,
        "updated_at": demand.updated_at,
        "answered_at": demand.answered_at,
    }


def _from_row(row: TenantDemandRow) -> TenantDemand:
    demand = TenantDemand(
        demand_id=row.id,
        tenant_id=row.tenant_id,
        property_type=row.property_type,
        city=row.city,
        neighbourhoods=tuple(row.neighbourhoods or ()),
        max_rent=(
            Money(row.max_rent_xaf, row.currency) if row.max_rent_xaf is not None else None
        ),
        now=row.received_at,
        near=(
            (row.latitude, row.longitude)
            if row.latitude is not None and row.longitude is not None
            else None
        ),
        near_exact=row.near_is_exact,
        max_advance_months=row.max_advance_months,
    )
    demand.pull_events()
    demand.status = DemandStatus(row.status)
    demand.updated_at = row.updated_at
    demand.answered_at = row.answered_at
    demand.mark_persisted(row.version)
    return demand


__all__ = ["SqlAlchemyTenantDemandRepository"]
