"""Property directory adapter for the Rental context.

Crosses a context boundary: the rental lifecycle needs to read a listing's
owner and to reserve / release / rent it. All property access is channelled
through this adapter so the rental domain keeps depending on its own port.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    PropertyRepository,
)
from loka.bounded_contexts.rental.application.ports import PropertySnapshot
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound

PROPERTY_REPOSITORY = "property"


class SqlAlchemyPropertyDirectory:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    @property
    def _repository(self) -> PropertyRepository:
        repository: PropertyRepository = self._uow.repository(PROPERTY_REPOSITORY)
        return repository

    async def snapshot(self, property_id: uuid.UUID) -> PropertySnapshot | None:
        prop = await self._repository.get(property_id)
        if prop is None:
            return None
        return PropertySnapshot(
            property_id=prop.id,
            landlord_id=prop.landlord_id,
            is_available=prop.is_publicly_visible,
        )

    async def reserve(
        self,
        property_id: uuid.UUID,
        *,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        prop = await self._load(property_id)
        prop.reserve(now=now, actor_id=actor_id)
        await self._persist(prop)

    async def release_reservation(self, property_id: uuid.UUID, *, now: datetime) -> None:
        prop = await self._load(property_id)
        prop.release_reservation(now=now)
        await self._persist(prop)

    async def mark_rented(
        self,
        property_id: uuid.UUID,
        *,
        now: datetime,
        actor_id: uuid.UUID | None = None,
    ) -> None:
        prop = await self._load(property_id)
        prop.mark_as_rented(now=now, actor_id=actor_id)
        await self._persist(prop)

    async def _load(self, property_id: uuid.UUID) -> Property:
        prop = await self._repository.get(property_id)
        if prop is None:
            raise ResourceNotFound(
                "property not found", context={"property_id": str(property_id)}
            )
        return prop

    async def _persist(self, prop: Property) -> None:
        await self._repository.save(prop)
        self._uow.collect(prop)