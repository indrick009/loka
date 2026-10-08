"""Outbound WhatsApp delivery.

Drains ``whatsapp.outgoing`` and hands the sentence to the gateway. The
conversation layer decides *what* to say and the gateway decides *how* it
reaches the phone; this consumer is the seam between them, and the only place
that knows both.

Two rules the rest of the system relies on:

* **Nothing is sent before the copy is rendered.** An untranslated
  ``question_key`` reaching a landlord is a variable name in a conversation,
  so an unknown key degrades to the clarification instead of being dropped.
* **A send that may still succeed is retried, a send that can never succeed
  is not.** A gateway 4xx means the destination or the body is wrong: retrying
  it produces the same answer five times before parking the message, so it
  goes to the DLQ on the first attempt.
"""

from __future__ import annotations

from typing import Any

import httpx
from redis.asyncio import Redis

from loka.interfaces.worker.whatsapp_replies import render_reply
from loka.shared.infrastructure.broker.consumer import (
    ConsumerBase,
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import whatsapp_failures_total
from loka.shared.infrastructure.redis_client import RedisRateLimiter

OUTBOUND_QUEUE = "whatsapp.outgoing"

_logger = get_logger(__name__)


class WhatsAppOutboundConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        whatsapp = settings.whatsapp
        self._gateway_url = whatsapp.gateway_url.rstrip("/")
        self._api_key = whatsapp.api_key.get_secret_value()
        self._timeout = whatsapp.request_timeout_seconds
        self._limit_per_minute = whatsapp.max_outbound_per_minute
        self._client = httpx.AsyncClient(
            base_url=self._gateway_url,
            timeout=httpx.Timeout(self._timeout),
            headers={"X-Api-Key": self._api_key} if self._api_key else {},
        )
        # Built, not awaited: redis-py connects lazily, and a rate limiter whose
        # backend is down must degrade to "allow" rather than stop replies.
        self._rate_limiter = RedisRateLimiter(
            Redis.from_url(settings.redis_url, decode_responses=True)
        )

    async def stop(self) -> None:
        await super().stop()
        await self._client.aclose()

    async def handle(self, payload: dict[str, Any]) -> None:
        body = payload.get("payload")
        if not isinstance(body, dict):
            raise PermanentHandlerError("outbound payload is missing or not an object")
        metadata = body.get("metadata")
        if not isinstance(metadata, dict):
            raise PermanentHandlerError("outbound payload has no metadata")

        recipient = _require_str(metadata, "recipient_phone")
        reply_kind = str(metadata.get("reply_kind") or "ACK")
        text = render_reply(
            reply_kind=reply_kind,
            question_key=_optional_str(metadata, "question_key"),
            intent=_optional_str(metadata, "intent"),
        )

        # A conversation may not answer faster than the platform can absorb its
        # own traffic; the counter is best-effort, never a correctness guard.
        allowed = await self._rate_limiter.allow(
            "whatsapp.outbound", limit=self._limit_per_minute, window_seconds=60
        )
        if not allowed:
            raise RetryableHandlerError(
                f"outbound rate limit of {self._limit_per_minute}/min reached"
            )

        await self._send(to=recipient, text=text)
        _logger.info(
            "outbound_message_sent",
            recipient=recipient,
            reply_kind=reply_kind,
            question_key=metadata.get("question_key"),
            session_id=metadata.get("session_id"),
            characters=len(text),
        )

    async def _send(self, *, to: str, text: str) -> None:
        try:
            response = await self._client.post("/send", json={"to": to, "text": text})
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            whatsapp_failures_total.labels(operation="send", reason="unreachable").inc()
            raise RetryableHandlerError(f"gateway unreachable: {exc}") from exc

        if response.status_code < 300:
            return

        reason = f"http_{response.status_code}"
        whatsapp_failures_total.labels(operation="send", reason=reason).inc()
        detail = response.text[:512]
        if response.status_code == 429 or response.status_code >= 500:
            raise RetryableHandlerError(f"gateway returned {reason}: {detail}")
        # The gateway is up and refuses this specific send: the phone number or
        # the body is wrong, and neither changes on the fourth attempt.
        raise PermanentHandlerError(f"gateway rejected the send {reason}: {detail}")


def _require_str(metadata: dict[str, Any], name: str) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError(f"outbound field '{name}' is missing")
    return value


def _optional_str(metadata: dict[str, Any], name: str) -> str | None:
    value = metadata.get(name)
    return value if isinstance(value, str) and value.strip() else None
