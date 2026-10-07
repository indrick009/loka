"""The search projection consumer's payload contract.

A malformed property event is permanent: retrying it would fail identically for
ever. These cases pin down which payloads are refused permanently, so a bad
producer shows up as a DLQ entry rather than a retry loop that never drains.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from loka.interfaces.worker.search_projection_consumer import (
    SEARCH_PROJECTION_QUEUE,
    parse_property_id,
)
from loka.shared.infrastructure.broker.consumer import PermanentHandlerError
from loka.shared.infrastructure.broker.topology import QUEUES

pytestmark = pytest.mark.unit


def envelope(**metadata: Any) -> dict[str, Any]:
    payload = {"property_id": str(uuid.uuid4()), **metadata}
    return {
        "message_id": str(uuid.uuid4()),
        "event_type": "PropertyPublished",
        "payload": {
            "aggregate_type": "Property",
            "aggregate_id": payload.get("property_id"),
            "metadata": payload,
        },
    }


def test_the_property_id_is_extracted() -> None:
    body = envelope()
    assert parse_property_id(body) == uuid.UUID(body["payload"]["metadata"]["property_id"])


def test_the_queue_is_declared_and_bound() -> None:
    spec = next(item for item in QUEUES if item.name == SEARCH_PROJECTION_QUEUE)
    assert "PropertyPublished" in spec.routing_keys
    assert "PropertyRented" in spec.routing_keys
    assert "PropertyMarkedUnavailable" in spec.routing_keys


def test_a_payload_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(PermanentHandlerError, match="missing or not an object"):
        parse_property_id({"payload": "not-an-object"})


def test_a_payload_without_metadata_is_refused() -> None:
    with pytest.raises(PermanentHandlerError, match="has no metadata"):
        parse_property_id({"payload": {"aggregate_id": str(uuid.uuid4())}})


def test_a_missing_property_id_is_refused() -> None:
    body = envelope()
    del body["payload"]["metadata"]["property_id"]
    with pytest.raises(PermanentHandlerError, match="property_id"):
        parse_property_id(body)


def test_a_malformed_property_id_is_refused() -> None:
    with pytest.raises(PermanentHandlerError, match="not a uuid"):
        parse_property_id(envelope(property_id="not-a-uuid"))


def test_a_blank_property_id_is_refused() -> None:
    with pytest.raises(PermanentHandlerError, match="property_id"):
        parse_property_id(envelope(property_id="   "))