"""The Cloud API adapter: Meta's official WhatsApp API, the target.

Expected answers become buttons (three at most, under the photo when there is
one: the card « photo, line, Choisir » of the ads) or a list (ten at most); a
contact card and a map pin follow. Our photos are uploaded to Meta first: it
cannot read the private store.
"""

from __future__ import annotations

from typing import Any

import httpx

from loka.bounded_contexts.conversation.domain.value_objects.rich_reply import (
    ContactCard,
    Place,
    RichReply,
)
from loka.bounded_contexts.conversation.infrastructure.adapters.whatsapp.delivery import (
    Delivery,
    post,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

# The Cloud API's own limits; past them a message is refused whole.
_BUTTONS = 3
_BUTTON_TITLE = 20
_ROW_TITLE = 24
_ROW_DESCRIPTION = 72
_BUTTON_BODY = 1024
_LIST_BODY = 4096


class CloudApiChannel(Delivery):
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
        image = await self._uploaded(image_url) if image_url else None
        await post(self._client, "/messages", _addressed(to, cloud_content(text, image, rich)))

    async def _uploaded(self, url: str) -> dict[str, str] | None:
        """The photo uploaded to Meta, as ``{"id": …}``; ``None`` sends the words alone.

        Our photos live in a private store Meta cannot read: the presigned URL
        works from this network only, so the bytes are fetched here and handed
        to ``POST /{phone-number-id}/media`` first.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as fetch:
                got = await fetch.get(url)
                got.raise_for_status()
            mime = (got.headers.get("content-type") or "image/jpeg").split(";")[0].strip()
            response = await self._client.post(
                "/media",
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": ("photo", got.content, mime)},
            )
            response.raise_for_status()
            return {"id": str(response.json()["id"])}
        except Exception as exc:  # the sentence still goes, without its photo
            _logger.warning("cloud_media_upload_failed", error=type(exc).__name__)
            return None

    async def _card(self, to: str, contact: ContactCard) -> None:
        digits = "".join(c for c in contact.phone if c.isdigit())
        card = {
            "name": {"formatted_name": contact.name, "first_name": contact.name},
            "phones": [{"phone": f"+{digits}", "type": "CELL", "wa_id": digits}],
        }
        await post(
            self._client, "/messages", _addressed(to, {"type": "contacts", "contacts": [card]})
        )

    async def _pin(self, to: str, place: Place) -> None:
        location: dict[str, Any] = {"latitude": place.latitude, "longitude": place.longitude}
        if place.name:
            location["name"] = place.name
        if place.address:
            location["address"] = place.address
        await post(
            self._client, "/messages", _addressed(to, {"type": "location", "location": location})
        )

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


def cloud_content(
    text: str, image: dict[str, str] | None, rich: RichReply | None
) -> dict[str, Any]:
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
        if image:
            interactive["header"] = {"type": "image", "image": image}
        return {"type": "interactive", "interactive": interactive}
    if options and not image and len(text) <= _LIST_BODY:
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
    if image:
        return {"type": "image", "image": {**image, "caption": text}}
    return {"type": "text", "text": {"body": text, "preview_url": True}}
