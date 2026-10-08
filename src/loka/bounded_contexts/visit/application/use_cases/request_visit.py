"""Request a visit to a property."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from loka.bounded_contexts.visit.application.dto.visit_dto import VisitView
from loka.bounded_contexts.visit.application.ports import VisitPropertyDirectory
from loka.bounded_contexts.visit.domain.entities.visit import Visit
from loka.bounded_contexts.visit.domain.repositories.visit_repository import (
    VISIT_REPOSITORY,
    VisitRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class RequestVisitCommand:
    property_id: uuid.UUID
    tenant_id: uuid.UUID
    preferred_date: date | None = None
    message: str | None = None


class RequestVisitUseCase(UseCase[RequestVisitCommand, VisitView]):
    name = "request_visit"

    def __init__(self, uow: UnitOfWork, *, properties: VisitPropertyDirectory) -> None:
        super().__init__(uow)
        self._properties = properties

    async def execute(  # type: ignore[override]
        self, command: RequestVisitCommand, *, now: datetime
    ) -> VisitView:
        target = await self._properties.lookup(command.property_id)
        if target is None:
            raise ResourceNotFound(
                "property not found", context={"property_id": str(command.property_id)}
            )
        repository: VisitRepository = self._uow.repository(VISIT_REPOSITORY)
        visit = Visit(
            visit_id=new_id(),
            property_id=target.property_id,
            landlord_id=target.landlord_id,
            tenant_id=command.tenant_id,
            preferred_date=command.preferred_date,
            message=command.message,
            now=now,
        )
        await repository.add(visit)
        self._uow.collect(visit)
        await self._uow.commit()
        return VisitView.from_aggregate(visit)