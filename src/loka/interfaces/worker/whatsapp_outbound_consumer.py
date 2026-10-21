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

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.asyncio import Redis

from loka.bounded_contexts.ai.infrastructure.composition import (
    voice_platform_reply_use_case,
)
from loka.bounded_contexts.messaging.infrastructure.persistence.sent_messages import (
    SqlAlchemySentMessageLog,
)
from loka.interfaces.worker.whatsapp_replies import render_reply
from loka.shared.infrastructure.broker.consumer import (
    ConsumerBase,
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import whatsapp_failures_total
from loka.shared.infrastructure.object_storage.media import (
    MediaStorage,
    MediaStoreError,
    S3MediaStorage,
)
from loka.shared.infrastructure.redis_client import RedisRateLimiter

OUTBOUND_QUEUE = "whatsapp.outgoing"

_logger = get_logger(__name__)


class WhatsAppOutboundConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings
        whatsapp = settings.whatsapp
        self._gateway_url = whatsapp.gateway_url.rstrip("/")
        self._api_key = whatsapp.api_key.get_secret_value()
        self._timeout = whatsapp.request_timeout_seconds
        self._limit_per_minute = whatsapp.max_outbound_per_minute
        # Presigning lives here, not in the analysis: a URL that expires has to
        # be minted moments before the send, and the domain only holds the key.
        self._media: MediaStorage = S3MediaStorage(settings.object_storage)
        self._media_ttl = settings.object_storage.presigned_url_ttl_seconds
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
        # A model-written sentence wins; the template is the fallback for the
        # deterministic path and for every deployment without an AI key.
        text = _optional_str(metadata, "text") or render_reply(
            reply_kind=reply_kind,
            question_key=_optional_str(metadata, "question_key"),
            intent=_optional_str(metadata, "intent"),
            language=_optional_str(metadata, "language"),
        )
        session_id = _optional_uuid(metadata.get("session_id"))
        answering = _optional_str(metadata, "answering")
        if answering is not None and session_id is not None:
            text = await self._in_user_voice(
                text,
                answering=answering,
                session_id=session_id,
                user_id=_optional_uuid(body.get("actor_id")),
            )
        # A photo, when the message names one, is sent with the sentence as its
        # caption: the tenant reads what was found and sees it in one message.
        image_url = await self._image_url(_optional_str(metadata, "media_object_key"))

        # A conversation may not answer faster than the platform can absorb its
        # own traffic; the counter is best-effort, never a correctness guard.
        allowed = await self._rate_limiter.allow(
            "whatsapp.outbound", limit=self._limit_per_minute, window_seconds=60
        )
        if not allowed:
            raise RetryableHandlerError(
                f"outbound rate limit of {self._limit_per_minute}/min reached"
            )

        await self._send(to=recipient, text=text, image_url=image_url)
        await self._record_sent(
            event_id=body.get("event_id"), session_id=session_id, recipient=recipient, text=text
        )
        _logger.info(
            "outbound_message_sent",
            recipient=recipient,
            reply_kind=reply_kind,
            question_key=metadata.get("question_key"),
            session_id=metadata.get("session_id"),
            characters=len(text),
            media=image_url is not None,
        )

    async def _in_user_voice(
        self,
        text: str,
        *,
        answering: str,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
    ) -> str:
        """The platform's sentence in the user's register, or as it is.

        Best effort by design: the rewrite is a better way of saying what is
        already decided, never a condition for saying it.
        """
        try:
            async with unit_of_work(self._database) as uow:
                voice = voice_platform_reply_use_case(uow, settings=self._settings)
                if voice is None:
                    return text
                return await voice.execute(
                    session_id=session_id,
                    user_id=user_id,
                    message=answering,
                    platform_text=text,
                    now=datetime.now(UTC),
                )
        except Exception as exc:  # the stock sentence still goes out
            _logger.warning(
                "outbound_voice_failed", session_id=str(session_id), error=type(exc).__name__
            )
            return text

    async def _record_sent(
        self, *, event_id: Any, session_id: uuid.UUID | None, recipient: str, text: str
    ) -> None:
        """Keep the words the user read, so the next turn can see them.

        After the send, and never able to undo it: a message already on the
        phone must not be retried because its copy could not be stored.
        """
        try:
            async with unit_of_work(self._database) as uow:
                await SqlAlchemySentMessageLog(uow.session).record(
                    idempotency_key=str(event_id or uuid.uuid4()),
                    session_id=session_id,
                    recipient_phone=recipient,
                    text=text,
                    now=datetime.now(UTC),
                )
                await uow.commit()
        except Exception as exc:  # see the docstring
            _logger.warning(
                "outbound_record_failed", session_id=str(session_id), error=type(exc).__name__
            )

    async def _image_url(self, object_key: str | None) -> str | None:
        """A short-lived URL for one stored object, or ``None`` when it fails.

        The photo is an extra on top of the sentence that says what was found:
        an object storage that is down must not cost the tenant that sentence,
        so the presign failure degrades to text rather than failing the send.
        """
        if not object_key:
            return None
        try:
            return await self._media.presigned_download_url(
                object_key, ttl_seconds=self._media_ttl
            )
        except MediaStoreError as exc:
            whatsapp_failures_total.labels(operation="presign", reason="failed").inc()
            _logger.warning(
                "outbound_media_presign_failed",
                object_key=object_key,
                error=str(exc),
            )
            return None

    async def _send(
        self, *, to: str, text: str, image_url: str | None = None
    ) -> None:
        body: dict[str, Any] = {"to": to, "text": text}
        if image_url:
            body = {"to": to, "image": image_url, "caption": text}
        try:
            response = await self._client.post("/send", json=body)
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


def _optional_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None
