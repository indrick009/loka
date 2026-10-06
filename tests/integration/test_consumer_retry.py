"""Retry and dead-letter behaviour against real RabbitMQ.

A failing handler must end up in the DLQ exactly once, after the configured
retries, and must never be lost or silently acknowledged. These tests drive the
real ``ConsumerBase`` loop, so the routing and acknowledgement decisions under
test are the production ones.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec, delay_queue_name
from loka.shared.infrastructure.config.settings import Settings

pytestmark = pytest.mark.integration

SPEC = QueueSpec("retry-test.notifications", routing_keys=("RetryTestRequested",), max_retries=3)
DLQ = f"{SPEC.name}.dlq"


class FailingConsumer(ConsumerBase):
    queue_spec = SPEC

    def __init__(self, broker: Broker) -> None:
        super().__init__(broker, concurrency=1)
        self.calls: list[dict[str, Any]] = []

    async def handle(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)
        raise RuntimeError("handler exploded")


class PermanentFailingConsumer(FailingConsumer):
    async def handle(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)
        raise PermanentHandlerError("unprocessable payload")


class SucceedingConsumer(FailingConsumer):
    async def handle(self, payload: dict[str, Any]) -> None:
        self.calls.append(payload)


@pytest_asyncio.fixture
async def broker(integration_settings: Settings) -> AsyncIterator[Broker]:
    connection = Broker(
        integration_settings.broker.model_copy(update={"publish_timeout_seconds": 2.0})
    )
    await asyncio.wait_for(connection.connect(), timeout=15)
    await asyncio.wait_for(connection.declare_queue(SPEC), timeout=30)
    for name in (SPEC.name, DLQ):
        await (await connection.channel.get_queue(name)).purge()
    yield connection
    for name in (SPEC.name, DLQ, delay_queue_name(SPEC.name)):
        await (await connection.channel.get_queue(name)).delete(if_unused=False, if_empty=False)
    await connection.close()


async def _drain(broker: Broker, queue_name: str, *, settle: float = 1.0) -> list[dict[str, Any]]:
    """Collect every message that reaches a queue once the flow has settled."""
    queue = await broker.channel.get_queue(queue_name)
    await asyncio.sleep(settle)
    bodies: list[dict[str, Any]] = []
    while True:
        message = await queue.get(timeout=0.5, fail=False)
        if message is None:
            break
        bodies.append(json.loads(message.body.decode()))
        await message.ack()
    return bodies


async def _publish(broker: Broker) -> None:
    await broker.publish(
        routing_key="RetryTestRequested",
        payload={"payload": {"event_type": "RetryTestRequested"}, "correlation_id": "retry-e2e"},
        headers={"x-correlation-id": "retry-e2e"},
    )


async def test_a_failing_handler_is_retried_then_lands_in_the_dlq_once(broker: Broker) -> None:
    consumer = FailingConsumer(broker)
    await consumer.start()
    await _publish(broker)

    dead_lettered = await _drain(broker, DLQ, settle=6.0)

    await consumer.stop()
    assert len(consumer.calls) == SPEC.max_retries, "one call per delivery, no hot loop"
    assert len(dead_lettered) == 1, "the DLQ must hold exactly one copy of the message"


async def test_a_permanent_failure_skips_retries(broker: Broker) -> None:
    consumer = PermanentFailingConsumer(broker)
    await consumer.start()
    await _publish(broker)

    dead_lettered = await _drain(broker, DLQ, settle=2.0)

    await consumer.stop()
    assert len(consumer.calls) == 1, "an unretryable error must not be retried"
    assert len(dead_lettered) == 1


async def test_a_successful_handler_leaves_no_dead_letter(broker: Broker) -> None:
    consumer = SucceedingConsumer(broker)
    await consumer.start()
    await _publish(broker)

    dead_lettered = await _drain(broker, DLQ, settle=1.5)

    await consumer.stop()
    assert len(consumer.calls) == 1
    assert dead_lettered == []
