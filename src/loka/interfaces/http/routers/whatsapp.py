"""WhatsApp gateway ingress.

Baileys is a dumb transport: it turns a WhatsApp delivery into a POST and
never touches the domain. This controller is the platform's only admission
point for those deliveries, so it is where the transport is authenticated, the
body is validated, and the raw event is placed on ``whatsapp.incoming``.

Nothing here interprets the message. De-duplication, session creation and
idempotence belong to the ingestion use case, which is reached through the
broker precisely so a gateway retry cannot create two conversations.

A delivery carrying nothing at all is refused here rather than stored: the
domain would reject it anyway, and a 4xx tells the gateway not to retry a body
that can never become valid. A shared position counts as content — it is the
preferred way every specification asks a user to say where something is, and it
arrives with no text at all.
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
    PayloadTooLarge,
    Unauthenticated,
    UnsupportedMediaType,
    ValidationFailed,
)
from loka.shared.infrastructure.broker.topology import EVENTS_EXCHANGE
from loka.shared.infrastructure.object_storage.media import (
    CHAT_MEDIA_PREFIX,
    MediaStoreError,
)

router = APIRouter(prefix="/integrations/whatsapp", tags=["gateway"])

INBOUND_ROUTING_KEY = "WhatsAppMessageReceived"

# WhatsApp caps its own media well below this; the limit is here so a transport
# that lies about what it is sending cannot stream the process out of memory.
MAX_MEDIA_BYTES = 16 * 1024 * 1024

# Only what WhatsApp can actually deliver. The list is a deny-by-default
# allowlist rather than a blocklist: this endpoint is reached with a shared key
# and writes straight to a bucket, so accepting anything a browser would render
# turns one leaked key into stored XSS for whoever reads the object later.
ALLOWED_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "image/heif",
        "video/mp4",
        "video/3gpp",
        "video/quicktime",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/aac",
        "audio/amr",
        "application/pdf",
    }
)

# Namespaces the owner segment of the object key. A phone number there would
# leak into every log line and presigned URL that carries the key, and a random
# id per attempt would scatter one redelivered photo across the bucket, so the
# segment is derived from the message id instead: stable and anonymous.
_MEDIA_OWNER_NAMESPACE = uuid.UUID("0a4f6b9c-5d2e-4c8a-9f17-3b6e8d1c2a45")


class GatewayLocation(BaseModel):
    """A position pin, in the WGS84 decimal degrees WhatsApp delivers.

    Bounds are enforced here as well as in the domain: a transport sending
    nonsense gets a 422 it will not retry, instead of a message the pipeline
    accepts and then cannot place.
    """

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    label: str | None = Field(default=None, max_length=160)


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
    location: GatewayLocation | None = None
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
    if body.text is None and body.media_object_key is None and body.location is None:
        raise ValidationFailed(
            "a gateway delivery must carry text, a media object or a location",
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


@router.post("/media", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def receive_gateway_media(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
    x_external_message_id: Annotated[
        str | None, Header(alias="X-External-Message-Id")
    ] = None,
) -> dict[str, Any]:
    """Take media bytes off the transport and return the key that names them.

    The gateway uploads first and only then posts the delivery that references
    the key, so a message on the bus never points at bytes that are not there.
    """
    container = get_container(request)
    _authorise(container.settings.whatsapp.api_key.get_secret_value(), x_api_key)

    storage = container.media_storage
    if storage is None:
        raise ExternalServiceUnavailable(
            "object storage is not configured", context={"component": "object_storage"}
        )

    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_MEDIA_TYPES:
        raise UnsupportedMediaType(
            "this media type is not one WhatsApp delivers",
            context={"content_type": content_type},
        )

    data = await _read_capped_body(request)
    if not data:
        raise ValidationFailed(
            "a media upload cannot be empty", context={"content_type": content_type}
        )

    owner_id = uuid.uuid5(
        _MEDIA_OWNER_NAMESPACE, x_external_message_id or str(uuid.uuid4())
    )
    try:
        stored = await storage.put(
            data, owner_id=owner_id, prefix=CHAT_MEDIA_PREFIX, content_type=content_type
        )
    except MediaStoreError as exc:
        raise ExternalServiceUnavailable(
            "object storage refused the upload", context={"component": "object_storage"}
        ) from exc

    return {
        "media_object_key": stored.object_key,
        "size_bytes": stored.size_bytes,
        "checksum": stored.checksum,
        "content_type": stored.content_type,
    }


async def _read_capped_body(request: Request) -> bytes:
    """Buffer the body, refusing it the moment it passes the limit.

    Streamed rather than ``request.body()``: that buffers everything first, so
    the limit would be checked only after the oversized body was already in
    memory, which is the failure the limit exists to prevent.
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_MEDIA_BYTES:
            raise PayloadTooLarge(
                "media exceeds the accepted size",
                context={"limit_bytes": MAX_MEDIA_BYTES},
            )
        chunks.append(chunk)
    return b"".join(chunks)


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
