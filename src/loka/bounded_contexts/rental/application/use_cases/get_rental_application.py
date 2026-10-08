"""Read a single rental application (a party only)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.rental.application.dto.rental_dto import RentalApplicationView
from loka.bounded_contexts.rental.application.use_cases.authorization import require_party
from loka.bounded_contexts.rental.domain.repositories.rental_repository import (
    RENTAL_APPLICATION_REPOSITORY,
    RentalApplicationRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


@dataclass(frozen=True, slots=True)
class GetRentalApplicationQuery:
    application_id: uuid.UUID
    requester_id: uuid.UUID


class GetRentalApplicationUseCase(UseCase[GetRentalApplicationQuery, RentalApplicationView]):
    name = "get_rental_application"

    def __init__(self, uow: UnitOfWork, *, landlords: LandlordDirectory) -> None:
        super().__init__(uow)
        self._landlords = landlords

    async def execute(self, query: GetRentalApplicationQuery) -> RentalApplicationView:
        repository: RentalApplicationRepository = self._uow.repository(
            RENTAL_APPLICATION_REPOSITORY
        )
        application = await repository.get(query.application_id)
        if application is None:
            raise ResourceNotFound(
                "rental application not found",
                context={"application_id": str(query.application_id)},
            )
        await require_party(self._landlords, application, query.requester_id)
        return RentalApplicationView.from_aggregate(application)