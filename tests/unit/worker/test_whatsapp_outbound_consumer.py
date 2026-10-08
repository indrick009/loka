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
    PermanentHandlerError,
    RetryableHandlerError,
)
from loka.shared.infrastructure.broker.topology import QUEUES, Broker, QueueSpec
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

pytestmark = pytest.mark.unit

RECIPIENT = "690000000"


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
        spec, Broker(settings.broker), Database(settings.database), settings
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
