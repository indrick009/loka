"""Initiate the 1 000 FCFA service fee for a rental application.

The URL-friendly phrasing is that the caller "pays" or "unlocks" the contact;
the domain language is precise: this creates a payment and asks the provider
for a reference. ``initiated`` is never ``settled`` — the invariant is enforced
by the aggregate and by the single-grant-per-application partial index.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.payment.application.dto.payment_dto import PaymentStatusView
from loka.bounded_contexts.payment.application.ports import PaymentGateway
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    PaymentPurpose,
    PaymentStatus,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.exceptions import (
    PaymentAlreadySettled,
    PaymentGatewayError,
)
from loka.bounded_contexts.payment.domain.repositories import (
    AccessGrantRepository,
    PaymentRepository,
)
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class InitiateServiceFeeCommand:
    application_id: uuid.UUID
    tenant_id: uuid.UUID
    landlord_id: uuid.UUID
    idempotency_key: str | None = None


class InitiateServiceFeeUseCase:
    name = "initiate_service_fee"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        gateway: PaymentGateway,
        service_fee_xaf: int = 1000,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._service_fee_xaf = service_fee_xaf
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: InitiateServiceFeeCommand, *, now: datetime
    ) -> PaymentStatusView:
        payments: PaymentRepository = self._uow.repository("payment")
        grants: AccessGrantRepository = self._uow.repository("payment_grant")

        if await grants.get_active_for_application(command.application_id) is not None:
            raise PaymentAlreadySettled(
                "this application already has an active access grant",
                context={"application_id": str(command.application_id)},
            )

        payment = await payments.get_open_for_application(command.application_id)
        if payment is None:
            payment = ServiceFeePayment(
                payment_id=new_id(),
                tenant_id=command.tenant_id,
                landlord_id=command.landlord_id,
                application_id=command.application_id,
                amount=Money(self._service_fee_xaf),
                purpose=PaymentPurpose.SERVICE_ACCESS,
                now=now,
                idempotency_key=command.idempotency_key,
            )
        elif payment.status in (PaymentStatus.INITIATED, PaymentStatus.PENDING_CONFIRMATION):
            # A duplicate initiate for an already-pending payment must not create
            # a second provider reference; surface the existing one instead.
            return self._view(payment)
        elif payment.is_terminal and payment.status is not PaymentStatus.FAILED:
            # An expired or cancelled attempt is a dead row: a fresh attempt gets
            # a fresh payment (the open partial index allows one live row only).
            payment = ServiceFeePayment(
                payment_id=new_id(),
                tenant_id=command.tenant_id,
                landlord_id=command.landlord_id,
                application_id=command.application_id,
                amount=Money(self._service_fee_xaf),
                purpose=PaymentPurpose.SERVICE_ACCESS,
                now=now,
                idempotency_key=command.idempotency_key,
            )

        try:
            initiation = await self._gateway.initiate(
                payment_id=str(payment.id),
                amount_xaf=payment.amount.amount,
            )
        except Exception as exc:  # provider boundary: any transport error is a gateway fault
            raise PaymentGatewayError(
                f"payment provider refused the request: {type(exc).__name__}",
                context={"application_id": str(command.application_id)},
            ) from exc

        payment.initiate(
            provider=initiation.provider,
            provider_reference=initiation.provider_reference,
            now=now,
        )
        if payment.persisted_version == 0:
            await payments.add(payment)
        else:
            await payments.save(payment)
        self._uow.collect(payment)
        await self._uow.commit()
        self._logger.info(
            "service_fee_initiated",
            application_id=str(command.application_id),
            payment_id=str(payment.id),
            provider=initiation.provider,
            provider_reference=initiation.provider_reference,
        )
        return self._view(payment)

    @staticmethod
    def _view(payment: ServiceFeePayment) -> PaymentStatusView:
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