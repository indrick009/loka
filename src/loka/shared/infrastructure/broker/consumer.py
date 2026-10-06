"""Idempotent consumer base.

Contract for every RabbitMQ consumer in the system:

* ``x-retry-attempt`` tracks the delivery count instead of hot-looping;
* a retryable failure is parked in the queue's delay queue, so exponential
  backoff costs no consumer slot;
* after ``max_retries`` the broker dead-letters the message to the DLQ;
* every delivery ends in exactly one terminal action: ack, reject, or
  republish-then-ack;
* handler exceptions never crash the worker loop.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import AbstractIncomingMessage, AbstractQueue

from loka.shared.application.context import (
    current_context,
    reset_causation_id,
    reset_correlation_id,
    set_causation_id,
    set_correlation_id,
)
from loka.shared.infrastructure.broker.topology import (
    Broker,
    QueueSpec,
    decode_message,
    delay_queue_name,
    header_int,
    header_str,
    retry_delay_ms,
)
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


class RetryableHandlerError(Exception):
    """Transient failure: the message is worth retrying."""


class PermanentHandlerError(Exception):
    """Unretryable failure: straight to the DLQ."""


class ConsumerBase(ABC):
    queue_spec: QueueSpec

    def __init__(self, broker: Broker, *, concurrency: int = 8) -> None:
        self._broker = broker
        self._concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self._queue: AbstractQueue | None = None
        self._consumer_tag: str | None = None

    @abstractmethod
    async def handle(self, payload: dict[str, Any]) -> None:
        """Process one message. Idempotency is the handler's responsibility."""

    async def start(self) -> None:
        await self._broker.declare_topology()
        queue = await self._broker.channel.get_queue(self.queue_spec.name)
        self._consumer_tag = await queue.consume(self._on_message, no_ack=False)
        self._queue = queue
        _logger.info(
            "consumer_started", queue=self.queue_spec.name, concurrency=self._concurrency
        )

    async def stop(self) -> None:
        queue, tag = self._queue, self._consumer_tag
        self._queue, self._consumer_tag = None, None
        if queue is not None and tag is not None:
            await queue.cancel(tag)
            _logger.info("consumer_stopped", queue=self.queue_spec.name)

    async def _on_message(self, message: AbstractIncomingMessage) -> None:
        payload = decode_message(message)
        headers = message.headers or {}
        correlation_id = (
            header_str(headers, "x-correlation-id")
            or str(payload.get("correlation_id") or "unknown")
        )
        corr_token = set_correlation_id(correlation_id)
        caus_token = set_causation_id(header_str(headers, "message_id"))
        attempt = header_int(headers, "x-retry-attempt")
        try:
            try:
                async with self._semaphore:
                    await self.handle(payload)
            except PermanentHandlerError as exc:
                _logger.error(
                    "message_rejected_permanently",
                    queue=self.queue_spec.name,
                    reason=str(exc),
                )
                await self._dead_letter(message, reason=str(exc) or type(exc).__name__)
            except Exception as exc:
                await self._handle_failure(message, exc, attempt)
            else:
                _logger.info(
                    "message_processed",
                    queue=self.queue_spec.name,
                    message_id=str(message.message_id),
                    attempt=attempt,
                )
                await message.ack()
        finally:
            reset_causation_id(caus_token)
            reset_correlation_id(corr_token)

    async def _handle_failure(
        self, message: AbstractIncomingMessage, exc: Exception, attempt: int
    ) -> None:
        spec = self.queue_spec
        if attempt + 1 >= spec.max_retries:
            _logger.error(
                "message_exhausted_retries",
                queue=spec.name,
                attempts=attempt + 1,
                error=str(exc) or type(exc).__name__,
            )
            await self._dead_letter(
                message, reason=str(exc) or type(exc).__name__, attempts=attempt + 1
            )
            return

        delay = retry_delay_ms(attempt + 1)
        _logger.warning(
            "message_retry_scheduled",
            queue=spec.name,
            attempt=attempt + 1,
            delay_ms=delay,
            error=str(exc) or type(exc).__name__,
        )
        headers = dict(message.headers or {})
        headers["x-retry-attempt"] = attempt + 1
        headers["x-last-error"] = (str(exc) or type(exc).__name__)[:512]
        # Park the retry in the delay queue and ack the original: the queue
        # dead-letters the parked copy back here once its expiration elapses.
        # Publishing before the ack means a crash between the two duplicates the
        # attempt rather than losing the message.
        await self._broker.channel.default_exchange.publish(
            Message(
                body=message.body,
                headers=headers,
                content_type=message.content_type,
                delivery_mode=DeliveryMode.PERSISTENT,
                correlation_id=message.correlation_id,
                expiration=delay / 1000,
            ),
            routing_key=delay_queue_name(spec.name),
        )
        await message.ack()

    async def _dead_letter(
        self,
        message: AbstractIncomingMessage,
        *,
        reason: str,
        attempts: int | None = None,
    ) -> None:
        """Park the message in the queue's DLQ, keeping the reason.

        A plain ``reject`` would dead-letter the body but drop the reason on the
        floor: the DLQ would hold deliveries nobody could explain once the logs
        had rotated. Republishing to the DLQ through the default exchange
        carries the reason in headers, and because the original is acked (not
        rejected) the work queue's dead-letter exchange adds no second copy.

        If the publish fails the message is rejected instead, so a broker
        hiccup parks the delivery rather than losing it.
        """
        headers = dict(message.headers or {})
        headers["x-last-error"] = reason[:512]
        headers["x-dead-lettered-at"] = datetime.now(UTC).isoformat()
        if attempts is not None:
            headers["x-retry-attempt"] = attempts
        try:
            await self._broker.channel.default_exchange.publish(
                Message(
                    body=message.body,
                    headers=headers,
                    content_type=message.content_type,
                    delivery_mode=DeliveryMode.PERSISTENT,
                    correlation_id=message.correlation_id,
                ),
                routing_key=self._broker.dead_letter_queue_name(self.queue_spec.name),
            )
        except Exception as exc:
            _logger.error(
                "dead_letter_publish_failed",
                queue=self.queue_spec.name,
                error=str(exc) or type(exc).__name__,
            )
            await message.reject(requeue=False)
            return
        await message.ack()


def exchange_type_for(spec: QueueSpec) -> ExchangeType:
    return ExchangeType.TOPIC


Handler = Callable[[dict[str, Any]], Awaitable[None]]


def context_snapshot() -> dict[str, Any]:
    return current_context()