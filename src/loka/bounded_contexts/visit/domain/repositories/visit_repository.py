"""Visit repository ports (domain-facing)."""

from __future__ import annotations

import uuid
from typing import Protocol

from loka.bounded_contexts.visit.domain.entities.visit import Visit

VISIT_REPOSITORY = "visit"


class VisitRepository(Protocol):
    async def get(self, visit_id: uuid.UUID) -> Visit | None: ...

    async def add(self, visit: Visit) -> None: ...

    async def save(self, visit: Visit, *, expected_version: int | None = None) -> None:
        """Raise ``ConcurrencyConflict`` when the version no longer matches."""

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[Visit]: ...

    async def list_for_landlord(self, landlord_id: uuid.UUID) -> list[Visit]: ...