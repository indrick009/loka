"""Initiate, callback and status use cases with in-memory doubles."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.payment.application.ports import PaymentInitiation, VerifiedCallback
from loka.bounded_contexts.payment.application.use_cases.get_service_access_status import (
    GetServiceAccessStatusUseCase,
    ServiceAccessQuery,
)
from loka.bounded_contexts.payment.application.use_cases.handle_payment_callback import (
    HandlePaymentCallbackCommand,
    HandlePaymentCallbackUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.initiate_service_fee import (
    InitiateServiceFeeCommand,
    InitiateServiceFeeUseCase,
)
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    PaymentPurpose,
    PaymentStatus,
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.exceptions import (
    CallbackRejected,
    PaymentAlreadySettled,
    PaymentGatewayError,
)
from loka.bounded_contexts.payment.domain.repositories import RawCallbackRecord
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import AuthorizationDenied, ResourceNotFound
from loka.shared.domain.identifiers import new_id

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
APP_ID = uuid.uuid4()
TENANT_ID = uuid.uuid4()
LANDLORD_ID = uuid.uuid4()


class FakePaymentRepository:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, ServiceFeePayment] = {}
        self.saved: list[ServiceFeePayment] = []
        self.added: list[ServiceFeePayment] = []

    async def get(self, payment_id: uuid.UUID) -> ServiceFeePayment | None:
        return self.store.get(payment_id)

    async def get_open_for_application(self, application_id: uuid.UUID) -> ServiceFeePayment | None:
        for payment in self.store.values():
            if payment.application_id == application_id and payment.status in (
                PaymentStatus.CREATED,
                PaymentStatus.INITIATED,
                PaymentStatus.PENDING_CONFIRMATION,
                PaymentStatus.FAILED,
            ):
                return payment
        return None

    async def get_for_application(self, application_id: uuid.UUID) -> ServiceFeePayment | None:
        for payment in self.store.values():
            if payment.application_id == application_id:
                return payment
        return None

    async def get_by_provider_reference(
        self, provider: str, provider_reference: str
    ) -> ServiceFeePayment | None:
        for payment in self.store.values():
            if payment.provider == provider and payment.provider_reference == provider_reference:
                return payment
        return None

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

    async def get_active_for_application(
        self, application_id: uuid.UUID
    ) -> ServiceAccessGrant | None:
        for grant in self.store.values():
            if grant.application_id == application_id and grant.is_active:
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


def _payment(**overrides: object) -> ServiceFeePayment:
    kwargs: dict[str, object] = {
        "payment_id": new_id(),
        "tenant_id": TENANT_ID,
        "landlord_id": LANDLORD_ID,
        "application_id": APP_ID,
        "amount": Money(1000),
        "purpose": PaymentPurpose.SERVICE_ACCESS,
        "now": NOW,
    }
    kwargs.update(overrides)
    return ServiceFeePayment(**kwargs)  # type: ignore[arg-type]


def _settled_payment() -> ServiceFeePayment:
    pay = _payment()
    pay.initiate(provider="mock", provider_reference="ref-settled", now=NOW)
    pay.mark_succeeded(
        provider_reference="ref-settled", signature="sig", now=NOW, provider_reported_at=NOW
    )
    return pay


def _pending_payment() -> ServiceFeePayment:
    pay = _payment()
    pay.initiate(provider="mock", provider_reference="ref-1", now=NOW)
    return pay


class TestInitiateServiceFee:
    @pytest.mark.asyncio
    async def test_new_application_creates_and_initiates_a_payment(self) -> None:
        payments = FakePaymentRepository()
        uow = FakeUnitOfWork(payments, FakeGrantRepository(), FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        view = await use_case.execute(
            InitiateServiceFeeCommand(
                application_id=APP_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert view.status == PaymentStatus.PENDING_CONFIRMATION.value
        assert view.amount_xaf == 1000
        assert view.provider == "mock"
        assert view.attempt_count == 1
        assert view.settled is False
        assert len(payments.added) == 1
        assert uow.commits == 1
        assert uow.collected and isinstance(uow.collected[0], ServiceFeePayment)

    @pytest.mark.asyncio
    async def test_a_pending_payment_is_returned_without_a_second_provider_call(self) -> None:
        gateway = FakeGateway()
        uow, _ = _uow_pair()
        use_case = InitiateServiceFeeUseCase(uow, gateway=gateway, service_fee_xaf=1000)
        first = await use_case.execute(
            InitiateServiceFeeCommand(
                application_id=APP_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        second = await use_case.execute(
            InitiateServiceFeeCommand(
                application_id=APP_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert second.payment_id == first.payment_id
        assert len(gateway.initiated) == 1

    @pytest.mark.asyncio
    async def test_a_failed_payment_is_retried_in_place(self) -> None:
        pay = _pending_payment()
        pay.mark_failed(reason="declined", now=NOW)
        payments = FakePaymentRepository()
        await payments.add(pay)
        uow = FakeUnitOfWork(payments, FakeGrantRepository(), FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        view = await use_case.execute(
            InitiateServiceFeeCommand(
                application_id=APP_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert view.status == PaymentStatus.PENDING_CONFIRMATION.value
        assert view.attempt_count == 2
        assert len(payments.store) == 1  # same row, not a new one
        assert payments.saved == [pay]

    @pytest.mark.asyncio
    async def test_a_settled_application_refuses_a_new_payment(self) -> None:
        grants = FakeGrantRepository()
        grants.seed(ServiceAccessGrant.from_payment(_settled_payment(), now=NOW, grant_id=new_id()))
        uow = FakeUnitOfWork(FakePaymentRepository(), grants, FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        with pytest.raises(PaymentAlreadySettled):
            await use_case.execute(
                InitiateServiceFeeCommand(
                    application_id=APP_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
                ),
                now=NOW,
            )
        assert uow.commits == 0

    @pytest.mark.asyncio
    async def test_a_gateway_failure_is_reported_as_a_gateway_error(self) -> None:
        uow, use_case = _uow_pair(
            gateway=FakeGateway(raise_on_initiate=True), initiate=True
        )
        with pytest.raises(PaymentGatewayError):
            await use_case.execute(
                InitiateServiceFeeCommand(
                    application_id=APP_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
                ),
                now=NOW,
            )
        assert uow.commits == 0


class TestHandlePaymentCallback:
    @pytest.mark.asyncio
    async def test_a_verified_callback_settles_and_grants_access(self) -> None:
        uow, use_case = _uow_pair(
            gateway=FakeGateway(
                provider_reference="ref-1",
                verification=VerifiedCallback(
                    provider_event_id="evt-1",
                    provider_reference="ref-1",
                    accepted=True,
                    status="SUCCEEDED",
                    provider_reported_at=NOW,
                ),
            )
        )
        await uow.payments.add(_pending_payment())

        outcome = await use_case.execute(
            HandlePaymentCallbackCommand(
                provider="mock", provider_event_id="evt-1", raw_body=b"{}", signature="sig",
                headers={},
            ),
            now=NOW,
        )

        assert outcome.replayed is False
        assert outcome.payment is not None and outcome.payment.status == "SUCCEEDED"
        assert outcome.grant is not None and outcome.grant.status == "ACTIVE"
        assert uow.commits == 1
        assert len(uow.grants.added) == 1

    @pytest.mark.asyncio
    async def test_a_failed_callback_marks_the_payment_failed_without_a_grant(self) -> None:
        uow, use_case = _uow_pair(
            gateway=FakeGateway(
                provider_reference="ref-1",
                verification=VerifiedCallback(
                    provider_event_id="evt-2",
                    provider_reference="ref-1",
                    accepted=True,
                    status="FAILED",
                    rejection_reason="insufficient funds",
                    provider_reported_at=NOW,
                ),
            )
        )
        await uow.payments.add(_pending_payment())

        outcome = await use_case.execute(
            HandlePaymentCallbackCommand(
                provider="mock", provider_event_id="", raw_body=b"{}", signature="sig", headers={},
            ),
            now=NOW,
        )

        assert outcome.payment is not None and outcome.payment.status == "FAILED"
        assert outcome.grant is None
        assert len(uow.grants.added) == 0
        assert uow.commits == 1

    @pytest.mark.asyncio
    async def test_an_unknown_payment_is_not_found(self) -> None:
        _, use_case = _uow_pair(gateway=FakeGateway(provider_reference="ghost"))
        with pytest.raises(ResourceNotFound):
            await use_case.execute(
                HandlePaymentCallbackCommand(
                    provider="mock", provider_event_id="", raw_body=b"{}", signature="sig",
                    headers={},
                ),
                now=NOW,
            )

    @pytest.mark.asyncio
    async def test_a_replayed_event_is_acknowledged_without_touching_state(self) -> None:
        uow, use_case = _uow_pair(gateway=FakeGateway(provider_reference="ref-1"))
        await uow.payments.add(_pending_payment())
        uow.callbacks.seed(
            RawCallbackRecord(
                provider="mock", provider_event_id="evt-1", payload_signature="sig",
                raw_payload={}, payment_id=None, accepted=True, rejection_reason=None,
                received_at=NOW,
            )
        )

        outcome = await use_case.execute(
            HandlePaymentCallbackCommand(
                provider="mock", provider_event_id="evt-1", raw_body=b"{}", signature="sig",
                headers={},
            ),
            now=NOW,
        )

        assert outcome.replayed is True
        assert uow.commits == 0

    @pytest.mark.asyncio
    async def test_a_bad_signature_is_rejected(self) -> None:
        uow, use_case = _uow_pair(
            gateway=FakeGateway(
                verification=VerifiedCallback(
                    provider_event_id="evt-1", provider_reference="", accepted=False,
                    status="REJECTED", rejection_reason="signature verification failed",
                )
            )
        )
        with pytest.raises(CallbackRejected):
            await use_case.execute(
                HandlePaymentCallbackCommand(
                    provider="mock", provider_event_id="", raw_body=b"{}", signature="bad",
                    headers={},
                ),
                now=NOW,
            )
        assert uow.commits == 0


class TestGetServiceAccessStatus:
    @pytest.mark.asyncio
    async def test_reports_payment_and_active_grant(self) -> None:
        uow = FakeUnitOfWork(
            FakePaymentRepository(), FakeGrantRepository(), FakeCallbackRepository()
        )
        use_case = GetServiceAccessStatusUseCase(uow)
        pay = _settled_payment()
        await uow.payments.add(pay)
        uow.grants.seed(ServiceAccessGrant.from_payment(pay, now=NOW, grant_id=new_id()))

        result = await use_case.execute(
            ServiceAccessQuery(application_id=APP_ID, tenant_id=TENANT_ID), now=NOW
        )

        assert result.payment is not None and result.payment.settled is True
        assert result.grant is not None and result.grant.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_unknown_application_is_not_found(self) -> None:
        uow = FakeUnitOfWork(
            FakePaymentRepository(), FakeGrantRepository(), FakeCallbackRepository()
        )
        use_case = GetServiceAccessStatusUseCase(uow)
        with pytest.raises(ResourceNotFound):
            await use_case.execute(
                ServiceAccessQuery(application_id=uuid.uuid4(), tenant_id=TENANT_ID), now=NOW
            )

    @pytest.mark.asyncio
    async def test_a_tenant_cannot_read_someone_elses_payment(self) -> None:
        uow = FakeUnitOfWork(
            FakePaymentRepository(), FakeGrantRepository(), FakeCallbackRepository()
        )
        use_case = GetServiceAccessStatusUseCase(uow)
        await uow.payments.add(_settled_payment())
        with pytest.raises(AuthorizationDenied):
            await use_case.execute(
                ServiceAccessQuery(application_id=APP_ID, tenant_id=uuid.uuid4()), now=NOW
            )


def _uow_pair(
    *,
    gateway: FakeGateway | None = None,
    initiate: bool = False,
) -> tuple[FakeUnitOfWork, object]:
    payments = FakePaymentRepository()
    grants = FakeGrantRepository()
    callbacks = FakeCallbackRepository()
    uow = FakeUnitOfWork(payments, grants, callbacks)
    if initiate:
        use_case: object = InitiateServiceFeeUseCase(uow, gateway=gateway or FakeGateway())
        return uow, use_case
    return uow, HandlePaymentCallbackUseCase(uow, gateway=gateway or FakeGateway())