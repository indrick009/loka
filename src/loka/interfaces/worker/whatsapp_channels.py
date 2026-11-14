"""The two ways a reply reaches WhatsApp: the official Cloud API, or Baileys.

``WHATSAPP__PROVIDER`` picks one. The conversation never knows which: it writes
a sentence and, sometimes, a :class:`RichReply`. Each channel shows what it can.

- **Cloud API** (the target): buttons or a list for the expected answers, the
  photo as the header of a card with its button, a contact card, a map pin.
- **Baileys** (the tests, today): the sentence, the photo with its caption, a
  contact card and a map pin, which Baileys sends natively. Buttons are left
  out on purpose: sent through an unofficial client they show badly or not at
  all, and the sentence already says « réponds 1 ».

Both map their failures the same way: unreachable or 502-504 is the platform
down (``GatewayDown``, waited for), 429 and other 5xx are retried, the rest is
a send that will never succeed.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx

from loka.bounded_contexts.conversation.domain.value_objects.rich_reply import (
    ContactCard,
    Place,
    RichReply,
)
from loka.shared.infrastructure.broker.consumer import (
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import whatsapp_failures_total

_logger = get_logger(__name__)

# The Cloud API's own limits; past them a message is refused whole.
_BUTTONS = 3
_BUTTON_TITLE = 20
_ROW_TITLE = 24
_ROW_DESCRIPTION = 72
_BUTTON_BODY = 1024
_LIST_BODY = 4096


class GatewayDown(RetryableHandlerError):
    """The gateway or its WhatsApp socket is down, not this message."""


class WhatsAppChannel(Protocol):
    async def send(
        self, *, to: str, text: str, image_url: str | None = None, rich: RichReply | None = None
    ) -> None: ...

    async def typing(self, *, to: str, message_id: str | None) -> None: ...

    async def aclose(self) -> None: ...


def channel_for(settings: Settings) -> WhatsAppChannel:
    if settings.whatsapp.provider == "cloud":
        return CloudApiChannel(settings)
    return BaileysChannel(settings)


async def _post(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> None:
    try:
        response = await client.post(url, json=body)
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        whatsapp_failures_total.labels(operation="send", reason="unreachable").inc()
        raise GatewayDown(f"whatsapp unreachable: {exc}") from exc
    if response.status_code < 300:
        return
    reason = f"http_{response.status_code}"
    whatsapp_failures_total.labels(operation="send", reason=reason).inc()
    detail = response.text[:512]
    if response.status_code in (502, 503, 504):
        raise GatewayDown(f"whatsapp returned {reason}: {detail}")
    if response.status_code == 429 or response.status_code >= 500:
        raise RetryableHandlerError(f"whatsapp returned {reason}: {detail}")
    # Up, and refusing this send: the number or the body is wrong, and neither
    # changes on the fourth attempt.
    raise PermanentHandlerError(f"whatsapp rejected the send {reason}: {detail}")


class _Channel:
    """The order every channel keeps: the sentence, then the card, then the pin."""

    async def send(
        self, *, to: str, text: str, image_url: str | None = None, rich: RichReply | None = None
    ) -> None:
        await self._main(to, text, image_url, rich)
        if rich is None:
            return
        # Best effort: the sentence already holds the number and the map link,
        # so a card that fails must not have the whole reply sent twice.
        try:
            if rich.contact is not None:
                await self._card(to, rich.contact)
            if rich.place is not None:
                await self._pin(to, rich.place)
        except Exception as exc:  # see above
            _logger.warning("whatsapp_extra_failed", error=type(exc).__name__)

    async def _main(
        self, to: str, text: str, image_url: str | None, rich: RichReply | None
    ) -> None:
        raise NotImplementedError

    async def _card(self, to: str, contact: ContactCard) -> None:
        raise NotImplementedError

    async def _pin(self, to: str, place: Place) -> None:
        raise NotImplementedError


class BaileysChannel(_Channel):
    def __init__(self, settings: Settings) -> None:
        whatsapp = settings.whatsapp
        api_key = whatsapp.api_key.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url=whatsapp.gateway_url.rstrip("/"),
            timeout=httpx.Timeout(whatsapp.request_timeout_seconds),
            headers={"X-Api-Key": api_key} if api_key else {},
        )

    async def _main(
        self, to: str, text: str, image_url: str | None, rich: RichReply | None
    ) -> None:
        body: dict[str, Any] = {"to": to, "text": text}
        if image_url:
            body = {"to": to, "image": image_url, "caption": text}
        await _post(self._client, "/send", body)

    async def _card(self, to: str, contact: ContactCard) -> None:
        card = {"name": contact.name, "phone": contact.phone}
        await _post(self._client, "/send", {"to": to, "contact": card})

    async def _pin(self, to: str, place: Place) -> None:
        location = {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "label": place.name,
            "address": place.address,
        }
        await _post(self._client, "/send", {"to": to, "location": location})

    async def typing(self, *, to: str, message_id: str | None) -> None:
        try:
            await self._client.post("/typing", json={"to": to})
        except httpx.HTTPError:
            return

    async def aclose(self) -> None:
        await self._client.aclose()


class CloudApiChannel(_Channel):
    """Meta's WhatsApp Cloud API: ``POST /{phone-number-id}/messages``."""

    def __init__(self, settings: Settings) -> None:
        cloud = settings.whatsapp.cloud
        self._client = httpx.AsyncClient(
            base_url=f"{cloud.graph_url.rstrip('/')}/{cloud.api_version}/{cloud.phone_number_id}",
            timeout=httpx.Timeout(settings.whatsapp.request_timeout_seconds),
            headers={"Authorization": f"Bearer {cloud.access_token.get_secret_value()}"},
        )

    async def _main(
        self, to: str, text: str, image_url: str | None, rich: RichReply | None
    ) -> None:
        await _post(self._client, "/messages", _addressed(to, cloud_content(text, image_url, rich)))

    async def _card(self, to: str, contact: ContactCard) -> None:
        digits = "".join(c for c in contact.phone if c.isdigit())
        card = {
            "name": {"formatted_name": contact.name, "first_name": contact.name},
            "phones": [{"phone": f"+{digits}", "type": "CELL", "wa_id": digits}],
        }
        await _post(
            self._client, "/messages", _addressed(to, {"type": "contacts", "contacts": [card]})
        )

    async def _pin(self, to: str, place: Place) -> None:
        location: dict[str, Any] = {"latitude": place.latitude, "longitude": place.longitude}
        if place.name:
            location["name"] = place.name
        if place.address:
            location["address"] = place.address
        await _post(
            self._client, "/messages", _addressed(to, {"type": "location", "location": location})
        )

    async def typing(self, *, to: str, message_id: str | None) -> None:
        """Marks the user's message read and shows « écrit… » until the reply."""
        if not message_id:
            return
        body = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
            "typing_indicator": {"type": "text"},
        }
        try:
            await self._client.post("/messages", json=body)
        except httpx.HTTPError:
            return

    async def aclose(self) -> None:
        await self._client.aclose()


def _addressed(to: str, content: dict[str, Any]) -> dict[str, Any]:
    digits = "".join(c for c in to if c.isdigit())
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": digits,
        **content,
    }


def cloud_content(text: str, image_url: str | None, rich: RichReply | None) -> dict[str, Any]:
    """The first message on the Cloud API: buttons or a list when answers are expected.

    Up to three answers become buttons, under the photo when there is one —
    the card « photo, line, Choisir » of the ads. Four to ten become a list.
    Anything that does not fit the API's limits goes as the plain sentence.
    """
    options = rich.options if rich else ()
    if options and len(options) <= _BUTTONS and len(text) <= _BUTTON_BODY:
        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": text},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": o.id, "title": o.title[:_BUTTON_TITLE]}}
                    for o in options
                ]
            },
        }
        if image_url:
            interactive["header"] = {"type": "image", "image": {"link": image_url}}
        return {"type": "interactive", "interactive": interactive}
    if options and not image_url and len(text) <= _LIST_BODY:
        rows = []
        for o in options:
            row: dict[str, Any] = {"id": o.id, "title": o.title[:_ROW_TITLE]}
            if o.description:
                row["description"] = o.description[:_ROW_DESCRIPTION]
            rows.append(row)
        menu = (rich.menu if rich and rich.menu else "Choisir")[:_BUTTON_TITLE]
        return {
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": text},
                "action": {"button": menu, "sections": [{"rows": rows}]},
            },
        }
    if image_url:
        return {"type": "image", "image": {"link": image_url, "caption": text}}
    return {"type": "text", "text": {"body": text, "preview_url": True}}
