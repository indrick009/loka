"""Payment <-> row mappers.

Kept separate from the aggregate so the domain never sees an ORM object and the
aggregate never sees SQL.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    GrantStatus,
    PaymentMethod,
    PaymentPurpose,
    PaymentStatus,
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.repositories import RawCallbackRecord
from loka.bounded_contexts.payment.infrastructure.persistence.models import (
    PaymentCallbackRow,
    ServiceAccessGrantRow,
    ServiceFeePaymentRow,
)
from loka.bounded_contexts.property.domain.value_objects.money import Money


def payment_to_row(payment: ServiceFeePayment) -> dict[str, Any]:
    return {
        "id": payment.id,
        "tenant_id": payment.tenant_id,
        "landlord_id": payment.landlord_id,
        "application_id": payment.application_id,
        "amount_xaf": payment.amount.amount,
        "currency": payment.amount.currency,
        "purpose": payment.purpose.value,
        "method": payment.method.value,
        "status": payment.status.value,
        "provider": payment.provider,
        "provider_reference": payment.provider_reference,
        "idempotency_key": payment.idempotency_key,
        "callback_signature": payment.callback_signature,
        "failure_reason": payment.failure_reason,
        "attempt_count": payment.attempt_count,
        "version": payment.version,
        "paid_at": payment.paid_at,
        "expires_at": payment.expires_at,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }


def payment_from_row(row: ServiceFeePaymentRow) -> ServiceFeePayment:
    """Rebuild the aggregate from persistence. Never leaks ORM rows upwards."""
    payment = ServiceFeePayment(
        payment_id=row.id,
        tenant_id=row.tenant_id,
        landlord_id=row.landlord_id,
        application_id=row.application_id,
        amount=Money(row.amount_xaf, row.currency),
        purpose=PaymentPurpose(row.purpose),
        now=row.created_at,
        idempotency_key=row.idempotency_key,
    )
    payment._assign_id(row.id)
    payment.method = PaymentMethod(row.method)
    payment.status = PaymentStatus(row.status)
    payment.provider = row.provider
    payment.provider_reference = row.provider_reference
    payment.callback_signature = row.callback_signature
    payment.failure_reason = row.failure_reason
    payment.attempt_count = row.attempt_count
    payment.paid_at = row.paid_at
    payment.expires_at = row.expires_at
    payment.created_at = row.created_at
    payment.updated_at = row.updated_at
    payment.mark_persisted(row.version)
    return payment


def grant_to_row(grant: ServiceAccessGrant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "tenant_id": grant.tenant_id,
        "landlord_id": grant.landlord_id,
        "application_id": grant.application_id,
        "payment_id": grant.payment_id,
        "status": grant.status.value,
        "reveal_count": grant.reveal_count,
        "granted_at": grant.granted_at,
        "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at,
        "revocation_reason": grant.revocation_reason,
    }


def grant_from_row(row: ServiceAccessGrantRow) -> ServiceAccessGrant:
    grant = ServiceAccessGrant(
        grant_id=row.id,
        tenant_id=row.tenant_id,
        landlord_id=row.landlord_id,
        application_id=row.application_id,
        payment_id=row.payment_id,
        now=row.granted_at,
    )
    grant._assign_id(row.id)
    grant.status = GrantStatus(row.status)
    grant.reveal_count = row.reveal_count
    grant.expires_at = row.expires_at
    grant.revoked_at = row.revoked_at
    grant.revocation_reason = row.revocation_reason
    return grant


def callback_from_row(row: PaymentCallbackRow) -> RawCallbackRecord:
    return RawCallbackRecord(
        provider=row.provider,
        provider_event_id=row.provider_event_id,
        payload_signature=row.payload_signature,
        raw_payload=dict(row.raw_payload),
        payment_id=row.payment_id,
        accepted=row.accepted,
        rejection_reason=row.rejection_reason,
        received_at=row.received_at,
    )


def callback_to_row(record: RawCallbackRecord) -> dict[str, Any]:
    return {
        "provider": record.provider,
        "provider_event_id": record.provider_event_id,
        "payload_signature": record.payload_signature,
        "raw_payload": record.raw_payload,
        "payment_id": record.payment_id,
        "accepted": record.accepted,
        "rejection_reason": record.rejection_reason,
        "received_at": record.received_at,
    }