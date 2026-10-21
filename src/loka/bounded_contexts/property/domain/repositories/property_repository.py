"""Property repository ports (domain-facing)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

from loka.bounded_contexts.property.domain.entities.availability_check import (
    AvailabilityCheck,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.tenant_demand import TenantDemand


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

class AvailabilityCheckRepository(Protocol):
    """The open question about a listing, and the landlord's answers.

    ``get_open_for_listing`` is what makes asking idempotent: a second tenant
    interested in the same lodging joins the question already pending instead of
    sending the landlord the same message twice.

    ``get_open_for_landlord`` is how an answer finds its question. A landlord
    replies "c'est déjà loué" in a WhatsApp thread that names no listing, so the
    only thing to go on is which of his listings he was last asked about.
    """

    async def get(self, check_id: Any) -> AvailabilityCheck | None: ...

    async def get_open_for_listing(self, property_id: Any) -> AvailabilityCheck | None: ...

    async def get_open_for_landlord(self, landlord_id: Any) -> AvailabilityCheck | None: ...

    async def latest_answered_for_listing(
        self, property_id: Any
    ) -> AvailabilityCheck | None: ...

    async def add(self, check: AvailabilityCheck) -> None: ...

    async def save(self, check: AvailabilityCheck) -> None: ...


class TenantDemandRepository(Protocol):
    """A tenant's open request, and the ones still owed an answer.

    ``get_open_for_tenant`` is what makes a refined search the same request
    rather than a new one: he changes the neighbourhood as he talks, and the
    demand keeps its identity and the time it has been waiting.

    ``watching_demands`` is how a new listing finds the tenants it may suit:
    those never answered and those answered who are still looking. Oldest
    first, because the person who has waited longest is the one to serve.
    """

    async def get(self, demand_id: Any) -> TenantDemand | None: ...

    async def get_open_for_tenant(self, tenant_id: Any) -> TenantDemand | None: ...

    async def watching_demands(self, *, limit: int) -> list[TenantDemand]: ...

    async def watching_for_tenant(self, tenant_id: Any) -> list[TenantDemand]: ...

    async def due_reminders(self, *, now: datetime, limit: int) -> list[TenantDemand]: ...

    async def lapsed_reminders(self, *, now: datetime, limit: int) -> list[TenantDemand]: ...

    async def add(self, demand: TenantDemand) -> None: ...

    async def save(self, demand: TenantDemand) -> None: ...
