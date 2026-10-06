"""Payment callback record port.

The raw callback is provider evidence: kept for reconciliation and disputes,
never taken as truth by itself. ``add`` returns False when the exact event was
already recorded, which is how duplicate webhook delivery stays a no-op even
when two workers race on the same event.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    ServiceAccessGrant,
    ServiceFeePayment,
)


@dataclass(frozen=True, slots=True)
class RawCallbackRecord:
    provider: str
    provider_event_id: str
    payload_signature: str
    raw_payload: dict[str, Any]
    payment_id: Any | None
    accepted: bool
    rejection_reason: str | None
    received_at: datetime


class PaymentRepository(Protocol):
    async def get(self, payment_id: Any) -> ServiceFeePayment | None: ...

    async def get_open_for_application(self, application_id: Any) -> ServiceFeePayment | None: ...

    async def get_for_application(self, application_id: Any) -> ServiceFeePayment | None: ...

    async def get_by_provider_reference(
        self, provider: str, provider_reference: str
    ) -> ServiceFeePayment | None: ...

    async def add(self, payment: ServiceFeePayment) -> None: ...

    async def save(self, payment: ServiceFeePayment) -> None: ...


class AccessGrantRepository(Protocol):
    async def get_active_for_application(
        self, application_id: Any
    ) -> ServiceAccessGrant | None: ...

    async def add(self, grant: ServiceAccessGrant) -> None: ...


class PaymentCallbackRepository(Protocol):
    async def find(self, provider: str, provider_event_id: str) -> RawCallbackRecord | None: ...

    async def add(self, record: RawCallbackRecord) -> bool: ...