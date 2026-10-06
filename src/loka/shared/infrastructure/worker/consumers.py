"""Consumer base and queue-to-consumer assembly.

A queue without a real handler is wired to :class:`UnimplementedConsumer`, which
rejects the message so it is retried and then parked in the DLQ. Draining
messages silently would lose WhatsApp commands and domain events without any
trace, so an unimplemented queue is loud on purpose.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import QUEUES, QueueSpec

if TYPE_CHECKING:
    from loka.shared.infrastructure.broker.topology import Broker
    from loka.shared.infrastructure.config.settings import Settings
    from loka.shared.infrastructure.db.engine import Database


ConsumerFactory = Callable[["QueueSpec", "Broker", "Database", "Settings"], ConsumerBase]


class UnimplementedConsumer(ConsumerBase):
    """Fails loudly instead of acknowledging work it cannot perform."""

    queue_spec: QueueSpec

    def __init__(
        self,
        spec: QueueSpec,
        broker: Broker,
        database: Database,
        settings: Settings | None = None,
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        raise PermanentHandlerError(
            f"no handler registered for queue '{self.queue_spec.name}'; "
            "message parked in the DLQ on purpose"
        )


def build_consumers(
    broker: Broker,
    database: Database,
    settings: Settings,
    handlers: Mapping[str, ConsumerFactory],
) -> list[ConsumerBase]:
    """One consumer per declared queue.

    ``handlers`` comes from the composition root, not from this module: binding
    a queue to a use case must not make ``shared`` depend on a context.
    """
    unknown = set(handlers) - {spec.name for spec in QUEUES}
    if unknown:
        raise LookupError(f"handlers registered for undeclared queues: {sorted(unknown)}")
    consumers: list[ConsumerBase] = []
    for spec in QUEUES:
        handler = handlers.get(spec.name)
        if handler is not None:
            consumers.append(handler(spec, broker, database, settings))
            continue
        consumers.append(UnimplementedConsumer(spec, broker, database, settings))
    return consumers
