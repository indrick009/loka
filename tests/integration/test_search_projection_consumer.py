"""The search projection consumer driven by the real broker.

``PropertyPublished`` / ``PropertyRented`` / ``PropertyMarkedUnavailable`` ->
RabbitMQ -> ``search.projections`` -> ``property_search_documents``.

The projector reads the current aggregate rather than the event payload, so
these tests insert the write model first and then drive the event.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio

from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    Duration,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertySearchDocumentRow,
)
from loka.bounded_contexts.property.infrastructure.persistence.property_repository import (
    SqlAlchemyPropertyRepository,
)
from loka.interfaces.worker.search_projection_consumer import (
    SEARCH_PROJECTION_QUEUE,
    SearchProjectionConsumer,
)
from loka.shared.infrastructure.broker.topology import QUEUES, Broker
from loka.shared.infrastructure.config.settings import Settings

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def broker(integration_settings: Settings) -> AsyncIterator[Broker]:
    connection = Broker(integration_settings.broker)
    await asyncio.wait_for(connection.connect(), timeout=15)
    await asyncio.wait_for(connection.declare_topology(), timeout=30)
    for spec in QUEUES:
        for queue_name in (spec.name, f"{spec.name}{integration_settings.broker.dlq_suffix}"):
            await (await connection.channel.get_queue(queue_name)).purge()
    yield connection
    await connection.close()


@pytest_asyncio.fixture
async def consumer(
    broker: Broker, database: Any, integration_settings: Any
) -> AsyncIterator[Any]:
    spec = next(item for item in QUEUES if item.name == SEARCH_PROJECTION_QUEUE)
    handler = SearchProjectionConsumer(spec, broker, database, integration_settings)
    await handler.start()
    yield handler
    await handler.stop()


def _listing(*, city: str = "Douala", now: datetime = NOW) -> Property:
    landlord_id = uuid.uuid4()
    prop = Property(property_id=uuid.uuid4(), landlord_id=landlord_id, now=now)
    prop.set_type(PropertyType.APARTMENT, now=now)
    prop.set_location(Location(city=city, neighbourhood="Bali"), now=now)
    prop.set_rent(Money(150_000), now=now)
    prop.set_charges(Money(25_000), ChargingPolicy.EXTRA, now=now)
    prop.set_deposit(Money(300_000), now=now)
    prop.set_rooms(BedroomCount(2, 1), SurfaceArea(65), now=now)
    prop.set_minimum_duration(Duration(6), now=now)
    prop.set_availability(AvailabilityWindow(now.date() + timedelta(days=5)), now=now)
    prop.set_conditions("Recently renovated", now=now)
    prop.add_media(
        PropertyMedia(
            media_id=uuid.uuid4(),
            kind=MediaKind.PHOTO,
            object_key=f"landlords/{landlord_id}/{prop.id}/1.jpg",
            status=MediaStatus.UPLOADED,
        ),
        now=now,
    )
    return prop


async def _persist(database: Any, prop: Property) -> None:
    async with database.session() as session:
        await SqlAlchemyPropertyRepository(session).add(prop)
        await session.commit()


def _event(property_id: uuid.UUID, *, event_type: str = "PropertyPublished") -> dict[str, Any]:
    """The envelope exactly as the outbox dispatcher serialises it."""
    return {
        "message_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": NOW.isoformat(),
        "correlation_id": "search-projection-test",
        "causation_id": None,
        "payload": {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "aggregate_type": "Property",
            "aggregate_id": str(property_id),
            "aggregate_version": 2,
            "occurred_at": NOW.isoformat(),
            "actor_id": None,
            "metadata": {"property_id": str(property_id)},
        },
    }


async def _publish(
    broker: Any, event: dict[str, Any], *, routing_key: str | None = None
) -> None:
    await broker.publish(routing_key=routing_key or str(event["event_type"]), payload=event)


async def _document(database: Any, property_id: uuid.UUID) -> PropertySearchDocumentRow | None:
    async with database.session() as session:
        return await session.get(PropertySearchDocumentRow, property_id)


async def _wait_for_document(
    database: Any, property_id: uuid.UUID, *, timeout: float = 10.0
) -> PropertySearchDocumentRow | None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        document = await _document(database, property_id)
        if document is not None:
            return document
        await asyncio.sleep(0.2)
    return None


async def _dlq_message(broker: Any, settings: Any) -> Any:
    queue = f"{SEARCH_PROJECTION_QUEUE}{settings.broker.dlq_suffix}"
    return await (await broker.channel.get_queue(queue)).get(timeout=5, fail=False)


async def test_a_published_property_reaches_the_read_model(
    consumer: Any, broker: Any, database: Any
) -> None:
    prop = _listing()
    prop.publish(now=NOW, actor_id=prop.landlord_id)
    prop.mark_verified(now=NOW)
    await _persist(database, prop)

    await _publish(broker, _event(prop.id))

    document = await _wait_for_document(database, prop.id)
    assert document is not None
    assert document.status == "AVAILABLE"
    assert document.city == "Douala"
    assert document.is_verified is True
    assert document.public_price_xaf == 175_000


async def test_a_rented_property_leaves_search(
    consumer: Any, broker: Any, database: Any
) -> None:
    prop = _listing()
    prop.publish(now=NOW, actor_id=prop.landlord_id)
    prop.mark_verified(now=NOW)
    prop.mark_as_rented(now=NOW, actor_id=prop.landlord_id)
    await _persist(database, prop)

    await _publish(broker, _event(prop.id, event_type="PropertyRented"))

    document = await _wait_for_document(database, prop.id)
    assert document is not None
    assert document.status == "RENTED"


async def test_a_malformed_event_is_parked_not_retried(
    consumer: Any, broker: Any, database: Any, integration_settings: Any
) -> None:
    await _publish(broker, {"payload": {"metadata": {}}}, routing_key="PropertyPublished")

    message = await _dlq_message(broker, integration_settings)
    assert message is not None
    headers = message.headers or {}
    assert "property_id" in str(headers.get("x-last-error", ""))
    await message.ack()


async def test_an_unknown_property_is_a_no_op(
    consumer: Any, broker: Any, database: Any, integration_settings: Any
) -> None:
    await _publish(broker, _event(uuid.uuid4()))

    await asyncio.sleep(1.5)
    assert await _document(database, uuid.uuid4()) is None
    assert await _dlq_message(broker, integration_settings) is None