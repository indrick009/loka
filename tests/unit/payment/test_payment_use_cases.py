"""Initiate, callback and status use cases with in-memory doubles."""

from __future__ import annotations

import uuid

import pytest
from tests.unit.payment.fakes import (
    LANDLORD_ID,
    LISTING_ID,
    NOW,
    TENANT_ID,
    FakeCallbackRepository,
    FakeGateway,
    FakeGrantRepository,
    FakePaymentRepository,
    FakeUnitOfWork,
    a_payment,
)

from loka.bounded_contexts.payment.application.ports import VerifiedCallback
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
from loka.shared.domain.errors import ResourceNotFound
from loka.shared.domain.identifiers import new_id


def _settled_payment() -> ServiceFeePayment:
    pay = a_payment()
    pay.initiate(provider="mock", provider_reference="ref-settled", now=NOW)
    pay.mark_succeeded(
        provider_reference="ref-settled", signature="sig", now=NOW, provider_reported_at=NOW
    )
    return pay


def _pending_payment() -> ServiceFeePayment:
    pay = a_payment()
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
                property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
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
                property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        second = await use_case.execute(
            InitiateServiceFeeCommand(
                property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
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
                property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert view.status == PaymentStatus.PENDING_CONFIRMATION.value
        assert view.attempt_count == 2
        assert len(payments.store) == 1  # same row, not a new one
        assert payments.saved == [pay]

    @pytest.mark.asyncio
    async def test_a_listing_already_unlocked_refuses_a_new_payment(self) -> None:
        grants = FakeGrantRepository()
        grants.seed(ServiceAccessGrant.from_payment(_settled_payment(), now=NOW, grant_id=new_id()))
        uow = FakeUnitOfWork(FakePaymentRepository(), grants, FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        with pytest.raises(PaymentAlreadySettled):
            await use_case.execute(
                InitiateServiceFeeCommand(
                    property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
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
                    property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
                ),
                now=NOW,
            )
        assert uow.commits == 0


class TestWhoPaysAgain:
    """The three situations the pricing rule has to tell apart.

    The pair is (tenant, listing), so: same listing again is free, a second
    listing from the same landlord is a second unlock, and another tenant
    wanting the same listing pays his own.
    """

    @pytest.mark.asyncio
    async def test_the_same_tenant_returning_to_the_same_listing_pays_nothing(self) -> None:
        grants = FakeGrantRepository()
        grants.seed(ServiceAccessGrant.from_payment(_settled_payment(), now=NOW, grant_id=new_id()))
        uow = FakeUnitOfWork(FakePaymentRepository(), grants, FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        with pytest.raises(PaymentAlreadySettled):
            await use_case.execute(
                InitiateServiceFeeCommand(
                    property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
                ),
                now=NOW,
            )

    @pytest.mark.asyncio
    async def test_a_second_listing_from_the_same_landlord_is_a_second_unlock(self) -> None:
        grants = FakeGrantRepository()
        grants.seed(ServiceAccessGrant.from_payment(_settled_payment(), now=NOW, grant_id=new_id()))
        uow = FakeUnitOfWork(FakePaymentRepository(), grants, FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        other_listing = uuid.uuid4()
        view = await use_case.execute(
            InitiateServiceFeeCommand(
                property_id=other_listing, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert view.amount_xaf == 1000
        assert view.status == PaymentStatus.PENDING_CONFIRMATION.value

    @pytest.mark.asyncio
    async def test_another_tenant_wanting_the_same_listing_pays_his_own(self) -> None:
        grants = FakeGrantRepository()
        grants.seed(ServiceAccessGrant.from_payment(_settled_payment(), now=NOW, grant_id=new_id()))
        uow = FakeUnitOfWork(FakePaymentRepository(), grants, FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        view = await use_case.execute(
            InitiateServiceFeeCommand(
                property_id=LISTING_ID, tenant_id=uuid.uuid4(), landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert view.amount_xaf == 1000
        assert view.status == PaymentStatus.PENDING_CONFIRMATION.value

    @pytest.mark.asyncio
    async def test_an_unsupplied_idempotency_key_is_the_pair_being_unlocked(self) -> None:
        """Keyed on the thing unlocked, so a retried attempt cannot charge twice."""
        payments = FakePaymentRepository()
        uow = FakeUnitOfWork(payments, FakeGrantRepository(), FakeCallbackRepository())
        use_case = InitiateServiceFeeUseCase(uow, gateway=FakeGateway(), service_fee_xaf=1000)

        await use_case.execute(
            InitiateServiceFeeCommand(
                property_id=LISTING_ID, tenant_id=TENANT_ID, landlord_id=LANDLORD_ID
            ),
            now=NOW,
        )

        assert payments.added[0].idempotency_key == f"unlock:{TENANT_ID}:{LISTING_ID}"


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
            ServiceAccessQuery(property_id=LISTING_ID, tenant_id=TENANT_ID), now=NOW
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
                ServiceAccessQuery(property_id=uuid.uuid4(), tenant_id=TENANT_ID), now=NOW
            )

    @pytest.mark.asyncio
    async def test_another_tenant_is_told_nothing_exists_rather_than_refused(self) -> None:
        """Refusing would itself disclose that somebody else paid for this listing."""
        uow = FakeUnitOfWork(
            FakePaymentRepository(), FakeGrantRepository(), FakeCallbackRepository()
        )
        use_case = GetServiceAccessStatusUseCase(uow)
        pay = _settled_payment()
        await uow.payments.add(pay)
        uow.grants.seed(ServiceAccessGrant.from_payment(pay, now=NOW, grant_id=new_id()))

        with pytest.raises(ResourceNotFound):
            await use_case.execute(
                ServiceAccessQuery(property_id=LISTING_ID, tenant_id=uuid.uuid4()), now=NOW
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