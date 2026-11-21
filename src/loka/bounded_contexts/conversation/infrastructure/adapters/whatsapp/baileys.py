"""The Baileys adapter: the unofficial gateway in ``deploy/baileys``, for the tests.

It sends the sentence, the photo with its caption, a contact card and a map
pin, which Baileys sends natively. Buttons are left out on purpose: sent
through an unofficial client they show badly or not at all, and the sentence
already says « réponds 1 ».
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


class BaileysChannel(Delivery):
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
        await post(self._client, "/send", body)

    async def _card(self, to: str, contact: ContactCard) -> None:
        card = {"name": contact.name, "phone": contact.phone}
        await post(self._client, "/send", {"to": to, "contact": card})

    async def _pin(self, to: str, place: Place) -> None:
        location = {
            "latitude": place.latitude,
            "longitude": place.longitude,
            "label": place.name,
            "address": place.address,
        }
        await post(self._client, "/send", {"to": to, "location": location})

    async def aclose(self) -> None:
        await self._client.aclose()
