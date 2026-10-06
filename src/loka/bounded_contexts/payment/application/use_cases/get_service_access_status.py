"""Payment and access status for one application.

Read-only: it never creates, mutates or commits anything. Accepted answers are
the current payment state and the active grant, so a client can decide between
"pay", "waiting confirmation" and "unlocked" without guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.bounded_contexts.payment.application.dto.payment_dto import (
    PaymentStatusView,
    ServiceAccessStatusView,
)
from loka.bounded_contexts.payment.domain.repositories import (
    AccessGrantRepository,
    PaymentRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import AuthorizationDenied, ResourceNotFound


@dataclass(frozen=True, slots=True)
class ServiceAccessQuery:
    application_id: Any
    tenant_id: Any


@dataclass(frozen=True, slots=True)
class ServiceAccessStatus:
    payment: PaymentStatusView | None
    grant: ServiceAccessStatusView | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "payment": self.payment.to_dict() if self.payment else None,
            "grant": self.grant.to_dict() if self.grant else None,
        }


class GetServiceAccessStatusUseCase:
    name = "get_service_access_status"

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(self, query: ServiceAccessQuery, *, now: datetime) -> ServiceAccessStatus:
        payments: PaymentRepository = self._uow.repository("payment")
        grants: AccessGrantRepository = self._uow.repository("payment_grant")

        payment = await payments.get_for_application(query.application_id)
        grant = await grants.get_active_for_application(query.application_id)
        if payment is None and grant is None:
            raise ResourceNotFound(
                "no payment exists for this application",
                context={"application_id": str(query.application_id)},
            )

        # The caller is the tenant who owns the attempt: someone else must not
        # learn whether a payment (or a grant) exists for another person.
        if payment is not None and payment.tenant_id != query.tenant_id:
            raise AuthorizationDenied(
                "only the tenant who owns this payment may read its status",
                context={"application_id": str(query.application_id)},
            )
        if grant is not None and grant.tenant_id != query.tenant_id:
            raise AuthorizationDenied(
                "only the tenant who owns this grant may read its status",
                context={"application_id": str(query.application_id)},
            )

        return ServiceAccessStatus(
            payment=_payment_view(payment) if payment else None,
            grant=_grant_view(grant) if grant else None,
        )


def _payment_view(payment: Any) -> PaymentStatusView:
    return PaymentStatusView(
        payment_id=str(payment.id),
        status=payment.status.value,
        amount_xaf=payment.amount.amount,
        currency=payment.amount.currency,
        purpose=payment.purpose.value,
        provider=payment.provider,
        provider_reference=payment.provider_reference,
        attempt_count=payment.attempt_count,
        expires_at=payment.expires_at,
        paid_at=payment.paid_at,
        settled=payment.is_settled,
    )


def _grant_view(grant: Any) -> ServiceAccessStatusView:
    return ServiceAccessStatusView(
        grant_id=str(grant.id),
        status=grant.status.value,
        reveal_count=grant.reveal_count,
        granted_at=grant.granted_at,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
    )