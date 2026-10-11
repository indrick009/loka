"""Handle a verified provider payment callback.

A webhook arrives with raw bytes and a signature. The gateway adapter is the
only code that understands provider signing; this use case receives only the
already-verified verdict. Settlement is idempotent on replay: the callback
record's unique constraint, the aggregate's ``mark_succeeded`` guard and the
one-active-grant-per-(tenant, listing) partial index all make duplicate
delivery harmless.

Verification is not confirmation. Some providers sign the body, which proves
what they said; others, Fapshi among them, only echo a shared secret in a
header, which proves who called and nothing about what the body contains.
Where a :class:`PaymentStatusSource` exists, the announced status is therefore
replaced by the one fetched from the provider before anything settles, and the
webhook is downgraded to what it really is — a prompt to go and look.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import structlog

from loka.bounded_contexts.payment.application.dto.payment_dto import (
    PaymentStatusView,
    ServiceAccessStatusView,
)
from loka.bounded_contexts.payment.application.ports import (
    PaymentGateway,
    PaymentStatusSource,
    VerifiedCallback,
)
from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    CALLBACK_TOLERANCE_SECONDS,
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.exceptions import CallbackRejected
from loka.bounded_contexts.payment.domain.repositories import (
    AccessGrantRepository,
    PaymentCallbackRepository,
    PaymentRepository,
    RawCallbackRecord,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class HandlePaymentCallbackCommand:
    provider: str
    provider_event_id: str
    raw_body: bytes
    signature: str
    headers: dict[str, str]


@dataclass(frozen=True, slots=True)
class CallbackOutcome:
    payment: PaymentStatusView | None = None
    grant: ServiceAccessStatusView | None = None
    replayed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "replayed": self.replayed,
            "payment": self.payment.to_dict() if self.payment else None,
            "grant": self.grant.to_dict() if self.grant else None,
        }


class HandlePaymentCallbackUseCase:
    name = "handle_payment_callback"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        gateway: PaymentGateway,
        status_source: PaymentStatusSource | None = None,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._gateway = gateway
        self._status_source = status_source
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: HandlePaymentCallbackCommand, *, now: datetime
    ) -> CallbackOutcome:
        verified = self._gateway.verify_callback(
            raw_body=command.raw_body,
            signature=command.signature,
            headers=command.headers,
        )
        if not verified.accepted:
            raise CallbackRejected(
                verified.rejection_reason or "provider callback signature is invalid",
                context={
                    "provider": command.provider,
                    "provider_event_id": command.provider_event_id,
                },
            )

        callbacks: PaymentCallbackRepository = self._uow.repository("payment_callback")

        # Check-then-insert is safe here: `add` returns False on the race where
        # another worker already recorded this exact event, which turns that bet
        # into a no-op replay instead of a 500 or a double settlement.
        if await callbacks.find(command.provider, verified.provider_event_id) is not None:
            return CallbackOutcome(replayed=True)

        verified = await self._confirm(verified)

        callback = RawCallbackRecord(
            provider=command.provider,
            provider_event_id=verified.provider_event_id,
            # Never the raw header. A provider that authenticates with a shared
            # secret would have that secret copied into a table read by support
            # tooling; the adapter's digest of the body is what disputes
            # actually need anyway.
            payload_signature=verified.audit_digest or command.signature,
            raw_payload=_record_payload(command.raw_body),
            payment_id=verified.payment_id,
            accepted=True,
            rejection_reason=None,
            received_at=now,
        )
        if not await callbacks.add(callback):
            return CallbackOutcome(replayed=True)

        payments: PaymentRepository = self._uow.repository("payment")
        payment = await payments.get_by_provider_reference(
            command.provider, verified.provider_reference
        )
        if payment is None:
            raise ResourceNotFound(
                "no payment matches the provider reference of this callback",
                context={
                    "provider": command.provider,
                    "provider_reference": verified.provider_reference,
                },
            )

        grant = await self._settle(payment, verified, now, command.signature)
        self._uow.collect(payment)
        if grant is not None:
            self._uow.collect(grant)
        await self._uow.commit()

        self._logger.info(
            "payment_callback_applied",
            payment_id=str(payment.id),
            status=payment.status.value,
            provider_reference=verified.provider_reference,
        )
        return CallbackOutcome(
            payment=_payment_view(payment),
            grant=_grant_view(grant) if grant else None,
        )

    async def _confirm(self, announced: VerifiedCallback) -> VerifiedCallback:
        """Turn what the provider said into what the provider confirms.

        Run before the callback is recorded: a failed confirmation must leave
        nothing behind, or the dedup row would make the next delivery — or the
        reconciliation pass — look like a replay of an event that never
        settled.
        """
        if self._status_source is None or announced.authoritative:
            return announced

        confirmed = await self._status_source.fetch_status(
            provider_reference=announced.provider_reference
        )
        if confirmed.status != announced.status:
            # Either a race, where the transaction moved on between the webhook
            # and the fetch, or a forged body. Both are resolved the same way —
            # the provider's answer wins — but the disagreement is worth seeing.
            self._logger.warning(
                "payment_callback_contradicted",
                provider_reference=announced.provider_reference,
                announced=announced.status,
                confirmed=confirmed.status,
            )
        return replace(
            confirmed,
            provider_event_id=announced.provider_event_id,
            audit_digest=announced.audit_digest,
            payment_id=confirmed.payment_id or announced.payment_id,
        )

    async def _settle(
        self,
        payment: ServiceFeePayment,
        verified: VerifiedCallback,
        now: datetime,
        signature: str,
    ) -> ServiceAccessGrant | None:
        payments: PaymentRepository = self._uow.repository("payment")
        status = verified.status.upper()

        if status == "FAILED":
            payment.mark_failed(
                reason=verified.rejection_reason or "provider reported failure",
                now=now,
            )
            await payments.save(payment)
            return None

        if status == "EXPIRED":
            # The payer never finished and the provider will take no more money
            # on this handle. ``expire`` is a no-op on a payment that is already
            # terminal, so an EXPIRED arriving after a success cannot undo it.
            payment.expire(now=now)
            await payments.save(payment)
            return None

        if status != "SUCCEEDED":
            # CREATED or PENDING: nothing has happened yet. Recorded above,
            # acted on here not at all — a transition invented for a pending
            # payment is a free unlock.
            self._logger.info(
                "payment_callback_not_terminal",
                payment_id=str(payment.id),
                status=status,
            )
            return None

        payment.mark_succeeded(
            provider_reference=verified.provider_reference,
            signature=verified.audit_digest or signature,
            now=now,
            provider_reported_at=verified.provider_reported_at,
            # A fetched verdict may be old: reconciliation exists precisely to
            # settle a payment made an hour ago. A pushed one must be fresh,
            # because an old pushed message cannot be told from a replayed one.
            tolerance_seconds=None if verified.authoritative else CALLBACK_TOLERANCE_SECONDS,
        )
        await payments.save(payment)

        grants: AccessGrantRepository = self._uow.repository("payment_grant")
        grant = await grants.get_active_for_listing(payment.tenant_id, payment.property_id)
        if grant is None:
            grant = ServiceAccessGrant.from_payment(payment, now=now, grant_id=new_id())
            await grants.add(grant)
        return grant


def _record_payload(raw_body: bytes) -> dict[str, Any]:
    try:
        import json

        parsed = json.loads(raw_body)
        return parsed if isinstance(parsed, dict) else {"body": raw_body.decode(errors="replace")}
    except Exception:
        return {"body": raw_body.decode(errors="replace")}


def _payment_view(payment: ServiceFeePayment) -> PaymentStatusView:
    return PaymentStatusView.of(payment)


def _grant_view(grant: ServiceAccessGrant) -> ServiceAccessStatusView:
    return ServiceAccessStatusView(
        grant_id=str(grant.id),
        status=grant.status.value,
        reveal_count=grant.reveal_count,
        granted_at=grant.granted_at,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
    )