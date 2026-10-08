"""Propose or negotiate rental terms.

Either party may counter-offer; every proposal is recorded on the aggregate so
the negotiation history is reconstructable from the event stream.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.property.domain.value_objects.money import Money
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
class ProposeRentalTermsCommand:
    application_id: uuid.UUID
    actor_id: uuid.UUID
    rent: Money
    deposit: Money | None = None
    message: str | None = None


class ProposeRentalTermsUseCase(UseCase[ProposeRentalTermsCommand, RentalApplicationView]):
    name = "propose_rental_terms"

    def __init__(self, uow: UnitOfWork, *, landlords: LandlordDirectory) -> None:
        super().__init__(uow)
        self._landlords = landlords

    async def execute(  # type: ignore[override]
        self, command: ProposeRentalTermsCommand, *, now: datetime
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
        await require_party(self._landlords, application, command.actor_id)
        application.propose_terms(
            rent=command.rent,
            deposit=command.deposit,
            message=command.message,
            actor_id=command.actor_id,
            now=now,
        )
        await repository.save(application)
        self._uow.collect(application)
        await self._uow.commit()
        return RentalApplicationView.from_aggregate(application)