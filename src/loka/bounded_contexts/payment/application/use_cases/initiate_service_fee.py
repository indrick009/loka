"""Initiate the 1 000 FCFA unlock of one listing for one tenant.

The URL-friendly phrasing is that the caller "pays" or "unlocks" the contact;
the domain language is precise: this creates a payment and asks the provider
for a reference. ``initiated`` is never ``settled`` — the invariant is enforced
by the aggregate and by the one-active-grant-per-(tenant, listing) partial
index.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.payment.application.dto.payment_dto import PaymentStatusView
from loka.bounded_contexts.payment.application.ports import (
    ListingAvailability,
    PaymentGateway,
)
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    PaymentPurpose,
    PaymentStatus,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.exceptions import (
    AvailabilityNotConfirmed,
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
    property_id: uuid.UUID
    tenant_id: uuid.UUID
    landlord_id: uuid.UUID
    idempotency_key: str | None = None
    # The mobile-money number to push the withdrawal to (direct payment). The
    # WhatsApp number is not always the one that holds the money, so the
    # tenant chooses it; ``None`` asks the provider for a checkout link instead.
    payer_phone: str | None = None

    @property
    def unlock_key(self) -> str:
        """The thing being bought, not the attempt buying it.

        An idempotency key tied to an attempt lets a retried attempt charge a
        second time for the same disclosure; tied to the pair, it cannot.
        """
        return self.idempotency_key or f"unlock:{self.tenant_id}:{self.property_id}"


class InitiateServiceFeeUseCase:
    name = "initiate_service_fee"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        gateway: PaymentGateway,
        availability: ListingAvailability | None = None,
        service_fee_xaf: int = 1000,
        payment_message: str = "Kora - contact du bailleur",
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._availability = availability
        self._service_fee_xaf = service_fee_xaf
        # What the payer reads on the provider's checkout page and on their
        # mobile-money confirmation. It is the only wording in this flow Kora
        # does not control the layout of, so it stays short and names Kora.
        self._payment_message = payment_message
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: InitiateServiceFeeCommand, *, now: datetime
    ) -> PaymentStatusView:
        payments: PaymentRepository = self._uow.repository("payment")
        grants: AccessGrantRepository = self._uow.repository("payment_grant")

        if (
            await grants.get_active_for_listing(command.tenant_id, command.property_id)
            is not None
        ):
            # He already has this landlord's number and this lodging's
            # location: there is nothing left to sell him here.
            raise PaymentAlreadySettled(
                "this tenant has already unlocked this listing",
                context={
                    "tenant_id": str(command.tenant_id),
                    "property_id": str(command.property_id),
                },
            )

        await self._require_confirmed_availability(command)

        payment = await payments.get_open_for_listing(command.tenant_id, command.property_id)
        if payment is None:
            payment = ServiceFeePayment(
                payment_id=new_id(),
                tenant_id=command.tenant_id,
                landlord_id=command.landlord_id,
                property_id=command.property_id,
                amount=Money(self._service_fee_xaf),
                purpose=PaymentPurpose.SERVICE_ACCESS,
                now=now,
                idempotency_key=command.unlock_key,
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
                property_id=command.property_id,
                amount=Money(self._service_fee_xaf),
                purpose=PaymentPurpose.SERVICE_ACCESS,
                now=now,
                idempotency_key=command.unlock_key,
            )

        try:
            initiation = await self._gateway.initiate(
                payment_id=str(payment.id),
                amount_xaf=payment.amount.amount,
                # Named arguments a provider may ignore: whoever pays and why.
                # A support question is then one search in the provider's own
                # dashboard instead of a join nobody can run from there.
                user_id=str(command.tenant_id),
                message=self._payment_message,
                payer_phone=command.payer_phone,
            )
        except Exception as exc:  # provider boundary: any transport error is a gateway fault
            raise PaymentGatewayError(
                f"payment provider refused the request: {type(exc).__name__}",
                context={
                    "tenant_id": str(command.tenant_id),
                    "property_id": str(command.property_id),
                },
            ) from exc

        payment.initiate(
            provider=initiation.provider,
            provider_reference=initiation.provider_reference,
            now=now,
            checkout_url=initiation.provider_url,
            link_ttl_seconds=initiation.link_ttl_seconds,
        )
        if payment.persisted_version == 0:
            await payments.add(payment)
        else:
            await payments.save(payment)
        self._uow.collect(payment)
        await self._uow.commit()
        self._logger.info(
            "service_fee_initiated",
            property_id=str(command.property_id),
            payment_id=str(payment.id),
            provider=initiation.provider,
            provider_reference=initiation.provider_reference,
        )
        return self._view(payment)

    async def _require_confirmed_availability(
        self, command: InitiateServiceFeeCommand
    ) -> None:
        """No charge before the landlord said the lodging is still free.

        The state can have changed between publication and this request, and
        the only person who knows is the landlord. Taking 1 000 FCFA for a
        contact that leads nowhere is the one failure this whole check exists
        to prevent (housing spec §9).
        """
        if self._availability is None:
            self._logger.warning(
                "service_fee_without_availability_gate",
                property_id=str(command.property_id),
            )
            return
        if await self._availability.is_confirmed_available(command.property_id):
            return
        raise AvailabilityNotConfirmed(
            "the landlord has not confirmed that this lodging is still available",
            context={
                "tenant_id": str(command.tenant_id),
                "property_id": str(command.property_id),
            },
        )

    @staticmethod
    def _view(payment: ServiceFeePayment) -> PaymentStatusView:
        return PaymentStatusView.of(payment)