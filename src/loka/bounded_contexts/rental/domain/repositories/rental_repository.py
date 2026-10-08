"""Rental repository ports (domain-facing)."""

from __future__ import annotations

import uuid
from typing import Protocol

from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
)

RENTAL_APPLICATION_REPOSITORY = "rental_application"


class RentalApplicationRepository(Protocol):
    async def get(self, application_id: uuid.UUID) -> RentalApplication | None: ...

    async def add(self, application: RentalApplication) -> None: ...

    async def save(
        self, application: RentalApplication, *, expected_version: int | None = None
    ) -> None:
        """Raise ``ConcurrencyConflict`` when the version no longer matches."""

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[RentalApplication]: ...

    async def list_for_landlord(self, landlord_id: uuid.UUID) -> list[RentalApplication]: ...

    async def open_for_property(self, property_id: uuid.UUID) -> list[RentalApplication]:
        """Applications still awaiting a decision, used to gate a property."""