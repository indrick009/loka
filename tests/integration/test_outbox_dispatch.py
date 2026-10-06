"""Outbox dispatch against real RabbitMQ.

These tests guard the path a lost or misrouted event would break: commit ->
outbox -> claim -> publish -> queue binding -> consume. They drive the real
``OutboxDispatcher`` so the production routing decision is covered, not a
re-implementation of it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from loka.shared.domain.events import DomainEvent
from loka.shared.infrastructure.broker.topology import QUEUES, Broker
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxStore
from loka.shared.infrastructure.worker.outbox_dispatcher import OutboxDispatcher

pytestmark = pytest.mark.integration


def _event(event_type: str) -> DomainEvent:
    return DomainEvent(
        event_type=event_type,
        aggregate_type="Property",
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        occurred_at=datetime.now(UTC),
        metadata={"landlord_id": str(uuid.uuid4())},
    )


@pytest_asyncio.fixture
async def broker(integration_settings: Settings) -> AsyncIterator[Broker]:
    """Function scoped on purpose: each test runs on its own event loop, and an
    aio-pika connection cannot be shared across loops. Queues are purged so each
    test only ever observes the messages it published itself."""
    connection = Broker(integration_settings.broker)
    await asyncio.wait_for(connection.connect(), timeout=15)
    await asyncio.wait_for(connection.declare_topology(), timeout=30)
    for spec in QUEUES:
        await (await connection.channel.get_queue(spec.name)).purge()
    yield connection
    await connection.close()


async def _consume(broker: Broker, queue_name: str, *, expected: int) -> list[dict]:
    """Read up to ``expected`` messages, bounded so a routing bug fails fast."""
    queue = await broker.channel.get_queue(queue_name)
    bodies: list[dict] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 5
    while len(bodies) < expected and loop.time() < deadline:
        message = await queue.get(timeout=1, fail=False)
        if message is None:
            continue
        bodies.append(json.loads(message.body.decode()))
        await message.ack()
    return bodies


async def _stage(database: Database, events: list[DomainEvent], correlation_id: str) -> None:
    async with database.session() as session:
        await OutboxStore(session).append(events, correlation_id=correlation_id)
        # Sessions run with autoflush off, like a unit of work that stages
        # events and flushes once before commit.
        await session.flush()


async def test_dispatch_publishes_to_the_queue_bound_to_the_event_type(
    broker: Broker, database: Database
) -> None:
    await _stage(database, [_event("PropertyPublished")], "dispatch-test")

    dispatched = await OutboxDispatcher(database, broker).dispatch_once()
    assert dispatched == 1

    payloads = await _consume(broker, "search.projections", expected=1)
    assert [payload["payload"]["event_type"] for payload in payloads] == ["PropertyPublished"]
    assert payloads[0]["correlation_id"] == "dispatch-test"

    async with database.session() as session:
        assert await OutboxStore(session).claim_batch(limit=10) == []


async def test_dispatch_keeps_unrelated_queues_clean(
    broker: Broker, database: Database
) -> None:
    await _stage(
        database,
        [_event("PropertyPublished"), _event("PaymentSucceeded")],
        "routing-test",
    )

    assert await OutboxDispatcher(database, broker).dispatch_once() == 2

    search_types = {
        payload["payload"]["event_type"]
        for payload in await _consume(broker, "search.projections", expected=1)
    }
    assert search_types == {"PropertyPublished"}

    payment_types = {
        payload["payload"]["event_type"]
        for payload in await _consume(broker, "payments.events", expected=1)
    }
    assert payment_types == {"PaymentSucceeded"}


async def test_dispatch_leaves_the_event_claimable_when_publish_fails(
    integration_settings: Settings, database: Database
) -> None:
    await _stage(database, [_event("PropertyPublished")], "retry-test")

    # A closed robust channel is the failure mode this guards: exchange
    # resolution then blocks forever instead of raising.
    broken = Broker(
        integration_settings.broker.model_copy(update={"publish_timeout_seconds": 1.0})
    )
    await broken.connect()
    await broken.channel.close()

    assert await OutboxDispatcher(database, broken).dispatch_once() == 0

    async with database.session() as session:
        pending = await OutboxStore(session).claim_batch(limit=10)
        assert [row.correlation_id for row in pending] == ["retry-test"]
        assert pending[0].attempts >= 1
        assert pending[0].last_error

    await broken.close()


async def test_connect_reopens_a_dead_channel(integration_settings: Settings) -> None:
    connection = Broker(
        integration_settings.broker.model_copy(update={"publish_timeout_seconds": 2.0})
    )
    await connection.connect()
    dead_channel = connection.channel
    await dead_channel.close()

    await connection.connect()

    assert connection.channel is not dead_channel
    assert not connection.channel.is_closed
    await connection.publish(
        routing_key="PropertyPublished",
        payload={"event_type": "PropertyPublished", "payload": {}},
    )
    await connection.close()
