"""What both WhatsApp adapters share: how a failure is read, and the order of a reply.

Unreachable or 502-504 is the platform down (``GatewayDown``, waited for),
429 and other 5xx are retried, the rest is a send that will never succeed.
A reply is the sentence, then the contact card, then the map pin.
"""

from __future__ import annotations

from typing import Any

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
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import whatsapp_failures_total

_logger = get_logger(__name__)


class GatewayDown(RetryableHandlerError):
    """The gateway or its WhatsApp socket is down, not this message."""


async def post(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> None:
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


class Delivery:
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
