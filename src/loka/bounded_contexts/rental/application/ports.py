"""Ports the Rental context depends on.

The Rental context never reads the property write model directly: it goes
through ``PropertyDirectory``, which also lets it trigger the cross-context
availability changes (reserve / rent / release) that closing a rental implies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PropertySnapshot:
    """Read-only facts about a listing that a rental decision needs."""

    property_id: uuid.UUID
    landlord_id: uuid.UUID
    is_available: bool


@runtime_checkable
class PropertyDirectory(Protocol):
    async def snapshot(self, property_id: uuid.UUID) -> PropertySnapshot | None: ...

    async def reserve(
        self,
        property_id: uuid.UUID,
        *,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None: ...

    async def release_reservation(self, property_id: uuid.UUID, *, now: datetime) -> None: ...

    async def mark_rented(
        self,
        property_id: uuid.UUID,
        *,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None: ...