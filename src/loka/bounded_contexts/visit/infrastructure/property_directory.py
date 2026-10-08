"""Property directory adapter for the Visit context (read-only)."""

from __future__ import annotations

import uuid

from loka.bounded_contexts.property.domain.repositories.property_repository import (
    PropertyRepository,
)
from loka.bounded_contexts.visit.application.ports import VisitProperty
from loka.shared.application.unit_of_work import UnitOfWork

PROPERTY_REPOSITORY = "property"


class SqlAlchemyVisitPropertyDirectory:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def lookup(self, property_id: uuid.UUID) -> VisitProperty | None:
        repository: PropertyRepository = self._uow.repository(PROPERTY_REPOSITORY)
        prop = await repository.get(property_id)
        if prop is None:
            return None
        return VisitProperty(property_id=prop.id, landlord_id=prop.landlord_id)