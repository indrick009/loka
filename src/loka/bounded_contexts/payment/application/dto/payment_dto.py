"""Payment context response DTOs.

Thin immutable projections for the HTTP layer; they never leak ORM rows or
aggregates upwards.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import for typing only
    from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
        ServiceFeePayment,
    )


class PaymentStatusView:
    __slots__ = (
        "amount_xaf",
        "attempt_count",
        "checkout_url",
        "currency",
        "expires_at",
        "paid_at",
        "payment_id",
        "provider",
        "provider_reference",
        "purpose",
        "settled",
        "status",
    )

    def __init__(
        self,
        *,
        payment_id: str,
        status: str,
        amount_xaf: int,
        currency: str,
        purpose: str,
        provider: str | None,
        provider_reference: str | None,
        attempt_count: int,
        expires_at: datetime,
        paid_at: datetime | None,
        settled: bool,
        checkout_url: str | None = None,
    ) -> None:
        self.payment_id = payment_id
        self.status = status
        self.amount_xaf = amount_xaf
        self.currency = currency
        self.purpose = purpose
        self.provider = provider
        self.provider_reference = provider_reference
        self.attempt_count = attempt_count
        self.expires_at = expires_at
        self.paid_at = paid_at
        self.settled = settled
        self.checkout_url = checkout_url

    @classmethod
    def of(cls, payment: ServiceFeePayment) -> PaymentStatusView:
        """The single place a payment becomes a response.

        Three use cases answered the same question with three copies of this
        mapping, so a field added for one of them was missing from the other
        two. There is nothing to choose between them: a payment projects one
        way.
        """
        return cls(
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
            checkout_url=payment.provider_checkout_url,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "payment_id": self.payment_id,
            "status": self.status,
            "amount_xaf": self.amount_xaf,
            "currency": self.currency,
            "purpose": self.purpose,
            "provider": self.provider,
            "provider_reference": self.provider_reference,
            "attempt_count": self.attempt_count,
            "expires_at": self.expires_at.isoformat(),
            "paid_at": self.paid_at.isoformat() if self.paid_at else None,
            "settled": self.settled,
            "checkout_url": self.checkout_url,
        }


class ServiceAccessStatusView:
    __slots__ = (
        "expires_at",
        "grant_id",
        "granted_at",
        "reveal_count",
        "revoked_at",
        "status",
    )

    def __init__(
        self,
        *,
        grant_id: str,
        status: str,
        reveal_count: int,
        granted_at: datetime,
        expires_at: datetime,
        revoked_at: datetime | None,
    ) -> None:
        self.grant_id = grant_id
        self.status = status
        self.reveal_count = reveal_count
        self.granted_at = granted_at
        self.expires_at = expires_at
        self.revoked_at = revoked_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "grant_id": self.grant_id,
            "status": self.status,
            "reveal_count": self.reveal_count,
            "granted_at": self.granted_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
        }