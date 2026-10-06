"""ServiceFeePayment state machine and access-grant invariants."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    CALLBACK_TOLERANCE_SECONDS,
    GRANT_TTL_DAYS,
    GrantStatus,
    PaymentPurpose,
    PaymentStatus,
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.errors import InvalidStateTransition

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
REFERENCE = "provider_ref_1"


def payment(**overrides: object) -> ServiceFeePayment:
    kwargs = {
        "payment_id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "landlord_id": uuid.uuid4(),
        "application_id": uuid.uuid4(),
        "amount": Money(1000),
        "purpose": PaymentPurpose.SERVICE_ACCESS,
        "now": NOW,
    }
    kwargs.update(overrides)
    return ServiceFeePayment(**kwargs)  # type: ignore[arg-type]


class TestInitiate:
    def test_a_created_payment_becomes_pending_and_counts_an_attempt(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        assert pay.status is PaymentStatus.PENDING_CONFIRMATION
        assert pay.attempt_count == 1
        assert pay.provider == "mock"
        assert pay.provider_reference == REFERENCE
        assert pay.expires_at == NOW + timedelta(minutes=30)
        assert pay.is_terminal is False
        assert pay.is_settled is False
        assert [event.event_type for event in pay.pull_events()] == ["PaymentInitiated"]

    def test_a_failed_payment_can_be_reinitiated_with_a_bumped_attempt(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_failed(reason="provider declined", now=NOW + timedelta(minutes=1))
        pay.initiate(provider="mock", provider_reference="mock_bis", now=NOW + timedelta(minutes=2))
        assert pay.status is PaymentStatus.PENDING_CONFIRMATION
        assert pay.attempt_count == 2
        assert pay.provider_reference == "mock_bis"
        assert pay.failure_reason is None

    def test_an_open_payment_cannot_be_initiated_again(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        with pytest.raises(InvalidStateTransition):
            pay.initiate(provider="mock", provider_reference="mock_bis", now=NOW)

    def test_a_settled_payment_cannot_be_initiated(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
        )
        with pytest.raises(InvalidStateTransition):
            pay.initiate(provider="mock", provider_reference="mock_bis", now=NOW)


class TestSucceeded:
    def test_a_verified_callback_settles_the_payment_and_emits_the_event(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig-1", now=NOW, provider_reported_at=NOW
        )
        assert pay.status is PaymentStatus.SUCCEEDED
        assert pay.is_settled is True
        assert pay.callback_signature == "sig-1"
        assert pay.paid_at == NOW
        event_types = [event.event_type for event in pay.pull_events()]
        assert "PaymentSucceeded" in event_types

    def test_replaying_the_same_callback_is_a_noop(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig-1", now=NOW, provider_reported_at=NOW
        )
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig-1", now=NOW, provider_reported_at=NOW
        )
        assert pay.attempt_count == 1
        assert [event.event_type for event in pay.pull_events()] == [
            "PaymentInitiated",
            "PaymentSucceeded",
        ]

    def test_a_different_reference_on_replay_is_refused(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
        )
        with pytest.raises(InvalidStateTransition):
            pay.mark_succeeded(
                provider_reference="other", signature="sig", now=NOW, provider_reported_at=NOW
            )

    def test_a_payment_must_be_pending_before_it_can_complete(self) -> None:
        pay = payment()
        with pytest.raises(InvalidStateTransition):
            pay.mark_succeeded(
                provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
            )

    def test_a_failed_payment_cannot_then_succeed(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_failed(reason="declined", now=NOW)
        with pytest.raises(InvalidStateTransition):
            pay.mark_succeeded(
                provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
            )

    def test_a_stale_callback_is_rejected(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        stale = NOW + timedelta(seconds=CALLBACK_TOLERANCE_SECONDS + 1)
        with pytest.raises(InvalidStateTransition):
            pay.mark_succeeded(
                provider_reference=REFERENCE,
                signature="sig",
                now=stale,
                provider_reported_at=NOW,
            )


class TestFailed:
    def test_failure_records_the_reason_and_emits_the_event(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_failed(reason="insufficient funds", now=NOW)
        assert pay.status is PaymentStatus.FAILED
        assert pay.failure_reason == "insufficient funds"
        assert [event.event_type for event in pay.pull_events()] == [
            "PaymentInitiated",
            "PaymentFailed",
        ]

    def test_a_settled_payment_cannot_fail(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
        )
        with pytest.raises(InvalidStateTransition):
            pay.mark_failed(reason="late", now=NOW)


class TestOtherTransitions:
    def test_expire_is_idempotent_on_terminal_states(self) -> None:
        pay = payment()
        pay.expire(now=NOW)
        assert pay.status is PaymentStatus.EXPIRED
        pay.expire(now=NOW)  # no-op, must not raise

    def test_refund_only_from_succeeded(self) -> None:
        pay = payment()
        with pytest.raises(InvalidStateTransition):
            pay.refund(reason="seller fault", now=NOW)

    def test_cancel_only_when_not_terminal(self) -> None:
        pay = payment()
        pay.expire(now=NOW)
        with pytest.raises(InvalidStateTransition):
            pay.cancel(reason="no longer needed", now=NOW)


class TestAccessGrant:
    def test_grant_is_born_from_a_settled_payment_only(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        with pytest.raises(InvalidStateTransition):
            ServiceAccessGrant.from_payment(pay, now=NOW, grant_id=uuid.uuid4())

    def test_grant_lifetime_is_the_configured_ttl(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
        )
        grant = ServiceAccessGrant.from_payment(pay, now=NOW, grant_id=uuid.uuid4())
        assert grant.status is GrantStatus.ACTIVE
        assert grant.expires_at == NOW + timedelta(days=GRANT_TTL_DAYS)
        assert grant.payment_id == pay.id

    def test_reveal_requires_an_active_grant(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
        )
        grant = ServiceAccessGrant.from_payment(pay, now=NOW, grant_id=uuid.uuid4())
        grant.expire(now=NOW + timedelta(days=GRANT_TTL_DAYS))
        with pytest.raises(InvalidStateTransition):
            grant.record_reveal(now=NOW + timedelta(days=GRANT_TTL_DAYS + 1))

    def test_reveal_increments_the_counter_and_emits_the_event(self) -> None:
        pay = payment()
        pay.initiate(provider="mock", provider_reference=REFERENCE, now=NOW)
        pay.mark_succeeded(
            provider_reference=REFERENCE, signature="sig", now=NOW, provider_reported_at=NOW
        )
        grant = ServiceAccessGrant.from_payment(pay, now=NOW, grant_id=uuid.uuid4())
        grant.record_reveal(now=NOW)
        assert grant.reveal_count == 1
        assert [
            event.event_type for event in grant.pull_events()
        ] == ["ContactInformationUnlocked"]