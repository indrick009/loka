"""Search projection consumer.

Property lifecycle events drive the public search read model. The consumer
never trusts the event payload for the document contents: it extracts the
property id and lets the projector read the current aggregate, which keeps the
projection convergent under redelivery and reordering.

A message with an unparseable property id is a permanent failure — retrying it
would fail identically — so it goes to the DLQ. A property that no longer
exists is *not* a failure: the projector turns that into a no-op.
"""

from __future__ import annotations

import uuid
from typing import Any

from loka.bounded_contexts.property.infrastructure.composition import (
    PROPERTY_SEARCH_PROJECTOR,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

SEARCH_PROJECTION_QUEUE = "search.projections"

_logger = get_logger(__name__)


class SearchProjectionConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        property_id = parse_property_id(payload)
        async with unit_of_work(self._database) as uow:
            changed = await uow.repository(PROPERTY_SEARCH_PROJECTOR).project(property_id)
            await uow.commit()
        _logger.info(
            "search_projection_processed",
            property_id=str(property_id),
            changed=changed,
        )


def parse_property_id(payload: dict[str, Any]) -> uuid.UUID:
    """Extract the property id from the event envelope, or refuse it forever."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("property event payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("property event payload has no metadata")
    value = metadata.get("property_id")
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError("property event field 'property_id' is missing")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(
            f"property event field 'property_id' is not a uuid: {value}"
        ) from exc