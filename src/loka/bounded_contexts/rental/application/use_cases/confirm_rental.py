"""Confirm an accepted rental.

Confirmation closes the trust loop: the property moves to RENTED and the
application reaches its final state. Only the landlord can confirm, since it
is the landlord who hands over the keys.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.rental.application.dto.rental_dto import RentalApplicationView
from loka.bounded_contexts.rental.application.ports import PropertyDirectory
from loka.bounded_contexts.rental.application.use_cases.authorization import require_landlord
from loka.bounded_contexts.rental.domain.repositories.rental_repository import (
    RENTAL_APPLICATION_REPOSITORY,
    RentalApplicationRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


@dataclass(frozen=True, slots=True)
class ConfirmRentalCommand:
    application_id: uuid.UUID
    actor_id: uuid.UUID


class ConfirmRentalUseCase(UseCase[ConfirmRentalCommand, RentalApplicationView]):
    name = "confirm_rental"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        landlords: LandlordDirectory,
        properties: PropertyDirectory,
    ) -> None:
        super().__init__(uow)
        self._landlords = landlords
        self._properties = properties

    async def execute(  # type: ignore[override]
        self, command: ConfirmRentalCommand, *, now: datetime
    ) -> RentalApplicationView:
        repository: RentalApplicationRepository = self._uow.repository(
            RENTAL_APPLICATION_REPOSITORY
        )
        application = await repository.get(command.application_id)
        if application is None:
            raise ResourceNotFound(
                "rental application not found",
                context={"application_id": str(command.application_id)},
            )
        await require_landlord(self._landlords, application, command.actor_id)
        application.confirm(now=now, actor_id=command.actor_id)
        await self._properties.mark_rented(
            application.property_id, now=now, actor_id=command.actor_id
        )
        await repository.save(application)
        self._uow.collect(application)
        await self._uow.commit()
        return RentalApplicationView.from_aggregate(application)