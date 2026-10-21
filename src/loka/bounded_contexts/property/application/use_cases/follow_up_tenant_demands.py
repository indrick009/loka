"""Ask a tenant, a month on, whether he is still looking — and act on it.

An answered demand keeps watching new listings (see ``TenantDemand``). Left to
itself it would watch for ever, and a tenant who found a room in March would
still be sent studios in June. Three moves bound it:

- ``sweep`` — a month without a word from him: the reminder is due, and its
  event asks the conversation to put the question; a week after the question
  with no answer, the watch ends;
- ``renew`` — he said he is still looking: another month;
- ``stop`` — he said he is not: the watch ends now.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.property.domain.repositories.property_repository import (
    TenantDemandRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork

# One sweep's worth. The sweep runs hourly; anything past it waits an hour.
SWEEP_BATCH = 200


@dataclass(frozen=True, slots=True)
class SweepOutcome:
    reminded: int
    lapsed: int


class FollowUpTenantDemandsUseCase:
    name = "follow_up_tenant_demands"

    def __init__(
        self, uow: UnitOfWork, *, logger: structlog.stdlib.BoundLogger | None = None
    ) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    async def sweep(self, *, now: datetime) -> SweepOutcome:
        demands: TenantDemandRepository = self._uow.repository("tenant_demand")
        reminded = lapsed = 0
        for demand in await demands.due_reminders(now=now, limit=SWEEP_BATCH):
            demand.remind(now=now)
            await demands.save(demand)
            self._uow.collect(demand)
            reminded += 1
        for demand in await demands.lapsed_reminders(now=now, limit=SWEEP_BATCH):
            demand.withdraw(reason="no answer to the reminder", now=now)
            await demands.save(demand)
            self._uow.collect(demand)
            lapsed += 1
        if reminded or lapsed:
            await self._uow.commit()
            self._logger.info("tenant_demands_swept", reminded=reminded, lapsed=lapsed)
        return SweepOutcome(reminded=reminded, lapsed=lapsed)

    async def renew(self, tenant_id: uuid.UUID, *, now: datetime) -> bool:
        """``False`` when he has no search left to renew."""
        return await self._each(tenant_id, now=now, stop=False)

    async def stop(self, tenant_id: uuid.UUID, *, now: datetime) -> bool:
        """``False`` when he had no search running."""
        return await self._each(tenant_id, now=now, stop=True)

    async def _each(self, tenant_id: uuid.UUID, *, now: datetime, stop: bool) -> bool:
        demands: TenantDemandRepository = self._uow.repository("tenant_demand")
        watched = await demands.watching_for_tenant(tenant_id)
        for demand in watched:
            if stop:
                demand.withdraw(reason="no longer looking", now=now)
            else:
                demand.renew(now=now)
            await demands.save(demand)
            self._uow.collect(demand)
        if watched:
            await self._uow.commit()
        return bool(watched)
