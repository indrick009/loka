"""RabbitMQ topology and consumer conventions.

Queues are declared centrally so producers and consumers cannot drift.
Every queue gets a dead-letter queue; retries use delayed re-publication with
exponential backoff, capped by ``max_retries``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
)

from loka.shared.infrastructure.config.settings import BrokerSettings
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

EVENTS_EXCHANGE = "loka.events"
COMMANDS_EXCHANGE = "loka.commands"


@dataclass(frozen=True, slots=True)
class QueueSpec:
    name: str
    exchange: str = EVENTS_EXCHANGE
    routing_keys: tuple[str, ...] = ()
    prefetch: int = 64
    max_retries: int = 5
    dead_letter: bool = True
    durable: bool = True


QUEUES: tuple[QueueSpec, ...] = (
    QueueSpec("whatsapp.incoming", routing_keys=("WhatsAppMessageReceived",)),
    QueueSpec("whatsapp.outgoing", routing_keys=("WhatsAppMessageSendRequested",)),
    QueueSpec("ai.requests", routing_keys=("ConversationAnalysisRequested",)),
    QueueSpec("fraud.analysis", routing_keys=("FraudAnalysisRequested",)),
    QueueSpec("property.verification", routing_keys=("PropertyDataQualityScanRequested",)),
    QueueSpec("notifications", routing_keys=("NotificationRequested",)),
    QueueSpec("payments.events", routing_keys=("PaymentSucceeded", "PaymentFailed")),
    QueueSpec("analytics.events", routing_keys=("AiUsageRecorded",)),
    QueueSpec(
        "search.projections",
        routing_keys=("PropertyPublished", "PropertyRented", "PropertyMarkedUnavailable"),
    ),
    QueueSpec("trust.projections", routing_keys=("TenantFeedbackSubmitted",)),
    QueueSpec("outbox.dispatch"),
)


class Broker:
    """Robust connection wrapper: auto-reconnect plus publisher confirms."""

    def __init__(self, settings: BrokerSettings) -> None:
        self._settings = settings
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractRobustChannel | None = None

    async def connect(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            if self._channel is not None and not self._channel.is_closed:
                return
            # A channel can die on its own (broker restart, idle timeout) while
            # the connection survives; reopening is cheaper than a reconnect.
            self._channel = await self._connection.channel(publisher_confirms=True)
            await self._declare_exchanges()
            _logger.info("broker_channel_reopened")
            return
        self._connection = await aio_pika.connect_robust(
            self._settings.url.get_secret_value(), timeout=10
        )
        self._channel = await self._connection.channel(publisher_confirms=True)
        await self._declare_exchanges()
        _logger.info("broker_connected")

    async def _declare_exchanges(self) -> None:
        await self.channel.declare_exchange(EVENTS_EXCHANGE, ExchangeType.TOPIC, durable=True)
        await self.channel.declare_exchange(COMMANDS_EXCHANGE, ExchangeType.DIRECT, durable=True)

    @property
    def channel(self) -> AbstractRobustChannel:
        if self._channel is None:
            raise RuntimeError("Broker.connect() has not been awaited")
        return self._channel

    async def declare_topology(self) -> None:
        for spec in QUEUES:
            await self.declare_queue(spec)

    def dead_letter_queue_name(self, queue_name: str) -> str:
        """Name of the DLQ for a work queue, from the configured suffix."""
        return f"{queue_name}{self._settings.dlq_suffix}"

    async def declare_queue(self, spec: QueueSpec) -> None:
        dlq_name = self.dead_letter_queue_name(spec.name)
        dlq = await self.channel.declare_queue(dlq_name, durable=spec.durable)
        exchange = await self.channel.get_exchange(spec.exchange)
        arguments = {"x-dead-letter-exchange": "", "x-dead-letter-routing-key": dlq_name}
        queue = await self.channel.declare_queue(
            spec.name, durable=spec.durable, arguments=arguments
        )
        # Retry parking lot: a rejected-for-retry message is published here with
        # a per-message expiration and dead-letters back to the work queue once
        # the backoff has elapsed, so no consumer slot is held while waiting.
        await self.channel.declare_queue(
            delay_queue_name(spec.name),
            durable=spec.durable,
            arguments={"x-dead-letter-exchange": "", "x-dead-letter-routing-key": spec.name},
        )
        if spec.routing_keys:
            for key in spec.routing_keys:
                await queue.bind(exchange, routing_key=key)
        else:
            await queue.bind(exchange, routing_key=spec.name)
        await dlq.bind(exchange, routing_key=dlq_name)

    async def publish(
        self,
        *,
        routing_key: str,
        payload: dict[str, Any],
        headers: dict[str, Any] | None = None,
        exchange: str = EVENTS_EXCHANGE,
        delivery_mode: DeliveryMode = DeliveryMode.PERSISTENT,
    ) -> None:
        """Publish to a named exchange.

        The default exchange routes by queue name, which silently drops domain
        events published with a dotted routing key, so the target exchange is
        always resolved explicitly.

        Every step is bounded by ``publish_timeout_seconds``: a robust channel
        that lost its broker can block forever on exchange resolution, and a
        silent hang would hold outbox rows claimed indefinitely instead of
        surfacing a retryable failure.
        """
        timeout = self._settings.publish_timeout_seconds
        target = await asyncio.wait_for(self.channel.get_exchange(exchange), timeout=timeout)
        await asyncio.wait_for(
            target.publish(
                Message(
                    body=_encode(payload),
                    content_type="application/json",
                    delivery_mode=delivery_mode,
                    headers=headers or {},
                    message_id=str(headers.get("message_id", "")) if headers else None,
                    correlation_id=(str(headers.get("correlation_id")) or None)
                    if headers
                    else None,
                ),
                routing_key=routing_key,
            ),
            timeout=timeout,
        )

    async def close(self) -> None:
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
            _logger.info("broker_closed")


def _encode(payload: dict[str, Any]) -> bytes:
    import json

    return json.dumps(payload, separators=(",", ":"), default=str).encode()


def decode_message(message: AbstractIncomingMessage | Message) -> dict[str, Any]:
    """Decode a message body, tolerating a malformed or non-JSON payload.

    A poison message must not crash the consumer loop, so anything unparsable is
    surfaced as an empty payload and logged rather than raising here.
    """
    import json

    try:
        decoded: Any = json.loads(message.body)
    except (ValueError, TypeError, UnicodeDecodeError):
        _logger.warning("message_payload_not_json", message_id=str(message.message_id))
        return {}
    return decoded if isinstance(decoded, dict) else {"payload": decoded}


def header_str(headers: dict[str, Any] | None, name: str) -> str | None:
    """Read a header as text; AMQP values are not guaranteed to be strings."""
    if not headers:
        return None
    value = headers.get(name)
    if value is None:
        return None
    return value if isinstance(value, str) else str(value)


def header_int(headers: dict[str, Any] | None, name: str, default: int = 0) -> int:
    """Read a header as an int, falling back to ``default`` when unusable."""
    value = header_str(headers, name)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def delay_queue_name(queue_name: str) -> str:
    return f"{queue_name}.delay"


def retry_delay_ms(attempt: int, base_ms: int = 1000, cap_ms: int = 300_000) -> int:
    """Exponential backoff: 1s, 2s, 4s ... capped at 5 minutes."""
    exponent = max(attempt - 1, 0)
    return int(min(base_ms * (2**exponent), cap_ms))