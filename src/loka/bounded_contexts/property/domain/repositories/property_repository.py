"""Property repository ports (domain-facing)."""

from __future__ import annotations

import uuid
from typing import Protocol

from loka.bounded_contexts.property.domain.entities.property import Property


class PropertyRepository(Protocol):
    async def get(self, property_id: uuid.UUID) -> Property | None: ...

    async def add(self, prop: Property) -> None: ...

    async def save(self, prop: Property, *, expected_version: int | None = None) -> None:
        """Raise ``ConcurrencyConflict`` when the version no longer matches."""


class LandlordPropertyQuota(Protocol):
    async def active_count(
        self, landlord_id: uuid.UUID, *, exclude_property_id: uuid.UUID | None = None
    ) -> int:
        """Active listings of a landlord, optionally excluding one property."""


class MarketReference(Protocol):
    """Median rent for a neighbourhood and property type.

    Isolated behind a port so the anomaly detector does not depend on how the
    statistic is computed (SQL window function today, a stats table later).
    """

    async def median_rent(
        self, *, city: str, neighbourhood: str | None, property_type: str | None
    ) -> int | None: ...