"""Shared kernel unit tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.errors import InvalidStateTransition
from loka.shared.domain.events import DomainEvent


class Order(AggregateRoot):
    __slots__ = ("status",)

    def __init__(self, order_id: uuid.UUID, now: datetime) -> None:
        super().__init__(aggregate_type="Order")
        self._assign_id(order_id)
        self.status = "PENDING"
        self.record("OrderCreated", occurred_at=now, payload={"order_id": str(order_id)})

    def confirm(self, now: datetime) -> None:
        if self.status != "PENDING":
            raise InvalidStateTransition(f"cannot confirm from {self.status}")
        self.status = "CONFIRMED"
        self.record("OrderConfirmed", occurred_at=now, payload={})


def test_entity_equality_is_identity_based() -> None:
    order_id = uuid.uuid4()
    now = datetime.now(UTC)
    assert Order(order_id, now) == Order(order_id, now)
    assert Order(order_id, now) != Order(uuid.uuid4(), now)


def test_aggregate_drains_events_once() -> None:
    order = Order(uuid.uuid4(), datetime.now(UTC))
    order.confirm(datetime.now(UTC))

    first = order.pull_events()
    second = order.pull_events()

    assert [event.event_type for event in first] == ["OrderCreated", "OrderConfirmed"]
    assert second == []
    assert [event.aggregate_version for event in first] == [1, 2]


def test_aggregate_rejects_illegal_transition() -> None:
    order = Order(uuid.uuid4(), datetime.now(UTC))
    order.confirm(datetime.now(UTC))
    with pytest.raises(InvalidStateTransition):
        order.confirm(datetime.now(UTC))


def test_domain_event_payload_is_serialisable() -> None:
    event = DomainEvent(
        event_type="X",
        aggregate_type="Y",
        aggregate_id=uuid.uuid4(),
        aggregate_version=1,
        occurred_at=datetime.now(UTC),
        metadata={"k": "v"},
    )
    payload = event.to_payload()
    assert uuid.UUID(payload["aggregate_id"])
    assert payload["metadata"] == {"k": "v"}