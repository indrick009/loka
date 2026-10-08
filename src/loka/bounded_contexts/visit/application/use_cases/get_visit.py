"""Read a single visit (a party only)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.visit.application.dto.visit_dto import VisitView
from loka.bounded_contexts.visit.application.use_cases.authorization import require_party
from loka.bounded_contexts.visit.domain.repositories.visit_repository import (
    VISIT_REPOSITORY,
    VisitRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


@dataclass(frozen=True, slots=True)
class GetVisitQuery:
    visit_id: uuid.UUID
    requester_id: uuid.UUID


class GetVisitUseCase(UseCase[GetVisitQuery, VisitView]):
    name = "get_visit"

    def __init__(self, uow: UnitOfWork, *, landlords: LandlordDirectory) -> None:
        super().__init__(uow)
        self._landlords = landlords

    async def execute(self, query: GetVisitQuery) -> VisitView:
        repository: VisitRepository = self._uow.repository(VISIT_REPOSITORY)
        visit = await repository.get(query.visit_id)
        if visit is None:
            raise ResourceNotFound(
                "visit not found", context={"visit_id": str(query.visit_id)}
            )
        await require_party(self._landlords, visit, query.requester_id)
        return VisitView.from_aggregate(visit)