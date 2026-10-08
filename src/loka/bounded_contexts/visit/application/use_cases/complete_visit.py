"""Record the outcome of a scheduled visit.

The landlord confirms what happened: the visit took place (``attended=True``)
or the tenant did not show up. Both outcomes feed the trust loop.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.visit.application.dto.visit_dto import VisitView
from loka.bounded_contexts.visit.application.use_cases.authorization import require_landlord
from loka.bounded_contexts.visit.domain.repositories.visit_repository import (
    VISIT_REPOSITORY,
    VisitRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


@dataclass(frozen=True, slots=True)
class CompleteVisitCommand:
    visit_id: uuid.UUID
    actor_id: uuid.UUID
    attended: bool = True


class CompleteVisitUseCase(UseCase[CompleteVisitCommand, VisitView]):
    name = "complete_visit"

    def __init__(self, uow: UnitOfWork, *, landlords: LandlordDirectory) -> None:
        super().__init__(uow)
        self._landlords = landlords

    async def execute(  # type: ignore[override]
        self, command: CompleteVisitCommand, *, now: datetime
    ) -> VisitView:
        repository: VisitRepository = self._uow.repository(VISIT_REPOSITORY)
        visit = await repository.get(command.visit_id)
        if visit is None:
            raise ResourceNotFound(
                "visit not found", context={"visit_id": str(command.visit_id)}
            )
        await require_landlord(self._landlords, visit, command.actor_id)
        if command.attended:
            visit.complete(now=now, actor_id=command.actor_id)
        else:
            visit.mark_no_show(now=now, actor_id=command.actor_id)
        await repository.save(visit)
        self._uow.collect(visit)
        await self._uow.commit()
        return VisitView.from_aggregate(visit)