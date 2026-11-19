"""The sweep that settles a lodging unlock without waiting for Fapshi's webhook.

The API is not reachable from the internet, so the webhook may never come;
Fapshi also never retries one. Every few seconds the payments still pending
are asked about at the provider; a settled one unlocks the contact through the
usual ``PaymentSucceeded`` path. Idempotent: a payment the webhook already
credited is left alone. Food, dating, found and bus tickets have their own
sweeps (``unlock_payments``, ``booking_payments``).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from loka.bounded_contexts.payment.application.use_cases.reconcile_pending_payments import (
    ReconcilePendingPaymentsCommand,
)
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    reconcile_pending_payments_use_case,
    unit_of_work,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 15.0
# A payment younger than this is still being validated on the payer's phone.
PENDING_AT_LEAST = timedelta(seconds=20)

_clock = SystemClock()


async def settle_once(database: Database, settings: Settings) -> None:
    if settings.payment.gateway == "mock":
        # The mock settles at once and has no status to ask.
        return
    async with unit_of_work(database) as uow:
        report = await reconcile_pending_payments_use_case(uow, settings).execute(
            ReconcilePendingPaymentsCommand(
                provider=settings.payment.gateway, older_than=PENDING_AT_LEAST, limit=20
            ),
            now=_clock.now(),
        )
    if report.examined:
        _logger.info("housing_payments_swept", **report.to_dict())


async def sweep_forever(
    database: Database, settings: Settings, *, interval: float = SWEEP_INTERVAL_SECONDS
) -> None:
    while True:
        try:
            await settle_once(database, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - resilience path
            _logger.exception("housing_payments_sweep_failed", error=str(exc))
        await asyncio.sleep(interval)
