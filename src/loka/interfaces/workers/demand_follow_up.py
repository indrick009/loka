"""The hourly sweep that keeps a tenant's search from running for ever.

Nothing a user does triggers a reminder: a month simply passes. So the worker
looks once an hour for searches due one, and for reminders left unanswered a
week (see ``FollowUpTenantDemandsUseCase``). The events it records reach the
conversation through the outbox like any other.

Each demand is saved under its version, so two worker processes sweeping at
the same moment cannot remind one tenant twice: the second save conflicts and
its sweep is simply retried an hour later.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loka.bounded_contexts.property.application.use_cases.follow_up_tenant_demands import (
    FollowUpTenantDemandsUseCase,
)
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 3600.0


async def sweep_forever(database: Database, *, interval: float = SWEEP_INTERVAL_SECONDS) -> None:
    while True:
        try:
            async with unit_of_work(database) as uow:
                await FollowUpTenantDemandsUseCase(uow).sweep(now=datetime.now(UTC))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - resilience path
            _logger.exception("demand_follow_up_failed", error=str(exc))
        await asyncio.sleep(interval)
