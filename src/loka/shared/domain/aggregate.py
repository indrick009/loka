"""Aggregate root base class.

Aggregates are the only place where consistency is enforced in a single
transaction. They record domain events which the application layer drains
into the outbox after a successful commit.
"""

from __future__ import annotations

import uuid
from abc import ABC
from datetime import datetime
from typing import Any

from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.entity import Entity
from loka.shared.domain.events import DomainEvent


class AggregateRoot(Entity, ABC):
    __slots__ = ("_events", "_pending_version", "_persisted_version", "aggregate_type")

    def __init__(self, *, aggregate_type: str | None = None) -> None:
        self._events: list[DomainEvent] = []
        self._persisted_version = 0
        self._pending_version = 0
        self.aggregate_type = aggregate_type or type(self).__name__

    @property
    def version(self) -> int:
        """Version this aggregate will have once the next commit succeeds."""
        return self._pending_version

    @property
    def persisted_version(self) -> int:
        """Version currently stored; the guard used by optimistic locking."""
        return self._persisted_version

    @property
    def has_pending_events(self) -> bool:
        return bool(self._events)

    def record(
        self,
        event_type: str,
        *,
        occurred_at: datetime,
        payload: dict[str, Any] | None = None,
        aggregate_id: uuid.UUID | None = None,
        actor_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> DomainEvent:
        """Append a business fact to this aggregate's stream."""
        self._pending_version += 1
        event = DomainEvent(
            event_type=event_type,
            aggregate_type=self.aggregate_type,
            aggregate_id=aggregate_id or self.id,
            aggregate_version=self.version,
            occurred_at=ensure_utc(occurred_at),
            actor_id=actor_id,
            metadata={**(payload or {}), **metadata} if metadata else (payload or {}),
        )
        self._events.append(event)
        return event

    def pull_events(self) -> list[DomainEvent]:
        """Drain pending events; the caller publishes them after commit."""
        drained = list(self._events)
        self._events.clear()
        return drained

    def mark_persisted(self, version: int) -> None:
        """Called by repositories once the aggregate is durably stored."""
        self._persisted_version = version
        self._pending_version = version

    def _bump(self) -> None:
        self._pending_version += 1