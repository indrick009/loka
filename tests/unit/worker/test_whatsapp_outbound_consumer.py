"""Outbound delivery: what is retried, what is not, and what is said.

The gateway is the last thing between the conversation and a real landlord, so
the two properties worth locking down here are that the sentence is rendered
human-readable *before* the send, and that a rejection which can never succeed
is not retried five times on its way to the DLQ.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from loka.interfaces.worker.whatsapp_outbound_consumer import (
    OUTBOUND_QUEUE,
    WhatsAppOutboundConsumer,
)
from loka.interfaces.worker.whatsapp_replies import (
    ACK_TEXT,
    CLARIFY_TEXT,
    render_reply,
)
from loka.shared.infrastructure.broker.consumer import (
    DeferredHandlerError,
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.broker.topology import QUEUES, Broker, QueueSpec
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.object_storage.media import MediaStoreError
from loka.shared.infrastructure.redis_client import LockBackendUnavailable, NotAcquired

pytestmark = pytest.mark.unit

RECIPIENT = "690000000"


class _NoLock:
    async def __aenter__(self) -> object:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class _RateLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed
        self.calls: list[dict[str, int]] = []

    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        self.calls.append({"limit": limit, "window_seconds": window_seconds})
        return self._allowed


def outbound(**metadata: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": str(uuid.uuid4()),
        "recipient_phone": RECIPIENT,
    }
    return {"payload": {"metadata": {**base, **metadata}}}


def build(
    settings: Settings,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    *,
    allowed: bool = True,
) -> WhatsAppOutboundConsumer:
    spec = next(spec for spec in QUEUES if spec.name == OUTBOUND_QUEUE)
    consumer = WhatsAppOutboundConsumer(
        spec,
        Broker(settings.broker),
        Database(settings.database),
        settings,
        lock_factory=lambda _name: _NoLock(),
    )
    limiter = _RateLimiter(allowed)
    consumer._rate_limiter = limiter  # type: ignore[attr-defined]
    if handler is not None:
        consumer._client = httpx.AsyncClient(  # type: ignore[attr-defined]
            transport=httpx.MockTransport(handler),
            base_url=settings.whatsapp.gateway_url,
            headers={"X-Api-Key": settings.whatsapp.api_key.get_secret_value()}
            if settings.whatsapp.api_key.get_secret_value()
            else {},
        )
    return consumer


def respond(status: int, body: str = "") -> Callable[[httpx.Request], httpx.Response]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body)

    return handler


def recording(captured: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    return handler


class TestQueue:
    def test_the_queue_is_the_declared_one(self) -> None:
        assert OUTBOUND_QUEUE in {spec.name for spec in QUEUES}

    def test_the_queue_consumes_the_event_the_analysis_emits(self) -> None:
        spec = next(spec for spec in QUEUES if spec.name == OUTBOUND_QUEUE)

        assert "WhatsAppMessageSendRequested" in spec.routing_keys


class TestRefusedPermanently:
    async def test_a_payload_without_the_inner_object(self, settings: Settings) -> None:
        with pytest.raises(PermanentHandlerError, match="payload"):
            await build(settings).handle({})

    async def test_a_payload_without_metadata(self, settings: Settings) -> None:
        with pytest.raises(PermanentHandlerError, match="metadata"):
            await build(settings).handle({"payload": {}})

    @pytest.mark.parametrize(
        "metadata", [{}, {"recipient_phone": ""}, {"recipient_phone": 690000000}]
    )
    async def test_a_missing_recipient(
        self, settings: Settings, metadata: dict[str, Any]
    ) -> None:
        with pytest.raises(PermanentHandlerError, match="recipient_phone"):
            await build(settings).handle({"payload": {"metadata": metadata}})


class TestTheSentence:
    async def test_the_rendered_copy_is_what_reaches_the_gateway(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        await consumer.handle(outbound(reply_kind="QUESTION", question_key="ask.rent"))

        assert captured["to"] == RECIPIENT
        assert captured["text"] == render_reply(
            reply_kind="QUESTION", question_key="ask.rent"
        )
        assert captured["text"] == "Quel est le loyer mensuel, en FCFA ?"

    async def test_a_model_written_text_is_sent_verbatim(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        await consumer.handle(
            outbound(
                reply_kind="QUESTION",
                question_key="ask.rent",
                text="Alors, ce loyer ?",
            )
        )

        assert captured["text"] == "Alors, ce loyer ?"

    async def test_an_untranslated_question_key_sends_prose_not_a_key(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        await consumer.handle(outbound(reply_kind="QUESTION", question_key="ask.tiny_price"))

        assert captured["text"] == CLARIFY_TEXT
        assert "ask.tiny_price" not in captured["text"]

    async def test_a_reply_without_a_question_sends_the_ack(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        await consumer.handle(outbound(reply_kind="ACK", intent=None))

        assert captured["text"] == ACK_TEXT

    async def test_the_gateway_key_is_forwarded(self) -> None:
        settings = Settings(
            environment="test",
            encryption_key="test-key-material-for-field-encryption",
            whatsapp={"api_key": "gateway-key"},
        )
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("X-Api-Key"))
            return httpx.Response(200, json={"ok": True})

        await build(settings, handler).handle(outbound(reply_kind="ACK"))

        assert seen == ["gateway-key"]


class _Media:
    def __init__(self, url: str = "", error: Exception | None = None) -> None:
        self.url = url
        self.error = error
        self.seen: list[tuple[str, int]] = []

    async def presigned_download_url(self, object_key: str, *, ttl_seconds: int) -> str:
        self.seen.append((object_key, ttl_seconds))
        if self.error is not None:
            raise self.error
        return self.url


class TestThePhoto:
    async def test_a_named_object_is_sent_as_an_image_with_the_text_as_caption(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))
        media = _Media("http://minio:9000/loka-media/p/1/a.jpg?sig=1")
        consumer._media = media  # type: ignore[attr-defined]

        await consumer.handle(
            outbound(reply_kind="ACK", text="1. Studio", media_object_key="p/1/a.jpg")
        )

        assert captured == {
            "to": RECIPIENT,
            "image": "http://minio:9000/loka-media/p/1/a.jpg?sig=1",
            "caption": "1. Studio",
        }
        assert media.seen == [("p/1/a.jpg", settings.object_storage.presigned_url_ttl_seconds)]

    async def test_a_message_without_a_photo_stays_text(self, settings: Settings) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        await consumer.handle(outbound(reply_kind="ACK", text="bonjour"))

        assert captured == {"to": RECIPIENT, "text": "bonjour"}

    async def test_a_failed_presign_still_sends_the_sentence(
        self, settings: Settings
    ) -> None:
        # Object storage being down must not cost the tenant what was found.
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))
        consumer._media = _Media(error=MediaStoreError("storage down"))  # type: ignore[attr-defined]

        await consumer.handle(
            outbound(reply_kind="ACK", text="1. Studio", media_object_key="p/1/a.jpg")
        )

        assert captured == {"to": RECIPIENT, "text": "1. Studio"}


class TestRetryable:
    async def test_an_unreachable_gateway_is_retried(self, settings: Settings) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("connection refused")

        with pytest.raises(RetryableHandlerError, match="unreachable"):
            await build(settings, handler).handle(outbound(reply_kind="ACK"))

    async def test_a_gateway_timeout_is_retried(self, settings: Settings) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(RetryableHandlerError, match="unreachable"):
            await build(settings, handler).handle(outbound(reply_kind="ACK"))

    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_a_transient_gateway_answer_is_retried(
        self, settings: Settings, status: int
    ) -> None:
        with pytest.raises(RetryableHandlerError, match=f"http_{status}"):
            await build(settings, respond(status, "later")).handle(
                outbound(reply_kind="ACK")
            )

    async def test_the_rate_limit_parks_the_message_instead_of_sending(
        self, settings: Settings
    ) -> None:
        calls: list[str] = []

        def handler(_request: httpx.Request) -> httpx.Response:
            calls.append("sent")
            return httpx.Response(200, json={"ok": True})

        consumer = build(settings, handler, allowed=False)

        with pytest.raises(RetryableHandlerError, match="rate limit"):
            await consumer.handle(outbound(reply_kind="ACK"))

        assert calls == []


class TestPermanent:
    @pytest.mark.parametrize("status", [400, 401, 403, 404])
    async def test_a_gateway_refusal_is_not_retried(
        self, settings: Settings, status: int
    ) -> None:
        with pytest.raises(PermanentHandlerError, match=f"http_{status}"):
            await build(settings, respond(status, "wrong")).handle(
                outbound(reply_kind="ACK")
            )


class TestRateLimitBudget:
    async def test_the_window_is_one_minute(self, settings: Settings) -> None:
        consumer = build(settings, respond(200))
        limiter = consumer._rate_limiter  # type: ignore[attr-defined]

        await consumer.handle(outbound(reply_kind="ACK"))

        assert limiter.calls == [
            {"limit": settings.whatsapp.max_outbound_per_minute, "window_seconds": 60}
        ]


def test_the_gateway_url_is_stripped_of_a_trailing_slash() -> None:
    settings = Settings(
        environment="test",
        encryption_key="test-key-material-for-field-encryption",
        whatsapp={"gateway_url": "http://baileys:3000/", "api_key": "k"},
    )
    consumer = WhatsAppOutboundConsumer(
        QueueSpec(OUTBOUND_QUEUE),
        Broker(settings.broker),
        Database(settings.database),
        settings,
    )

    # Stored canonical, so the base URL that ends up in logs and in httpx is
    # the one that was configured rather than a normalised surprise.
    assert consumer._gateway_url == "http://baileys:3000"  # type: ignore[attr-defined]


class _RefusingLock:
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def __aenter__(self) -> object:
        raise self._error

    async def __aexit__(self, *exc_info: object) -> None:
        return None


class TestThreadOrder:
    """One send at a time per phone: "bonne nouvelle" once overtook the
    acknowledgement of the very search it answered."""

    async def test_a_send_already_in_flight_to_that_phone_waits_its_turn(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))
        consumer._lock_factory = lambda _name: _RefusingLock(NotAcquired("busy"))  # type: ignore[attr-defined]

        with pytest.raises(RetryableHandlerError, match="in flight"):
            await consumer.handle(outbound(reply_kind="ACK", text="Salut"))
        assert captured == {}

    async def test_a_lock_backend_that_is_down_still_delivers(self, settings: Settings) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))
        consumer._lock_factory = lambda _name: _RefusingLock(  # type: ignore[attr-defined]
            LockBackendUnavailable("redis down")
        )

        await consumer.handle(outbound(reply_kind="ACK", text="Salut"))

        assert captured["text"] == "Salut"


class TestPhotoBursts:
    """An album of ten photos is ten messages: answered once, by the last."""

    async def test_a_message_held_back_is_deferred_not_failed(self, settings: Settings) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))
        later = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()

        with pytest.raises(DeferredHandlerError) as deferred:
            await consumer.handle(outbound(reply_kind="ACK", text="Photo", not_before=later))

        assert 0 < deferred.value.delay_ms <= 5_000
        assert captured == {}

    async def test_an_acknowledgement_overtaken_by_a_newer_photo_is_dropped(
        self, settings: Settings
    ) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        async def newer(_message_id: uuid.UUID) -> bool:
            return True

        consumer._a_newer_photo_follows = newer  # type: ignore[method-assign]

        await consumer.handle(
            outbound(reply_kind="ACK", text="Photo reçue (1)", media_burst_of=str(uuid.uuid4()))
        )

        assert captured == {}

    async def test_the_last_photo_of_a_burst_is_answered(self, settings: Settings) -> None:
        captured: dict[str, Any] = {}
        consumer = build(settings, recording(captured))

        async def newer(_message_id: uuid.UUID) -> bool:
            return False

        consumer._a_newer_photo_follows = newer  # type: ignore[method-assign]

        await consumer.handle(
            outbound(reply_kind="ACK", text="Photos reçues (5)", media_burst_of=str(uuid.uuid4()))
        )

        assert captured["text"] == "Photos reçues (5)"
