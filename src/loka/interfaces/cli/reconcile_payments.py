"""Ask the payment provider about payments whose webhook never arrived.

Fapshi delivers one webhook per event and never retries, so a request lost
while the API was restarting is a payer charged and never served. Run this on a
schedule — every few minutes is plenty at Kora's volume — and during any
incident that touched the webhook endpoint.

    python -m loka.interfaces.cli.reconcile_payments --older-than 10

It is safe to run twice: settlement is idempotent on the aggregate and on the
one-active-grant-per-(tenant, listing) index, so a payment already credited is
counted and left alone.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from loka.bounded_contexts.payment.application.use_cases.reconcile_pending_payments import (
    ReconcilePendingPaymentsCommand,
    ReconciliationReport,
)
from loka.shared.domain.clock import SystemClock
from loka.shared.infrastructure.composition import (
    reconcile_pending_payments_use_case,
    unit_of_work,
)
from loka.shared.infrastructure.config.settings import Settings, get_settings
from loka.shared.infrastructure.db.engine import Database

clock = SystemClock()


async def reconcile(
    settings: Settings, *, older_than_minutes: int, limit: int
) -> ReconciliationReport:
    database = Database(settings.database, application_name="loka-reconcile")
    await database.connect()
    try:
        async with unit_of_work(database) as uow:
            return await reconcile_pending_payments_use_case(uow, settings).execute(
                ReconcilePendingPaymentsCommand(
                    provider=settings.payment.gateway,
                    older_than=timedelta(minutes=older_than_minutes),
                    limit=limit,
                ),
                now=clock.now(),
            )
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Settle payments the provider confirmed but never announced."
    )
    parser.add_argument(
        "--older-than",
        type=int,
        default=settings.payment.reconcile_after_minutes,
        help="only payments pending for at least this many minutes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="most payments to examine in one pass (the provider rate-limits status reads)",
    )
    args = parser.parse_args(argv)

    report = asyncio.run(
        reconcile(settings, older_than_minutes=args.older_than, limit=args.limit)
    )
    print(f"reconciliation complete ({settings.payment.gateway})")
    for key, value in report.to_dict().items():
        print(f"  {key:<14} {value}")


if __name__ == "__main__":
    main()
