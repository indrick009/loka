"""Record what a tenant is looking for, so Kora can come back to him.

Kora answers no search in the thread. It takes the demand, acknowledges it, and
replies in a later message once there is something worth sending — a direct
match, a neighbouring area, a price a little above, or the outcome of a §8
negotiation with a landlord. None of that fits in the two seconds a reply takes.

Idempotent per tenant. A tenant sharpens his search as he talks, and every turn
that completes his criteria arrives here: the open demand is restated rather
than duplicated, so one person is owed one answer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.property.domain.entities.tenant_demand import TenantDemand
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    TenantDemandRepository,
)
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ConcurrencyConflict
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class RecordTenantDemandCommand:
    tenant_id: uuid.UUID
    property_type: str | None = None
    city: str | None = None
    neighbourhood: str | None = None
    max_rent_xaf: int | None = None


@dataclass(frozen=True, slots=True)
class RecordedDemand:
    demand_id: uuid.UUID
    restated: bool


class RecordTenantDemandUseCase:
    name = "record_tenant_demand"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: RecordTenantDemandCommand, *, now: datetime
    ) -> RecordedDemand:
        demands: TenantDemandRepository = self._uow.repository("tenant_demand")
        max_rent = (
            Money(command.max_rent_xaf) if command.max_rent_xaf is not None else None
        )

        existing = await demands.get_open_for_tenant(command.tenant_id)
        if existing is not None:
            existing.restate(
                property_type=command.property_type,
                city=command.city,
                neighbourhood=command.neighbourhood,
                max_rent=max_rent,
                now=now,
            )
            await demands.save(existing)
            self._uow.collect(existing)
            await self._uow.commit()
            return RecordedDemand(demand_id=existing.demand_id, restated=True)

        demand = TenantDemand(
            demand_id=new_id(),
            tenant_id=command.tenant_id,
            property_type=command.property_type,
            city=command.city,
            neighbourhood=command.neighbourhood,
            max_rent=max_rent,
            now=now,
        )
        try:
            await demands.add(demand)
        except ConcurrencyConflict:
            # Two turns of the same conversation raced. Theirs is the demand
            # that exists, and this tenant is owed one answer, not two.
            raced = await demands.get_open_for_tenant(command.tenant_id)
            if raced is None:
                raise
            return RecordedDemand(demand_id=raced.demand_id, restated=True)

        self._uow.collect(demand)
        await self._uow.commit()
        self._logger.info(
            "tenant_demand_recorded",
            tenant_id=str(command.tenant_id),
            city=command.city,
            property_type=command.property_type,
        )
        return RecordedDemand(demand_id=demand.demand_id, restated=False)
