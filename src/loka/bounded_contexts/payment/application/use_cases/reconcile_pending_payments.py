"""Settle payments whose webhook never arrived.

Fapshi sends one webhook per event and never retries, so a request lost to a
deploy, a timeout or a 500 is money the payer sent and a contact Kora never
revealed — and nothing in the system would ever say so. The payer's only
remaining signal is a complaint.

This closes that hole from the other side: take the payments still waiting for
a verdict, ask the provider what actually happened to each, and apply it. It is
the same settlement path as a webhook, reached by asking instead of being told,
which is why both go through :class:`PaymentStatusSource` and neither trusts a
pushed body.

Deliberately not a daemon. It is one bounded pass over a bounded list, so a
cron entry, a one-off command during an incident and a test all run the same
code with nothing scheduling itself in the background.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import structlog

from loka.bounded_contexts.payment.application.ports import PaymentStatusSource
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.repositories import (
    AccessGrantRepository,
    PaymentRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError, ExternalServiceUnavailable, RateLimited
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class ReconcilePendingPaymentsCommand:
    provider: str
    # Younger than this and the webhook is probably still in flight; asking
    # about it burns the provider's rate limit on a question that answers
    # itself in seconds.
    older_than: timedelta
    limit: int = 50


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    examined: int = 0
    settled: int = 0
    failed: int = 0
    expired: int = 0
    still_pending: int = 0
    unreachable: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "examined": self.examined,
            "settled": self.settled,
            "failed": self.failed,
            "expired": self.expired,
            "still_pending": self.still_pending,
            "unreachable": self.unreachable,
        }


class ReconcilePendingPaymentsUseCase:
    name = "reconcile_pending_payments"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        status_source: PaymentStatusSource,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._status_source = status_source
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: ReconcilePendingPaymentsCommand, *, now: datetime
    ) -> ReconciliationReport:
        payments: PaymentRepository = self._uow.repository("payment")
        waiting = await payments.get_awaiting_confirmation(
            provider=command.provider,
            initiated_before=now - command.older_than,
            limit=command.limit,
        )
        report = ReconciliationReport(examined=len(waiting))
        if not waiting:
            return report

        for payment in waiting:
            report = await self._reconcile_one(payment, report, now=now)

        await self._uow.commit()
        self._logger.info("payments_reconciled", provider=command.provider, **report.to_dict())
        return report

    async def _reconcile_one(
        self, payment: ServiceFeePayment, report: ReconciliationReport, *, now: datetime
    ) -> ReconciliationReport:
        """One payment, and a provider failure that stops at this payment.

        A sweep that aborts on the first unreachable transaction leaves every
        later payer uncredited for the sake of an earlier one, so transport
        faults are counted and the pass continues. A rate limit is different:
        it means stop asking, and it is raised.
        """
        reference = payment.provider_reference or ""
        try:
            verdict = await self._status_source.fetch_status(provider_reference=reference)
        except RateLimited:
            raise
        except ExternalServiceUnavailable as exc:
            self._logger.warning(
                "payment_reconciliation_unreachable",
                payment_id=str(payment.id),
                provider_reference=reference,
                error=str(exc),
            )
            return _with(report, unreachable=report.unreachable + 1)

        status = verdict.status.upper()
        if status == "SUCCEEDED":
            grant = await self._settle(payment, verdict, now=now)
            self._logger.info(
                "payment_reconciled_late",
                payment_id=str(payment.id),
                grant_id=str(grant.id) if grant else None,
            )
            return _with(report, settled=report.settled + 1)

        if status == "FAILED":
            payment.mark_failed(reason="provider reported failure", now=now)
            await self._save(payment)
            return _with(report, failed=report.failed + 1)

        if status == "EXPIRED":
            payment.expire(now=now)
            await self._save(payment)
            return _with(report, expired=report.expired + 1)

        return _with(report, still_pending=report.still_pending + 1)

    async def _settle(
        self, payment: ServiceFeePayment, verdict: Any, *, now: datetime
    ) -> ServiceAccessGrant | None:
        try:
            payment.mark_succeeded(
                provider_reference=verdict.provider_reference,
                signature=verdict.audit_digest or "",
                now=now,
                provider_reported_at=verdict.provider_reported_at,
                # No freshness window: the whole point of this pass is to
                # settle a payment the provider confirms was made hours ago.
                tolerance_seconds=None,
            )
        except DomainError:
            # The aggregate refused the transition — it was already settled or
            # already dead. Nothing to do and nothing to shout about.
            self._logger.info(
                "payment_reconciliation_declined",
                payment_id=str(payment.id),
                status=payment.status.value,
            )
            return None

        await self._save(payment)
        grants: AccessGrantRepository = self._uow.repository("payment_grant")
        grant = await grants.get_active_for_listing(payment.tenant_id, payment.property_id)
        if grant is None:
            grant = ServiceAccessGrant.from_payment(payment, now=now, grant_id=new_id())
            await grants.add(grant)
        self._uow.collect(grant)
        return grant

    async def _save(self, payment: ServiceFeePayment) -> None:
        payments: PaymentRepository = self._uow.repository("payment")
        await payments.save(payment)
        self._uow.collect(payment)


def _with(report: ReconciliationReport, **changes: int) -> ReconciliationReport:
    return replace(report, **changes)
