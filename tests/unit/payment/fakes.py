"""In-memory doubles for the payment context.

Shared rather than copied: three test modules drive the same aggregate through
the same repositories, and a fake that drifts from the port it stands in for
tests nothing. Adding a method to a port means adding it here, which is the
cheapest reminder that a port gained a method at all.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from loka.bounded_contexts.payment.application.ports import (
    PaymentInitiation,
    VerifiedCallback,
)
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    PaymentPurpose,
    PaymentStatus,
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.repositories import RawCallbackRecord
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.identifiers import new_id

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LISTING_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()
LANDLORD_ID = uuid.uuid4()


class FakePaymentRepository:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, ServiceFeePayment] = {}
        self.saved: list[ServiceFeePayment] = []
        self.added: list[ServiceFeePayment] = []

    async def get(self, payment_id: uuid.UUID) -> ServiceFeePayment | None:
        return self.store.get(payment_id)

    async def get_open_for_listing(
        self, tenant_id: uuid.UUID, property_id: uuid.UUID
    ) -> ServiceFeePayment | None:
        for payment in self.store.values():
            if (
                payment.tenant_id == tenant_id
                and payment.property_id == property_id
                and payment.status
                in (
                    PaymentStatus.CREATED,
                    PaymentStatus.INITIATED,
                    PaymentStatus.PENDING_CONFIRMATION,
                    PaymentStatus.FAILED,
                )
            ):
                return payment
        return None

    async def get_latest_for_listing(
        self, tenant_id: uuid.UUID, property_id: uuid.UUID
    ) -> ServiceFeePayment | None:
        for payment in self.store.values():
            if payment.tenant_id == tenant_id and payment.property_id == property_id:
                return payment
        return None

    async def get_by_provider_reference(
        self, provider: str, provider_reference: str
    ) -> ServiceFeePayment | None:
        for payment in self.store.values():
            if payment.provider == provider and payment.provider_reference == provider_reference:
                return payment
        return None

    async def get_awaiting_confirmation(
        self, *, provider: str, initiated_before: datetime, limit: int
    ) -> list[ServiceFeePayment]:
        waiting = [
            payment
            for payment in self.store.values()
            if payment.provider == provider
            and payment.status is PaymentStatus.PENDING_CONFIRMATION
            and payment.provider_reference is not None
            and payment.updated_at < initiated_before
        ]
        waiting.sort(key=lambda payment: payment.updated_at)
        return waiting[:limit]

    async def add(self, payment: ServiceFeePayment) -> None:
        self.store[payment.id] = payment
        self.added.append(payment)
        payment.mark_persisted(payment.version)

    async def save(
        self, payment: ServiceFeePayment, *, expected_version: int | None = None
    ) -> None:
        self.store[payment.id] = payment
        self.saved.append(payment)


class FakeGrantRepository:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, ServiceAccessGrant] = {}
        self.added: list[ServiceAccessGrant] = []

    async def get_active_for_listing(
        self, tenant_id: uuid.UUID, property_id: uuid.UUID
    ) -> ServiceAccessGrant | None:
        for grant in self.store.values():
            if (
                grant.tenant_id == tenant_id
                and grant.property_id == property_id
                and grant.is_active
            ):
                return grant
        return None

    async def add(self, grant: ServiceAccessGrant) -> None:
        self.store[grant.id] = grant
        self.added.append(grant)

    def seed(self, grant: ServiceAccessGrant) -> None:
        self.store[grant.id] = grant


class FakeCallbackRepository:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], RawCallbackRecord] = {}

    async def find(self, provider: str, provider_event_id: str) -> RawCallbackRecord | None:
        return self.store.get((provider, provider_event_id))

    async def add(self, record: RawCallbackRecord) -> bool:
        key = (record.provider, record.provider_event_id)
        if key in self.store:
            return False
        self.store[key] = record
        return True

    def seed(self, record: RawCallbackRecord) -> None:
        self.store[(record.provider, record.provider_event_id)] = record


class FakeGateway:
    def __init__(
        self,
        *,
        provider_reference: str = "mock_ref_1",
        verification: VerifiedCallback | None = None,
        raise_on_initiate: bool = False,
    ) -> None:
        self._provider_reference = provider_reference
        self._verification = verification
        self._raise_on_initiate = raise_on_initiate
        self.initiated: list[tuple[str, int]] = []

    def name(self) -> str:
        return "mock"

    async def initiate(
        self, *, payment_id: str, amount_xaf: int, **kwargs: object
    ) -> PaymentInitiation:
        if self._raise_on_initiate:
            raise RuntimeError("transport down")
        self.initiated.append((payment_id, amount_xaf))
        return PaymentInitiation(provider="mock", provider_reference=self._provider_reference)

    def verify_callback(
        self, *, raw_body: bytes, signature: str, headers: dict[str, str]
    ) -> VerifiedCallback:
        return self._verification or VerifiedCallback(
            provider_event_id="evt-1",
            provider_reference=self._provider_reference,
            accepted=True,
            status="SUCCEEDED",
            provider_reported_at=NOW,
        )


class FakeUnitOfWork(UnitOfWork):
    def __init__(
        self,
        payments: FakePaymentRepository,
        grants: FakeGrantRepository,
        callbacks: FakeCallbackRepository,
    ) -> None:
        self.payments = payments
        self.grants = grants
        self.callbacks = callbacks
        self.collected: list[object] = []
        self.commits = 0

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    @property
    def events(self) -> object:
        return None

    def collect(self, aggregate: object) -> None:
        self.collected.append(aggregate)

    def repository(self, name: str) -> object:
        if name == "payment":
            return self.payments
        if name == "payment_grant":
            return self.grants
        if name == "payment_callback":
            return self.callbacks
        raise LookupError(name)


def a_payment(**overrides: object) -> ServiceFeePayment:
    kwargs: dict[str, object] = {
        "payment_id": new_id(),
        "tenant_id": TENANT_ID,
        "landlord_id": LANDLORD_ID,
        "property_id": LISTING_ID,
        "amount": Money(1000),
        "purpose": PaymentPurpose.SERVICE_ACCESS,
        "now": NOW,
    }
    kwargs.update(overrides)
    return ServiceFeePayment(**kwargs)  # type: ignore[arg-type]
