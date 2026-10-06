"""Mock gateway: wire contract a real provider must also satisfy."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.payment.infrastructure.gateways.mock_gateway import (
    MockPaymentGateway,
)
from loka.shared.application.ports import SignatureVerifier

SECRET = "test-webhook-secret"


def _signed_body(payload: dict, *, secret: str = SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    return body, SignatureVerifier.sign(body, secret)


def _payload(*, status: str = "SUCCEEDED", reference: str = "mock_x") -> dict:
    return {
        "event": {"id": "evt-1", "type": "payment.callback"},
        "data": {
            "provider_reference": reference,
            "status": status,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }


@pytest.mark.asyncio
async def test_the_same_payment_always_yields_the_same_reference() -> None:
    gateway = MockPaymentGateway(webhook_secret=SECRET)
    payment_id = uuid.uuid4()

    first = await gateway.initiate(payment_id=str(payment_id), amount_xaf=1000)
    second = await gateway.initiate(payment_id=str(payment_id), amount_xaf=1000)

    assert first.provider == "mock"
    assert first.provider_reference == second.provider_reference
    assert first.provider_reference.startswith("mock_")


def test_a_validly_signed_success_callback_is_trusted() -> None:
    gateway = MockPaymentGateway(webhook_secret=SECRET)
    body, signature = _signed_body(_payload(status="SUCCEEDED", reference="mock_x"))

    result = gateway.verify_callback(raw_body=body, signature=signature, headers={})

    assert result.accepted is True
    assert result.status == "SUCCEEDED"
    assert result.provider_reference == "mock_x"
    assert result.rejection_reason is None


def test_a_validly_signed_failure_callback_is_accepted_but_failed() -> None:
    gateway = MockPaymentGateway(webhook_secret=SECRET)
    body, signature = _signed_body(_payload(status="FAILED", reference="mock_x"))

    result = gateway.verify_callback(raw_body=body, signature=signature, headers={})

    assert result.accepted is True
    assert result.status == "FAILED"


def test_a_bad_signature_is_rejected_before_looking_at_the_payload() -> None:
    gateway = MockPaymentGateway(webhook_secret=SECRET)
    body, _ = _signed_body(_payload())

    result = gateway.verify_callback(
        raw_body=body, signature="deadbeef" * 8, headers={}
    )

    assert result.accepted is False
    assert "signature" in (result.rejection_reason or "")


def test_a_signature_using_the_wrong_secret_is_rejected() -> None:
    body, _ = _signed_body(_payload(), secret="another-secret")
    gateway = MockPaymentGateway(webhook_secret=SECRET)

    result = gateway.verify_callback(
        raw_body=body, signature=SignatureVerifier.sign(body, "another-secret"), headers={}
    )

    assert result.accepted is False


def test_an_unknown_status_is_invalid_even_with_a_good_signature() -> None:
    gateway = MockPaymentGateway(webhook_secret=SECRET)
    body, signature = _signed_body(_payload(status="REFUNDED"))

    result = gateway.verify_callback(raw_body=body, signature=signature, headers={})

    assert result.accepted is False
    assert result.status == "INVALID"