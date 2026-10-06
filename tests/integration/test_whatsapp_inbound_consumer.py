"""Inbound WhatsApp handler driven by the real broker.

Gateway delivery -> RabbitMQ -> consumer -> PostgreSQL, exercised with the
production wiring: the message is published on the exchange the topology binds
``whatsapp.incoming`` to, the registered handler consumes it, and the resulting
row and event are read back from the database.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from loka.bounded_contexts.messaging.infrastructure.persistence.models import (
    ConversationSessionRow,
    InboundMessageRow,
)
from loka.interfaces.worker.whatsapp_inbound_consumer import (
    INBOUND_QUEUE,
    WhatsAppInboundConsumer,
    parse_inbound_payload,
)
from loka.interfaces.workers.registry import handler_registry
from loka.shared.infrastructure.broker.consumer import PermanentHandlerError
from loka.shared.infrastructure.broker.topology import EVENTS_EXCHANGE, QUEUES, Broker
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.outbox import OutboxRecord

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def broker(integration_settings: Settings) -> AsyncIterator[Broker]:
    connection = Broker(integration_settings.broker)
    await asyncio.wait_for(connection.connect(), timeout=15)
    await asyncio.wait_for(connection.declare_topology(), timeout=30)
    for spec in QUEUES:
        # The DLQ too: a parked message from an earlier run would otherwise be
        # read here and make the assertions pass on stale data.
        for queue_name in (
            spec.name,
            f"{spec.name}{integration_settings.broker.dlq_suffix}",
        ):
            await (await connection.channel.get_queue(queue_name)).purge()
    yield connection
    await connection.close()


@pytest_asyncio.fixture
async def consumer(
    broker: Broker, database: Database, integration_settings: Settings
) -> AsyncIterator[WhatsAppInboundConsumer]:
    spec = next(item for item in QUEUES if item.name == INBOUND_QUEUE)
    handler = WhatsAppInboundConsumer(spec, broker, database, integration_settings)
    await handler.start()
    yield handler
    await handler.stop()


def _delivery(**overrides: object) -> dict[str, object]:
    """A gateway delivery as the adapter would publish it."""
    body: dict[str, object] = {
        "provider": "baileys",
        "external_message_id": "BAIL_E1_WIRE",
        "conversation_ref": "699123456@s.whatsapp.net",
        "sender_phone": "699123456",
        "message_kind": "TEXT",
        "provider_timestamp": NOW.isoformat(),
        "text": "Je veux publier un appartement",
        "metadata": {"pushName": "Awa"},
    }
    body.update(overrides)
    return {
        "message_id": str(uuid.uuid4()),
        "event_type": "WhatsAppMessageReceived",
        "occurred_at": NOW.isoformat(),
        "correlation_id": "gateway-correlation",
        "causation_id": None,
        "payload": body,
    }


async def _publish(broker: Broker, delivery: dict[str, object]) -> None:
    await broker.publish(
        exchange=EVENTS_EXCHANGE,
        routing_key="WhatsAppMessageReceived",
        payload=delivery,
    )


async def _wait_for(
    database: Database, statement: object, *, expected: int, timeout: float = 8.0
) -> int:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    count = 0
    while loop.time() < deadline:
        async with database.session() as session:
            count = await session.scalar(statement)  # type: ignore[arg-type]
        if count >= expected:
            return count
        await asyncio.sleep(0.2)
    return count


async def test_the_registry_wires_the_inbound_queue(
    broker: Broker, database: Database, integration_settings: Settings
) -> None:
    """A handler registered against a queue nobody declares would never run."""
    registry = handler_registry()
    assert INBOUND_QUEUE in registry

    spec = next(item for item in QUEUES if item.name == INBOUND_QUEUE)
    built = registry[INBOUND_QUEUE](spec, broker, database, integration_settings)
    assert isinstance(built, WhatsAppInboundConsumer)


async def test_a_gateway_delivery_is_ingested_end_to_end(
    consumer: WhatsAppInboundConsumer, broker: Broker, database: Database
) -> None:
    await _publish(broker, _delivery())

    stored = await _wait_for(
        database,
        select(func.count()).select_from(InboundMessageRow),
        expected=1,
    )
    assert stored == 1

    async with database.session() as session:
        row = await session.scalar(select(InboundMessageRow))
        assert row is not None
        assert row.external_message_id == "BAIL_E1_WIRE"
        assert row.message_kind == "TEXT"
        assert row.session_id is not None
        assert row.metadata_json["pushName"] == "Awa"
        events = await session.scalar(
            select(func.count())
            .select_from(OutboxRecord)
            .where(OutboxRecord.event_type == "WhatsAppMessageIngested")
        )
        assert events == 1
        sessions = await session.scalar(select(func.count()).select_from(ConversationSessionRow))
        assert sessions == 1


async def test_a_redelivered_delivery_is_absorbed(
    consumer: WhatsAppInboundConsumer, broker: Broker, database: Database
) -> None:
    """Two identical deliveries on the wire, one row in the database."""
    delivery = _delivery()
    await _publish(broker, delivery)
    assert await _wait_for(
        database, select(func.count()).select_from(InboundMessageRow), expected=1
    )

    await _publish(broker, delivery)
    await asyncio.sleep(1.5)

    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessageRow)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxRecord)
                .where(OutboxRecord.event_type == "WhatsAppMessageIngested")
            )
            == 1
        )
        assert await session.scalar(select(func.count()).select_from(ConversationSessionRow)) == 1


async def test_two_different_messages_from_one_contact_share_a_session(
    consumer: WhatsAppInboundConsumer, broker: Broker, database: Database
) -> None:
    await _publish(broker, _delivery(external_message_id="BAIL_W_1", text="Bonjour"))
    assert await _wait_for(
        database, select(func.count()).select_from(InboundMessageRow), expected=1
    )
    await _publish(broker, _delivery(external_message_id="BAIL_W_2", text="Deux chambres"))

    assert await _wait_for(
        database, select(func.count()).select_from(InboundMessageRow), expected=2
    )
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(ConversationSessionRow)) == 1


async def test_a_malformed_delivery_is_parked_in_the_dlq(
    consumer: WhatsAppInboundConsumer, broker: Broker, database: Database
) -> None:
    """A payload with no external id can never be de-duplicated: never retry it."""
    await _publish(broker, {"payload": {"provider": "baileys"}})

    dlq = await broker.channel.get_queue(f"{INBOUND_QUEUE}.dlq")
    message = await dlq.get(timeout=8, fail=False)
    assert message is not None
    headers = message.headers or {}
    # The body is untouched so the delivery can be replayed by hand; the reason
    # lives in the headers, which is where an operator looks.
    assert "external_message_id" in str(headers.get("x-last-error", ""))
    await message.ack()

    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(InboundMessageRow)) == 0


def test_a_payload_without_a_body_is_rejected_permanently() -> None:
    with pytest.raises(PermanentHandlerError, match="payload is missing"):
        parse_inbound_payload({"event_type": "WhatsAppMessageReceived"})


def test_a_payload_with_a_non_iso_timestamp_is_rejected_permanently() -> None:
    with pytest.raises(PermanentHandlerError, match="provider_timestamp"):
        parse_inbound_payload(_delivery(provider_timestamp="yesterday"))


def test_blank_text_is_normalised_to_none() -> None:
    command = parse_inbound_payload(_delivery(text="   "))
    assert command.text is None
    assert command.message_kind == "TEXT"
