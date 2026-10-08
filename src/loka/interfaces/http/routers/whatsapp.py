"""WhatsApp gateway ingress.

Baileys is a dumb transport: it turns a WhatsApp delivery into a POST and
never touches the domain. This controller is the platform's only admission
point for those deliveries, so it is where the transport is authenticated, the
body is validated, and the raw event is placed on ``whatsapp.incoming``.

Nothing here interprets the message. De-duplication, session creation and
idempotence belong to the ingestion use case, which is reached through the
broker precisely so a gateway retry cannot create two conversations.

A delivery with neither text nor a media object is refused here rather than
stored: the domain would reject it anyway, and a 4xx tells the gateway not to
retry a body that can never become valid.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from pydantic import BaseModel, Field, field_validator

from loka.interfaces.http.container import get_container
from loka.shared.application.context import current_context, new_id
from loka.shared.domain.errors import (
    ExternalServiceUnavailable,
    Unauthenticated,
    ValidationFailed,
)
from loka.shared.infrastructure.broker.topology import EVENTS_EXCHANGE

router = APIRouter(prefix="/integrations/whatsapp", tags=["gateway"])

INBOUND_ROUTING_KEY = "WhatsAppMessageReceived"


class GatewayMessage(BaseModel):
    """One delivery, as the transport sees it."""

    provider: str = Field(default="baileys", min_length=1, max_length=32)
    external_message_id: str = Field(min_length=1, max_length=180)
    conversation_ref: str = Field(min_length=1, max_length=96)
    sender_phone: str = Field(min_length=1, max_length=32)
    message_kind: str = Field(default="TEXT", min_length=1, max_length=32)
    provider_timestamp: datetime
    text: str | None = None
    media_object_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text", "media_object_key")
    @classmethod
    def _blank_becomes_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("message_kind")
    @classmethod
    def _kind_is_uppercase(cls, value: str) -> str:
        return value.strip().upper()


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def receive_gateway_event(
    body: GatewayMessage,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> dict[str, Any]:
    container = get_container(request)
    _authorise(container.settings.whatsapp.api_key.get_secret_value(), x_api_key)
    broker = container.broker
    if broker is None:
        raise ExternalServiceUnavailable(
            "the message broker is not connected", context={"component": "broker"}
        )
    # Cheap when already connected, and the only way to recover if the broker
    # was unreachable when the API started. Failing here yields a 503 the
    # gateway retries, instead of a 500 that looks like a bug in the payload.
    try:
        await broker.connect()
    except Exception as exc:
        raise ExternalServiceUnavailable(
            "the message broker is unreachable", context={"component": "broker"}
        ) from exc
    if body.text is None and body.media_object_key is None:
        raise ValidationFailed(
            "a gateway delivery must carry text or a media object",
            context={"message_kind": body.message_kind},
        )

    envelope = _envelope(body)
    try:
        await broker.publish(
            routing_key=INBOUND_ROUTING_KEY,
            payload=envelope,
            exchange=EVENTS_EXCHANGE,
            headers={
                "message_id": envelope["message_id"],
                "x-event-type": INBOUND_ROUTING_KEY,
                "x-correlation-id": envelope["correlation_id"],
            },
        )
    except Exception as exc:
        raise ExternalServiceUnavailable(
            "the delivery could not be queued", context={"component": "broker"}
        ) from exc
    return {
        "accepted": True,
        "external_message_id": body.external_message_id,
        "message_id": envelope["message_id"],
    }


def _authorise(configured: str, presented: str | None) -> None:
    """Fail closed: an unconfigured key must not open the endpoint."""
    if not configured:
        raise ExternalServiceUnavailable(
            "the whatsapp gateway key is not configured", context={"component": "whatsapp"}
        )
    if presented is None or not hmac.compare_digest(presented, configured):
        raise Unauthenticated("invalid gateway api key")


def _envelope(body: GatewayMessage) -> dict[str, Any]:
    """The shape every consumer on the way in already expects.

    Consumers read ``payload`` from the body, so publishing the inner object
    directly would make every inbound delivery look malformed no matter how
    valid it was.
    """
    correlation_id = current_context().get("correlation_id") or new_id()
    return {
        "message_id": str(uuid.uuid4()),
        "event_type": INBOUND_ROUTING_KEY,
        "occurred_at": datetime.now(UTC).isoformat(),
        "correlation_id": correlation_id,
        "causation_id": current_context().get("request_id"),
        "payload": body.model_dump(mode="json"),
    }
