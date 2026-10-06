"""Inbound WhatsApp message handler.

The first real consumer: it drains the ``whatsapp.incoming`` queue, which the
gateway feeds with raw deliveries keyed by ``WhatsAppMessageReceived``, and
turns them into a de-duplicated, durable message plus a
``WhatsAppMessageIngested`` domain event.

De-duplication is not this handler's job to remember: it happens in the
database. What the handler does refuse is to *invent* data. A delivery missing
its external id cannot be de-duplicated, so substituting a generated one would
store the message twice on the next redelivery; that is a permanent defect and
goes straight to the DLQ instead of being retried for ever.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.messaging.application.use_cases.receive_inbound_message import (
    ReceiveInboundMessageCommand,
)
from loka.bounded_contexts.messaging.domain.value_objects.inbound_message import MessageKind
from loka.bounded_contexts.messaging.infrastructure.composition import (
    receive_inbound_message_use_case,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.crypto.blind_index import derive_blind_index_key
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

INBOUND_QUEUE = "whatsapp.incoming"

_logger = get_logger(__name__)


class WhatsAppInboundConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._index_key = derive_blind_index_key(settings.encryption_key.get_secret_value())

    async def handle(self, payload: dict[str, Any]) -> None:
        command = parse_inbound_payload(payload)
        async with unit_of_work(self._database) as uow:
            receipt = await receive_inbound_message_use_case(
                uow, index_key=self._index_key
            ).execute(command, now=datetime.now(UTC))
        _logger.info(
            "inbound_message_ingested",
            message_id=str(receipt.message_id),
            session_id=str(receipt.session_id) if receipt.session_id else None,
            sender_user_id=str(receipt.sender_user_id) if receipt.sender_user_id else None,
            duplicated=receipt.duplicated,
        )


def parse_inbound_payload(payload: dict[str, Any]) -> ReceiveInboundMessageCommand:
    """Turn a broker payload into a command, or fail permanently."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("inbound message payload is missing or not an object")

    def require(name: str) -> str:
        value = body.get(name)
        if not isinstance(value, str) or not value.strip():
            raise PermanentHandlerError(f"inbound message field '{name}' is missing")
        return value

    text = body.get("text")
    media = body.get("media_object_key")
    metadata = body.get("metadata")
    kind = body.get("message_kind")
    return ReceiveInboundMessageCommand(
        provider=require("provider"),
        external_message_id=require("external_message_id"),
        conversation_ref=require("conversation_ref"),
        sender_phone=require("sender_phone"),
        message_kind=kind if isinstance(kind, str) and kind else MessageKind.UNSUPPORTED.value,
        provider_timestamp=_parse_timestamp(body.get("provider_timestamp")),
        text=text if isinstance(text, str) and text.strip() else None,
        media_object_key=media if isinstance(media, str) and media.strip() else None,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError("inbound message provider_timestamp is missing")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise PermanentHandlerError(
            f"inbound message provider_timestamp is not ISO-8601: {value}"
        ) from exc
