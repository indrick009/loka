"""Transport-agnostic domain events.

Events are plain frozen dataclasses. They know nothing about RabbitMQ,
JSON schemas or database rows; the infrastructure layer maps them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Base class for every business event.

    ``aggregate_version`` lets consumers detect gaps or duplicates when a
    stream is replayed out of order.
    """

    aggregate_type: str
    aggregate_id: uuid.UUID
    aggregate_version: int
    occurred_at: datetime
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    event_type: str = field(default="DomainEvent")
    actor_id: uuid.UUID | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "aggregate_version": self.aggregate_version,
            "occurred_at": self.occurred_at.isoformat(),
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "metadata": self.metadata,
        }


@dataclass(frozen=True, slots=True)
class IntegrationEvent:
    """Envelope used to move an event across process boundaries."""

    message_id: uuid.UUID
    event_type: str
    occurred_at: datetime
    correlation_id: str
    causation_id: str | None
    payload: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "message_id": str(self.message_id),
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "payload": self.payload,
        }