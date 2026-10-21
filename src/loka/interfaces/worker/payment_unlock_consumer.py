"""Hand over the contact a tenant paid for, and tell him when it fails.

Two payment events, one promise each:

- ``PaymentSucceeded`` — the 1 000 FCFA is settled. The grant is marked as
  revealed so the fact is recorded, and the landlord's number and the lodging's
  location are sent to the tenant. This is the only place the contact crosses
  from Payment back into the conversation, and it is driven by the provider's
  callback, never by the tenant saying he paid.
- ``PaymentFailed`` — nothing was debited. The tenant is told and returned to
  the step that reads his retry.

A succeeded payment whose contact cannot be assembled, or a failed one with no
conversation to tell, is a permanent failure: it is exactly the case a human
has to see, and retrying it cannot change the outcome.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.ai.application.use_cases.present_payment_failure import (
    PresentPaymentFailureUseCase,
)
from loka.bounded_contexts.ai.infrastructure.composition import (
    reveal_unlocked_contact_use_case,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.payment.infrastructure.composition import (
    PAYMENT_GRANT_REPOSITORY,
)
from loka.shared.domain.errors import InvalidStateTransition
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

PAYMENT_UNLOCK_QUEUE = "payment.unlocks"

_SUCCEEDED = "PaymentSucceeded"
_FAILED = "PaymentFailed"

_logger = get_logger(__name__)


class PaymentUnlockConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database

    async def handle(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("event_type")
        if event_type not in (_SUCCEEDED, _FAILED):
            raise PermanentHandlerError(f"payment.unlocks received {event_type!r}")
        metadata = _metadata(payload)
        tenant_id = uuid_field(metadata, "tenant_id")
        property_id = uuid_field(metadata, "property_id")
        correlation_id = str(payload.get("correlation_id") or uuid.uuid4())
        now = datetime.now(UTC)

        if event_type == _SUCCEEDED:
            await self._reveal(
                tenant_id=tenant_id,
                property_id=property_id,
                landlord_id=uuid_field(metadata, "landlord_id"),
                correlation_id=correlation_id,
                now=now,
            )
        else:
            await self._notify_failure(
                tenant_id=tenant_id,
                correlation_id=correlation_id,
                now=now,
            )

    async def _reveal(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        landlord_id: uuid.UUID,
        correlation_id: str,
        now: datetime,
    ) -> None:
        async with unit_of_work(self._database) as uow:
            grants = uow.repository(PAYMENT_GRANT_REPOSITORY)
            grant = await grants.get_active_for_listing(tenant_id, property_id)
            if grant is not None:
                try:
                    grant.record_reveal(now=now)
                except InvalidStateTransition as exc:
                    # The grant aged out between the callback and here. The
                    # tenant still paid, so the contact goes out regardless;
                    # only the reveal count is left alone.
                    _logger.warning(
                        "reveal_not_recorded",
                        grant_id=str(grant.id),
                        error=str(exc),
                    )
                else:
                    await grants.save(grant)
                    uow.collect(grant)
            revealed = await reveal_unlocked_contact_use_case(uow).execute(
                tenant_id=tenant_id,
                property_id=property_id,
                landlord_id=landlord_id,
                correlation_id=correlation_id,
                now=now,
            )
        if not revealed:
            raise PermanentHandlerError(
                f"unlock for tenant {tenant_id} on {property_id} could not be revealed"
            )

    async def _notify_failure(
        self, *, tenant_id: uuid.UUID, correlation_id: str, now: datetime
    ) -> None:
        async with unit_of_work(self._database) as uow:
            handled = await PresentPaymentFailureUseCase(
                uow, uow.repository(CONVERSATION_SESSION_REPOSITORY)
            ).execute(tenant_id=tenant_id, correlation_id=correlation_id, now=now)
        if not handled:
            raise PermanentHandlerError(
                f"tenant {tenant_id} has no conversation to be told of the failure"
            )


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("payment payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("payment payload has no metadata")
    return metadata


def uuid_field(metadata: dict[str, Any], name: str) -> uuid.UUID:
    value = metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError(f"payment field {name!r} is missing")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(f"payment field {name!r} is not a uuid: {value}") from exc
