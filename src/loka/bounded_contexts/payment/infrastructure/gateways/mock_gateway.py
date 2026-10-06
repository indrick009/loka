"""Mock payment gateway.

Used for local development and the integration suite. It speaks the same wire
contract as a real provider — an initiate call returns a reference, callbacks
arrive signed with the configured webhook secret and must survive
:class:`HmacSha256SignatureVerifier` — so swapping in a live provider is a
composition change, not a code change.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.payment.application.ports import (
    PaymentInitiation,
    VerifiedCallback,
)
from loka.shared.application.ports import (
    HmacSha256SignatureVerifier,
    SignatureVerifier,
)


class MockPaymentGateway:
    """Deterministic provider double: same payment yields the same reference."""

    name_id = "mock"

    def __init__(
        self,
        *,
        webhook_secret: str = "change-me",
        verifier: SignatureVerifier | None = None,
    ) -> None:
        self._webhook_secret = webhook_secret
        self._verifier = verifier or HmacSha256SignatureVerifier()

    def name(self) -> str:
        return self.name_id

    async def initiate(
        self, *, payment_id: str, amount_xaf: int, **kwargs: Any
    ) -> PaymentInitiation:
        reference = "mock_" + uuid.uuid5(uuid.NAMESPACE_URL, f"loka/{payment_id}").hex
        return PaymentInitiation(provider=self.name_id, provider_reference=reference)

    def verify_callback(
        self, *, raw_body: bytes, signature: str, headers: dict[str, str]
    ) -> VerifiedCallback:
        if not self._verifier.verify(raw_body, signature, secret=self._webhook_secret):
            return VerifiedCallback(
                provider_event_id="",
                provider_reference="",
                accepted=False,
                status="REJECTED",
                rejection_reason="signature verification failed",
            )
        payload = json.loads(raw_body)
        event = payload.get("event") or {}
        provider_event_id = str(event.get("id") or "")
        status = str((payload.get("data") or {}).get("status") or "SUCCEEDED").upper()
        reported = _parse_timestamp((payload.get("data") or {}).get("timestamp"))
        if status not in ("SUCCEEDED", "FAILED"):
            return VerifiedCallback(
                provider_event_id=provider_event_id,
                provider_reference="",
                accepted=False,
                status="INVALID",
                rejection_reason="unknown status reported by gateway",
                provider_reported_at=reported,
            )
        payment_id_value = (payload.get("data") or {}).get("payment_id")
        return VerifiedCallback(
            provider_event_id=provider_event_id,
            provider_reference=str(
                (payload.get("data") or {}).get("provider_reference") or ""
            ),
            accepted=True,
            status=status,
            provider_reported_at=reported,
            payment_id=str(payment_id_value) if payment_id_value else None,
        )


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed