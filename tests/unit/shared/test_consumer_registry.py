"""A queue with no handler must never acknowledge its messages."""

from __future__ import annotations

from typing import Any

import pytest

from loka.interfaces.workers.registry import handler_registry
from loka.shared.infrastructure.broker.consumer import PermanentHandlerError
from loka.shared.infrastructure.broker.topology import QUEUES
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.worker.consumers import (
    UnimplementedConsumer,
    build_consumers,
)


class _FakeBroker:
    def __init__(self) -> None:
        self.declared = 0

    async def declare_topology(self) -> None:
        self.declared += 1


def test_every_queue_has_a_consumer() -> None:
    consumers = build_consumers(_FakeBroker(), object(), None, {})  # type: ignore[arg-type]
    assert {consumer.queue_spec.name for consumer in consumers} == {spec.name for spec in QUEUES}


def test_no_handler_means_the_message_is_rejected() -> None:
    spec = QUEUES[0]
    consumer = UnimplementedConsumer(spec, _FakeBroker(), object())  # type: ignore[arg-type]

    with pytest.raises(PermanentHandlerError) as excinfo:
        _run(consumer.handle({"anything": "at all"}))

    assert spec.name in str(excinfo.value)


def test_queues_without_a_handler_fall_back_to_the_failing_consumer() -> None:
    registry = handler_registry()
    consumers = build_consumers(
        _FakeBroker(), object(), Settings(), registry  # type: ignore[arg-type]
    )
    implemented = {
        consumer.queue_spec.name
        for consumer in consumers
        if not isinstance(consumer, UnimplementedConsumer)
    }
    assert implemented == set(registry)
    assert implemented, "at least one queue must have a real handler"


def test_the_inbound_queue_is_the_first_one_implemented() -> None:
    assert "whatsapp.incoming" in handler_registry()


def test_a_handler_for_an_unknown_queue_is_refused() -> None:
    class _Fake(UnimplementedConsumer):
        async def handle(self, payload: dict[str, Any]) -> None:
            return None

    with pytest.raises(LookupError, match=r"typo\.queue"):
        build_consumers(
            _FakeBroker(), object(), None, {"typo.queue": _Fake}  # type: ignore[arg-type]
        )


def test_registry_uses_a_registered_handler_when_present() -> None:
    class _FakeConsumer(UnimplementedConsumer):
        async def handle(self, payload: dict[str, Any]) -> None:
            return None

    spec = next(item for item in QUEUES if item.name == "whatsapp.incoming")
    consumers = build_consumers(
        _FakeBroker(), object(), None, {spec.name: _FakeConsumer}  # type: ignore[arg-type]
    )
    matched = [c for c in consumers if c.queue_spec.name == spec.name]
    assert matched and isinstance(matched[0], _FakeConsumer)


def _run(coroutine: Any) -> Any:
    import asyncio

    return asyncio.run(coroutine)
