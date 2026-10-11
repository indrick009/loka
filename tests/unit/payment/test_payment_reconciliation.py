"""Two ways a payment gets settled, and what each is allowed to believe.

Fapshi authenticates a webhook by echoing a shared secret in a header. That
proves who called; it proves nothing about the body, so a verdict pushed to us
is a prompt to go and look, not a fact. And Fapshi sends one webhook per event
and never retries, so a request lost to a deploy is a payer charged and never
served — in silence, with no provider-side retry coming to save it.

Hence the two paths tested here. The callback handler confirms what it was told
before settling anything. Reconciliation settles what it was never told at all.
Both ask the provider; neither trusts a body.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from tests.unit.payment.fakes import (
    NOW,
    FakeCallbackRepository,
    FakeGateway,
    FakeGrantRepository,
    FakePaymentRepository,
    FakeUnitOfWork,
    a_payment,
)

from loka.bounded_contexts.payment.application.ports import VerifiedCallback
from loka.bounded_contexts.payment.application.use_cases.handle_payment_callback import (
    HandlePaymentCallbackCommand,
    HandlePaymentCallbackUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.reconcile_pending_payments import (
    ReconcilePendingPaymentsCommand,
    ReconcilePendingPaymentsUseCase,
)
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    PaymentStatus,
    ServiceFeePayment,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited

pytestmark = pytest.mark.unit

REFERENCE = "tx_1"


class FakeStatusSource:
    """The provider, asked rather than believed."""

    def __init__(
        self,
        *,
        status: str = "SUCCEEDED",
        reported_at: datetime | None = NOW,
        error: Exception | None = None,
    ) -> None:
        self._status = status
        self._reported_at = reported_at
        self._error = error
        self.asked: list[str] = []

    async def fetch_status(self, *, provider_reference: str) -> VerifiedCallback:
        self.asked.append(provider_reference)
        if self._error is not None:
            raise self._error
        return VerifiedCallback(
            provider_event_id=f"{provider_reference}:{self._status}",
            provider_reference=provider_reference,
            accepted=True,
            status=self._status,
            provider_reported_at=self._reported_at,
            authoritative=True,
        )


def a_pending_payment(*, now: datetime = NOW, reference: str = REFERENCE) -> ServiceFeePayment:
    payment = a_payment(now=now)
    payment.initiate(provider="fapshi", provider_reference=reference, now=now)
    return payment


def a_unit_of_work(*payments: ServiceFeePayment) -> FakeUnitOfWork:
    uow = FakeUnitOfWork(FakePaymentRepository(), FakeGrantRepository(), FakeCallbackRepository())
    for payment in payments:
        uow.payments.store[payment.id] = payment
    return uow


def a_webhook(status: str = "SUCCEEDED") -> HandlePaymentCallbackCommand:
    return HandlePaymentCallbackCommand(
        provider="fapshi",
        provider_event_id="",
        raw_body=json.dumps({"transId": REFERENCE, "status": status}).encode(),
        signature="",
        headers={"x-wh-secret": "secret"},
    )


def announcing(status: str, *, authoritative: bool = False) -> FakeGateway:
    return FakeGateway(
        provider_reference=REFERENCE,
        verification=VerifiedCallback(
            provider_event_id=f"{REFERENCE}:{status}",
            provider_reference=REFERENCE,
            accepted=True,
            status=status,
            provider_reported_at=NOW,
            audit_digest="body-digest",
            authoritative=authoritative,
        ),
    )


class TestAWebhookIsConfirmedBeforeItSettles:
    @pytest.mark.asyncio
    async def test_the_provider_is_asked_about_the_transaction_it_named(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)
        source = FakeStatusSource(status="SUCCEEDED")

        await HandlePaymentCallbackUseCase(
            uow, gateway=announcing("SUCCEEDED"), status_source=source
        ).execute(a_webhook(), now=NOW)

        assert source.asked == [REFERENCE]
        assert payment.status is PaymentStatus.SUCCEEDED

    @pytest.mark.asyncio
    async def test_a_body_claiming_success_cannot_settle_a_payment_the_provider_calls_pending(
        self,
    ) -> None:
        """The attack the shared-secret scheme leaves open.

        Whoever learns the header value can post ``status: SUCCESSFUL`` for any
        transaction id. Only the provider's own answer settles anything, so the
        forged body buys nothing.
        """
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        await HandlePaymentCallbackUseCase(
            uow,
            gateway=announcing("SUCCEEDED"),
            status_source=FakeStatusSource(status="PENDING"),
        ).execute(a_webhook(), now=NOW)

        assert payment.status is PaymentStatus.PENDING_CONFIRMATION
        assert uow.grants.added == []

    @pytest.mark.asyncio
    async def test_a_provider_that_cannot_be_reached_leaves_no_dedup_record(self) -> None:
        """Otherwise the retry that would have worked is filed as a replay."""
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)
        source = FakeStatusSource(error=ExternalServiceUnavailable("unreachable"))

        with pytest.raises(ExternalServiceUnavailable):
            await HandlePaymentCallbackUseCase(
                uow, gateway=announcing("SUCCEEDED"), status_source=source
            ).execute(a_webhook(), now=NOW)

        assert uow.callbacks.store == {}
        assert payment.status is PaymentStatus.PENDING_CONFIRMATION

    @pytest.mark.asyncio
    async def test_the_secret_header_is_never_what_gets_stored(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        await HandlePaymentCallbackUseCase(
            uow, gateway=announcing("SUCCEEDED"), status_source=FakeStatusSource()
        ).execute(a_webhook(), now=NOW)

        recorded = next(iter(uow.callbacks.store.values()))
        assert recorded.payload_signature == "body-digest"

    @pytest.mark.asyncio
    async def test_a_gateway_with_no_status_endpoint_settles_on_its_own_signature(
        self,
    ) -> None:
        """Nothing to confirm: a signed body has already proved what it says."""
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        await HandlePaymentCallbackUseCase(
            uow, gateway=announcing("SUCCEEDED"), status_source=None
        ).execute(a_webhook(), now=NOW)

        assert payment.status is PaymentStatus.SUCCEEDED


class TestTheStatusesAWebhookCanCarry:
    @pytest.mark.asyncio
    async def test_an_expired_link_expires_the_payment_rather_than_settling_it(
        self,
    ) -> None:
        """The transition that was missing: anything not FAILED used to succeed."""
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        await HandlePaymentCallbackUseCase(
            uow,
            gateway=announcing("EXPIRED"),
            status_source=FakeStatusSource(status="EXPIRED"),
        ).execute(a_webhook("EXPIRED"), now=NOW)

        assert payment.status is PaymentStatus.EXPIRED
        assert uow.grants.added == []

    @pytest.mark.asyncio
    async def test_a_failure_fails_the_payment(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        await HandlePaymentCallbackUseCase(
            uow,
            gateway=announcing("FAILED"),
            status_source=FakeStatusSource(status="FAILED"),
        ).execute(a_webhook("FAILED"), now=NOW)

        assert payment.status is PaymentStatus.FAILED

    @pytest.mark.asyncio
    async def test_a_pending_notification_changes_nothing(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        await HandlePaymentCallbackUseCase(
            uow,
            gateway=announcing("PENDING"),
            status_source=FakeStatusSource(status="PENDING"),
        ).execute(a_webhook("PENDING"), now=NOW)

        assert payment.status is PaymentStatus.PENDING_CONFIRMATION


class TestSettlingAPaymentNobodyToldUsAbout:
    """Reconciliation: the only thing standing between a lost webhook and a
    payer who paid for nothing."""

    def command(self, **overrides: object) -> ReconcilePendingPaymentsCommand:
        kwargs: dict[str, object] = {
            "provider": "fapshi",
            "older_than": timedelta(minutes=10),
            "limit": 50,
        }
        kwargs.update(overrides)
        return ReconcilePendingPaymentsCommand(**kwargs)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_a_payment_confirmed_an_hour_ago_is_still_granted(self) -> None:
        """The case the five-minute callback window would have refused.

        The window stops a captured webhook being replayed days later. A status
        read from the provider is not a message and cannot be replayed, so the
        window does not apply to it — and if it did, every late payment would
        stay unpaid forever.
        """
        paid_at = NOW
        later = NOW + timedelta(hours=1)
        payment = a_pending_payment(now=paid_at)
        uow = a_unit_of_work(payment)

        report = await ReconcilePendingPaymentsUseCase(
            uow, status_source=FakeStatusSource(status="SUCCEEDED", reported_at=paid_at)
        ).execute(self.command(), now=later)

        assert report.settled == 1
        assert payment.status is PaymentStatus.SUCCEEDED
        assert len(uow.grants.added) == 1

    @pytest.mark.asyncio
    async def test_a_payment_still_in_flight_is_left_alone(self) -> None:
        """Younger than the window: the webhook is probably still arriving."""
        payment = a_pending_payment(now=NOW)
        uow = a_unit_of_work(payment)
        source = FakeStatusSource()

        report = await ReconcilePendingPaymentsUseCase(
            uow, status_source=source
        ).execute(self.command(), now=NOW + timedelta(minutes=2))

        assert report.examined == 0
        assert source.asked == []

    @pytest.mark.asyncio
    async def test_a_second_pass_does_not_grant_twice(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)
        later = NOW + timedelta(hours=1)
        use_case = ReconcilePendingPaymentsUseCase(
            uow, status_source=FakeStatusSource(status="SUCCEEDED")
        )

        await use_case.execute(self.command(), now=later)
        second = await use_case.execute(self.command(), now=later)

        assert second.examined == 0
        assert len(uow.grants.added) == 1

    @pytest.mark.asyncio
    async def test_an_unreachable_transaction_does_not_abandon_the_rest(self) -> None:
        """One payer's provider error must not cost every later payer their grant."""
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        report = await ReconcilePendingPaymentsUseCase(
            uow, status_source=FakeStatusSource(error=ExternalServiceUnavailable("down"))
        ).execute(self.command(), now=NOW + timedelta(hours=1))

        assert report.unreachable == 1
        assert payment.status is PaymentStatus.PENDING_CONFIRMATION

    @pytest.mark.asyncio
    async def test_a_rate_limit_stops_the_pass(self) -> None:
        """Six reads a minute per transaction: pushing through earns 429s only."""
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        with pytest.raises(RateLimited):
            await ReconcilePendingPaymentsUseCase(
                uow, status_source=FakeStatusSource(error=RateLimited("slow down"))
            ).execute(self.command(), now=NOW + timedelta(hours=1))

    @pytest.mark.asyncio
    async def test_an_expired_link_is_closed_out(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        report = await ReconcilePendingPaymentsUseCase(
            uow, status_source=FakeStatusSource(status="EXPIRED")
        ).execute(self.command(), now=NOW + timedelta(hours=1))

        assert report.expired == 1
        assert payment.status is PaymentStatus.EXPIRED

    @pytest.mark.asyncio
    async def test_a_payment_the_provider_still_calls_pending_stays_pending(self) -> None:
        payment = a_pending_payment()
        uow = a_unit_of_work(payment)

        report = await ReconcilePendingPaymentsUseCase(
            uow, status_source=FakeStatusSource(status="PENDING")
        ).execute(self.command(), now=NOW + timedelta(hours=1))

        assert report.still_pending == 1
        assert payment.status is PaymentStatus.PENDING_CONFIRMATION

    @pytest.mark.asyncio
    async def test_another_providers_payments_are_not_touched(self) -> None:
        payment = a_payment(now=NOW)
        payment.initiate(provider="mock", provider_reference="mock_ref", now=NOW)
        uow = a_unit_of_work(payment)
        source = FakeStatusSource()

        report = await ReconcilePendingPaymentsUseCase(
            uow, status_source=source
        ).execute(self.command(provider="fapshi"), now=NOW + timedelta(hours=1))

        assert report.examined == 0
        assert source.asked == []


class TestTheLinkLifetimeComesFromTheProvider:
    def test_a_provider_that_states_its_window_replaces_the_platform_guess(self) -> None:
        payment = a_payment(now=NOW)

        payment.initiate(
            provider="fapshi",
            provider_reference=REFERENCE,
            now=NOW,
            checkout_url="https://checkout.fapshi.com/pay/tx_1",
            link_ttl_seconds=86_400,
        )

        assert payment.expires_at == NOW + timedelta(hours=24)
        assert payment.provider_checkout_url == "https://checkout.fapshi.com/pay/tx_1"

    def test_a_silent_provider_keeps_the_half_hour(self) -> None:
        payment = a_payment(now=NOW)

        payment.initiate(provider="mock", provider_reference="mock_ref", now=NOW)

        assert payment.expires_at == NOW + timedelta(minutes=30)

    def test_the_link_survives_a_round_trip_through_the_view(self) -> None:
        """A payer who lost the message can only be sent the same link again."""
        from loka.bounded_contexts.payment.application.dto.payment_dto import (
            PaymentStatusView,
        )

        payment = a_payment(now=NOW)
        payment.initiate(
            provider="fapshi",
            provider_reference=REFERENCE,
            now=NOW,
            checkout_url="https://checkout.fapshi.com/pay/tx_1",
        )

        view = PaymentStatusView.of(payment)

        assert view.to_dict()["checkout_url"] == "https://checkout.fapshi.com/pay/tx_1"
