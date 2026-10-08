"""Withdraw a rental application.

Only the tenant can withdraw. If the landlord had already accepted and the
property was reserved, the reservation is released so the listing returns to
the market.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.rental.application.dto.rental_dto import RentalApplicationView
from loka.bounded_contexts.rental.application.ports import PropertyDirectory
from loka.bounded_contexts.rental.application.use_cases.authorization import require_tenant
from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplicationStatus,
)
from loka.bounded_contexts.rental.domain.repositories.rental_repository import (
    RENTAL_APPLICATION_REPOSITORY,
    RentalApplicationRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import ResourceNotFound


@dataclass(frozen=True, slots=True)
class WithdrawRentalApplicationCommand:
    application_id: uuid.UUID
    actor_id: uuid.UUID


class WithdrawRentalApplicationUseCase(
    UseCase[WithdrawRentalApplicationCommand, RentalApplicationView]
):
    name = "withdraw_rental_application"

    def __init__(self, uow: UnitOfWork, *, properties: PropertyDirectory) -> None:
        super().__init__(uow)
        self._properties = properties

    async def execute(  # type: ignore[override]
        self, command: WithdrawRentalApplicationCommand, *, now: datetime
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
        require_tenant(application, command.actor_id)
        was_accepted = application.status is RentalApplicationStatus.ACCEPTED
        application.withdraw(now=now, actor_id=command.actor_id)
        if was_accepted:
            await self._properties.release_reservation(application.property_id, now=now)
        await repository.save(application)
        self._uow.collect(application)
        await self._uow.commit()
        return RentalApplicationView.from_aggregate(application)