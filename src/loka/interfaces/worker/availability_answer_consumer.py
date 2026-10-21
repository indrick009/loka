"""The landlord's availability answer, carried back to the waiting tenant.

``availability.questions`` asks the owner "le logement est encore disponible ?";
this queue hears his answer and does the two things the conversation needs:
moves the tenant who chose that lodging to the 1 000 FCFA offer when the answer
is yes, or tells him it is gone and returns him to his list when it is no.

An answer with no ``tenant_id`` is not an error: a landlord may volunteer
"c'est déjà loué" about a lodging nobody asked about, and there is nobody to
write to. It is logged and acknowledged. A message that is not this event, or
that carries an id that is not a uuid, is refused permanently — retrying would
fail identically.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.ai.application.use_cases.handle_availability_answer import (
    HandleAvailabilityAnswerUseCase,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

AVAILABILITY_ANSWER_QUEUE = "availability.answers"

AVAILABILITY_ANSWERED_EVENT = "AvailabilityCheckAnswered"

_logger = get_logger(__name__)


class AvailabilityAnswerConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database

    async def handle(self, payload: dict[str, Any]) -> None:
        if payload.get("event_type") != AVAILABILITY_ANSWERED_EVENT:
            raise PermanentHandlerError(
                f"availability.answers received {payload.get('event_type')!r}"
            )
        metadata = _metadata(payload)
        tenant_id = optional_uuid(metadata, "tenant_id")
        if tenant_id is None:
            # Nobody is waiting on this answer; nothing to write and nobody to move.
            _logger.info("availability_answer_without_tenant")
            return
        property_id = uuid_field(metadata, "property_id")
        landlord_id = uuid_field(metadata, "landlord_id")
        confirmed = metadata.get("confirms_availability")
        if not isinstance(confirmed, bool):
            raise PermanentHandlerError(
                "availability field 'confirms_availability' is missing or not a boolean"
            )

        async with unit_of_work(self._database) as uow:
            handled = await HandleAvailabilityAnswerUseCase(
                uow, uow.repository(CONVERSATION_SESSION_REPOSITORY)
            ).execute(
                tenant_id=tenant_id,
                property_id=property_id,
                landlord_id=landlord_id,
                confirmed=confirmed,
                offer=_mapping(metadata.get("offer")),
                offer_reply=_mapping(metadata.get("offer_reply")),
                correlation_id=str(payload.get("correlation_id") or uuid.uuid4()),
                now=datetime.now(UTC),
            )
        if not handled:
            raise PermanentHandlerError(
                f"tenant {tenant_id} has no conversation to move on this answer"
            )


def _mapping(value: object) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("availability payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("availability payload has no metadata")
    return metadata


def optional_uuid(metadata: dict[str, Any], name: str) -> uuid.UUID | None:
    """The field as a uuid, or ``None`` when it is absent or null on purpose."""
    value = metadata.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError(f"availability field {name!r} is not a uuid")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(f"availability field {name!r} is not a uuid: {value}") from exc


def uuid_field(metadata: dict[str, Any], name: str) -> uuid.UUID:
    value = optional_uuid(metadata, name)
    if value is None:
        raise PermanentHandlerError(f"availability field {name!r} is missing")
    return value
