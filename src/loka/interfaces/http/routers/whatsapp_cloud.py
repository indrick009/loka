"""Meta's WhatsApp Cloud API webhook: the official channel's way in.

Used once ``WHATSAPP__PROVIDER=cloud``. Every delivery is turned into the very
:class:`GatewayMessage` the Baileys gateway posts, then admitted the same way,
so nothing downstream knows which channel carried it.

- A tapped button or list row arrives as its option id — « 1 », « OUI » —
  exactly what the user would have typed (see ``value_objects/rich_reply``).
- A photo, a voice note or a document is fetched from Meta with the access
  token and stored before the delivery is queued, like the gateway's uploads.
- Every POST is checked against ``X-Hub-Signature-256`` with the app secret;
  an unconfigured secret refuses everything.
- Statuses (sent, delivered, read) are acknowledged and ignored.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response, status

from loka.interfaces.http.container import Container, get_container
from loka.interfaces.http.routers.whatsapp import (
    ALLOWED_MEDIA_TYPES,
    MAX_MEDIA_BYTES,
    GatewayLocation,
    GatewayMessage,
    admit,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, Unauthenticated
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import CHAT_MEDIA_PREFIX

router = APIRouter(prefix="/integrations/whatsapp/cloud", tags=["gateway"])

_logger = get_logger(__name__)

_KINDS = {
    "text": "TEXT",
    "interactive": "TEXT",
    "button": "TEXT",
    "image": "IMAGE",
    "video": "VIDEO",
    "audio": "AUDIO",
    "document": "DOCUMENT",
    "sticker": "IMAGE",
    "location": "LOCATION",
}
_MEDIA = ("image", "video", "audio", "document", "sticker")
_OWNER_NAMESPACE = uuid.UUID("5b1c7e2a-93d4-4f60-8a1e-2c7d9e4b6f03")


@router.get("/webhook", include_in_schema=False)
async def verify_webhook(request: Request) -> Response:
    """Meta's subscription check: echo the challenge when the token matches."""
    cloud = get_container(request).settings.whatsapp.cloud
    expected = cloud.verify_token.get_secret_value()
    params = request.query_params
    presented = params.get("hub.verify_token") or ""
    if (
        not expected
        or params.get("hub.mode") != "subscribe"
        or not hmac.compare_digest(presented, expected)
    ):
        raise Unauthenticated("invalid webhook verify token")
    return Response(content=params.get("hub.challenge", ""), media_type="text/plain")


@router.post("/webhook", status_code=status.HTTP_200_OK, include_in_schema=False)
async def receive_webhook(request: Request) -> dict[str, Any]:
    container = get_container(request)
    raw = await request.body()
    _verify_signature(
        container.settings.whatsapp.cloud.app_secret.get_secret_value(),
        raw,
        request.headers.get("X-Hub-Signature-256"),
    )
    accepted = 0
    for value in _values(json.loads(raw or b"{}")):
        names = {
            c.get("wa_id"): (c.get("profile") or {}).get("name")
            for c in value.get("contacts") or []
        }
        for message in value.get("messages") or []:
            delivery = await _delivery(container, message, names)
            if delivery is None:
                continue
            await admit(container, delivery)
            accepted += 1
    return {"accepted": accepted}


def _verify_signature(secret: str, raw: bytes, header: str | None) -> None:
    if not secret:
        raise ExternalServiceUnavailable(
            "the whatsapp cloud app secret is not configured", context={"component": "whatsapp"}
        )
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    if not header or not hmac.compare_digest(header, expected):
        raise Unauthenticated("invalid webhook signature")


def _values(body: Any) -> list[dict[str, Any]]:
    if not isinstance(body, dict):
        return []
    return [
        change.get("value") or {}
        for entry in body.get("entry") or []
        for change in entry.get("changes") or []
        if change.get("field") == "messages"
    ]


async def _delivery(
    container: Container, message: dict[str, Any], names: dict[str, Any]
) -> GatewayMessage | None:
    kind = str(message.get("type") or "")
    sender = str(message.get("from") or "")
    if kind not in _KINDS or not sender or not message.get("id"):
        _logger.info("cloud_message_ignored", kind=kind)
        return None
    text: str | None = None
    location: GatewayLocation | None = None
    media_key: str | None = None
    if kind == "text":
        text = (message.get("text") or {}).get("body")
    elif kind == "interactive":
        interactive = message.get("interactive") or {}
        picked = interactive.get("button_reply") or interactive.get("list_reply") or {}
        text = picked.get("id") or picked.get("title")
    elif kind == "button":
        button = message.get("button") or {}
        text = button.get("payload") or button.get("text")
    elif kind == "location":
        pin = message.get("location") or {}
        location = GatewayLocation(
            latitude=pin["latitude"],
            longitude=pin["longitude"],
            label=pin.get("name") or pin.get("address"),
        )
    else:
        media = message.get(kind) or {}
        text = media.get("caption")
        media_key = await _store_media(container, str(media.get("id") or ""), str(message["id"]))
    if text is None and media_key is None and location is None:
        return None
    stamp = message.get("timestamp")
    return GatewayMessage(
        provider="cloud",
        external_message_id=str(message["id"]),
        conversation_ref=sender,
        sender_phone=f"+{sender}",
        message_kind=_KINDS[kind] if media_key or kind not in _MEDIA else "TEXT",
        provider_timestamp=datetime.fromtimestamp(int(stamp), UTC) if stamp else datetime.now(UTC),
        text=text,
        media_object_key=media_key,
        location=location,
        metadata={"pushName": names.get(sender)},
    )


async def _store_media(container: Container, media_id: str, message_id: str) -> str | None:
    """Fetch one media from Meta and store it; ``None`` keeps the caption alone."""
    storage = container.media_storage
    cloud = container.settings.whatsapp.cloud
    if not media_id or storage is None:
        return None
    headers = {"Authorization": f"Bearer {cloud.access_token.get_secret_value()}"}
    try:
        async with httpx.AsyncClient(timeout=30, headers=headers) as client:
            meta = await client.get(f"{cloud.graph_url.rstrip('/')}/{cloud.api_version}/{media_id}")
            meta.raise_for_status()
            info = meta.json()
            content_type = str(info.get("mime_type") or "").split(";")[0].strip().lower()
            too_big = int(info.get("file_size") or 0) > MAX_MEDIA_BYTES
            if content_type not in ALLOWED_MEDIA_TYPES or too_big:
                return None
            data = await client.get(str(info["url"]))
            data.raise_for_status()
        stored = await storage.put(
            data.content[:MAX_MEDIA_BYTES],
            owner_id=uuid.uuid5(_OWNER_NAMESPACE, message_id),
            prefix=CHAT_MEDIA_PREFIX,
            content_type=content_type,
        )
    except Exception as exc:  # the caption, if any, still reaches the conversation
        _logger.warning("cloud_media_fetch_failed", error=type(exc).__name__)
        return None
    return stored.object_key
