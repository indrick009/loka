"""Service fee payment aggregate.

The single most important invariant in this context: a payment being
*initiated* is not a payment being *completed*. Contact details unlock only
from a ``SUCCEEDED`` state reached through a verified gateway callback.

The unlock is anchored on **(tenant, listing)**, not on an application: the
1 000 FCFA buys the landlord's number *and* the lodging's location, and a
location belongs to one listing. So the same tenant returning to the same
listing owes nothing, a second listing from the same landlord is a second
unlock, and another tenant wanting that listing pays his own — the grant is
his, not the listing's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition

CALLBACK_TOLERANCE_SECONDS = 300

# How long a payment handle lives when the provider states no window of its
# own. Overridden per initiation by providers that do: Fapshi keeps a payment
# link payable for 24 hours.
DEFAULT_LINK_TTL_SECONDS = 1800
GRANT_TTL_DAYS = 30


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    INITIATED = "INITIATED"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


TERMINAL_STATUSES = frozenset(
    {
        PaymentStatus.SUCCEEDED,
        PaymentStatus.FAILED,
        PaymentStatus.EXPIRED,
        PaymentStatus.REFUNDED,
        PaymentStatus.CANCELLED,
    }
)


class PaymentMethod(StrEnum):
    MOBILE_MONEY = "MOBILE_MONEY"
    BANK_TRANSFER = "BANK_TRANSFER"
    CARD = "CARD"


class PaymentPurpose(StrEnum):
    SERVICE_ACCESS = "SERVICE_ACCESS"
    DEPOSIT = "DEPOSIT"


class ServiceFeePayment(AggregateRoot):
    __slots__ = (
        "amount",
        "attempt_count",
        "callback_signature",
        "created_at",
        "expires_at",
        "failure_reason",
        "idempotency_key",
        "landlord_id",
        "method",
        "paid_at",
        "payment_id",
        "property_id",
        "provider",
        "provider_checkout_url",
        "provider_reference",
        "purpose",
        "status",
        "tenant_id",
        "updated_at",
    )

    def __init__(
        self,
        *,
        payment_id: uuid.UUID,
        tenant_id: uuid.UUID,
        landlord_id: uuid.UUID,
        property_id: uuid.UUID,
        amount: Money,
        purpose: PaymentPurpose,
        now: datetime,
        idempotency_key: str | None = None,
    ) -> None:
        super().__init__(aggregate_type="ServiceFeePayment")
        self._assign_id(payment_id)
        self.payment_id = payment_id
        self.tenant_id = tenant_id
        self.landlord_id = landlord_id
        self.property_id = property_id
        self.amount = amount.require_positive(reason="payment amount must be positive")
        self.purpose = purpose
        self.method = PaymentMethod.MOBILE_MONEY
        self.status = PaymentStatus.CREATED
        self.provider: str | None = None
        self.provider_reference: str | None = None
        # The page the payer actually opens. Stored, not recomputed: a hosted
        # checkout URL is the provider's to mint, so a payer who lost the
        # message can only be sent the same link again if we kept it.
        self.provider_checkout_url: str | None = None
        self.idempotency_key = idempotency_key
        self.callback_signature: str | None = None
        self.paid_at: datetime | None = None
        self.failure_reason: str | None = None
        self.attempt_count = 0
        self.expires_at = ensure_utc(now) + timedelta(seconds=DEFAULT_LINK_TTL_SECONDS)
        self.created_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def is_settled(self) -> bool:
        """The only state that authorises unlocking contact details."""
        return self.status is PaymentStatus.SUCCEEDED

    def initiate(
        self,
        *,
        provider: str,
        provider_reference: str,
        now: datetime,
        checkout_url: str | None = None,
        link_ttl_seconds: int | None = None,
    ) -> None:
        """Bind this payment to a provider handle.

        ``link_ttl_seconds`` is how long that provider will keep accepting
        money. When it says nothing, the platform's own half-hour applies; when
        it does, its window wins, because telling a payer their link expired
        while the provider would still honour it costs a sale for nothing.
        """
        if self.status not in (PaymentStatus.CREATED, PaymentStatus.FAILED):
            raise InvalidStateTransition(
                f"cannot initiate a payment in state {self.status.value}",
                context={"payment_id": str(self.id)},
            )
        self.provider = provider
        self.provider_reference = provider_reference
        self.provider_checkout_url = checkout_url
        self.status = PaymentStatus.PENDING_CONFIRMATION
        self.attempt_count += 1
        self.expires_at = ensure_utc(now) + timedelta(
            seconds=link_ttl_seconds or DEFAULT_LINK_TTL_SECONDS
        )
        self.failure_reason = None
        self._touch(now)
        self.record(
            "PaymentInitiated",
            occurred_at=now,
            payload={
                "payment_id": str(self.id),
                "provider": provider,
                "provider_reference": provider_reference,
                "amount": self.amount.amount,
            },
        )

    def mark_succeeded(
        self,
        *,
        provider_reference: str,
        signature: str,
        now: datetime,
        provider_reported_at: datetime | None = None,
        tolerance_seconds: int | None = CALLBACK_TOLERANCE_SECONDS,
    ) -> ServiceFeePayment:
        """Apply a verified gateway callback.

        Idempotent: replaying the same callback is a no-op, which is what makes
        duplicate webhook delivery safe.

        ``tolerance_seconds`` is how stale a reported payment time may be. The
        window exists so a captured callback cannot be replayed days later to
        unlock a listing for free. ``None`` disables it, and is only correct
        when the caller fetched this verdict from the provider itself: then the
        age of the message proves nothing, because there was no message —
        which is exactly the case when reconciliation finds a payment made an
        hour ago and still owed its grant.
        """
        if self.status is PaymentStatus.SUCCEEDED:
            if self.provider_reference != provider_reference:
                raise InvalidStateTransition(
                    "payment already succeeded with a different provider reference"
                )
            return self
        if self.is_terminal:
            raise InvalidStateTransition(
                f"cannot complete a payment in state {self.status.value}",
                context={"payment_id": str(self.id), "status": self.status.value},
            )
        if self.status is not PaymentStatus.PENDING_CONFIRMATION:
            raise InvalidStateTransition("payment must be initiated before it can complete")

        reported = ensure_utc(provider_reported_at or now)
        if (
            tolerance_seconds is not None
            and abs((ensure_utc(now) - reported).total_seconds()) > tolerance_seconds
        ):
            raise InvalidStateTransition(
                "callback timestamp is outside the tolerance window",
                context={"now": now.isoformat(), "provider_reported_at": reported.isoformat()},
            )

        self.status = PaymentStatus.SUCCEEDED
        self.provider_reference = provider_reference
        self.callback_signature = signature
        self.paid_at = reported
        self._touch(now)
        self.record(
            "PaymentSucceeded",
            occurred_at=now,
            payload={
                "payment_id": str(self.id),
                "tenant_id": str(self.tenant_id),
                "landlord_id": str(self.landlord_id),
                "property_id": str(self.property_id),
                "amount": self.amount.amount,
                "currency": self.amount.currency,
            },
        )
        return self

    def mark_failed(self, *, reason: str, now: datetime) -> None:
        if self.status is PaymentStatus.SUCCEEDED:
            raise InvalidStateTransition("a settled payment cannot fail")
        self.status = PaymentStatus.FAILED
        self.failure_reason = reason[:500]
        self._touch(now)
        self.record(
            "PaymentFailed",
            occurred_at=now,
            payload={"payment_id": str(self.id), "reason": self.failure_reason},
        )

    def expire(self, *, now: datetime) -> None:
        if self.is_terminal:
            return
        self.status = PaymentStatus.EXPIRED
        self._touch(now)
        self.record("PaymentExpired", occurred_at=now, payload={"payment_id": str(self.id)})

    def refund(self, *, reason: str, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is not PaymentStatus.SUCCEEDED:
            raise InvalidStateTransition("only a settled payment can be refunded")
        self.status = PaymentStatus.REFUNDED
        self.failure_reason = reason[:500]
        self._touch(now)
        self.record(
            "PaymentRefunded",
            occurred_at=now,
            actor_id=actor_id,
            payload={"payment_id": str(self.id), "reason": reason},
        )

    def cancel(self, *, reason: str, now: datetime) -> None:
        if self.is_terminal:
            raise InvalidStateTransition("a terminal payment cannot be cancelled")
        self.status = PaymentStatus.CANCELLED
        self.failure_reason = reason[:500]
        self._touch(now)
        self.record(
            "PaymentCancelled", occurred_at=now, payload={"payment_id": str(self.id)}
        )

    def assert_settled(self, *, action: str) -> None:
        if not self.is_settled:
            raise InvalidStateTransition(
                f"{action} requires a settled payment, current state is {self.status.value}",
                context={"payment_id": str(self.id), "status": self.status.value},
            )

    def _touch(self, now: datetime) -> None:
        self.updated_at = ensure_utc(now)
        self._bump()


class GrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ServiceAccessGrant(AggregateRoot):
    """Proof that this tenant paid to unlock this listing.

    Carries both halves of the pair, because both are needed to answer the only
    question the grant exists to answer: may *this* person see the number and
    the location of *this* lodging?
    """

    __slots__ = (
        "expires_at",
        "grant_id",
        "granted_at",
        "landlord_id",
        "payment_id",
        "property_id",
        "reveal_count",
        "revocation_reason",
        "revoked_at",
        "status",
        "tenant_id",
    )

    def __init__(
        self,
        *,
        grant_id: uuid.UUID,
        tenant_id: uuid.UUID,
        landlord_id: uuid.UUID,
        property_id: uuid.UUID,
        payment_id: uuid.UUID,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="ServiceAccessGrant")
        self._assign_id(grant_id)
        self.grant_id = grant_id
        self.tenant_id = tenant_id
        self.landlord_id = landlord_id
        self.property_id = property_id
        self.payment_id = payment_id
        self.status = GrantStatus.ACTIVE
        self.granted_at = ensure_utc(now)
        self.expires_at = ensure_utc(now) + timedelta(days=GRANT_TTL_DAYS)
        self.revoked_at: datetime | None = None
        self.revocation_reason: str | None = None
        self.reveal_count = 0

    @classmethod
    def from_payment(
        cls, payment: ServiceFeePayment, *, now: datetime, grant_id: uuid.UUID
    ) -> ServiceAccessGrant:
        """Only reachable from a settled payment."""
        payment.assert_settled(action="creating an access grant")
        return cls(
            grant_id=grant_id,
            tenant_id=payment.tenant_id,
            landlord_id=payment.landlord_id,
            property_id=payment.property_id,
            payment_id=payment.id,
            now=now,
        )

    @property
    def is_active(self) -> bool:
        return self.status is GrantStatus.ACTIVE

    def assert_revealable(self, *, now: datetime) -> None:
        if self.status is GrantStatus.REVOKED:
            raise InvalidStateTransition("access grant has been revoked")
        if self.status is GrantStatus.EXPIRED or ensure_utc(now) >= self.expires_at:
            raise InvalidStateTransition(
                "access grant has expired",
                context={"grant_id": str(self.id)},
            )

    def record_reveal(self, *, now: datetime) -> None:
        self.assert_revealable(now=now)
        self.reveal_count += 1
        self.record(
            "ContactInformationUnlocked",
            occurred_at=now,
            payload={
                "grant_id": str(self.id),
                "tenant_id": str(self.tenant_id),
                "landlord_id": str(self.landlord_id),
                "property_id": str(self.property_id),
                "reveal_count": self.reveal_count,
            },
        )

    def expire(self, *, now: datetime) -> None:
        if self.status is not GrantStatus.ACTIVE:
            return
        self.status = GrantStatus.EXPIRED
        self.record(
            "ServiceAccessGrantExpired",
            occurred_at=now,
            payload={"grant_id": str(self.id)},
        )

    def revoke(self, *, reason: str, now: datetime, actor_id: uuid.UUID | None = None) -> None:
        if self.status is GrantStatus.REVOKED:
            return
        self.status = GrantStatus.REVOKED
        self.revoked_at = ensure_utc(now)
        self.revocation_reason = reason[:500]
        self.record(
            "ServiceAccessGrantRevoked",
            occurred_at=now,
            actor_id=actor_id,
            payload={"grant_id": str(self.id), "reason": reason},
        )