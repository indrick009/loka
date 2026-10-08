"""Express interest in a property.

A tenant signals intent by creating a rental application. The command carries
only the property id: the owning landlord is resolved through the property
directory so the caller can never choose whose listing they are applying to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.rental.application.dto.rental_dto import RentalApplicationView
from loka.bounded_contexts.rental.application.ports import PropertyDirectory
from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
)
from loka.bounded_contexts.rental.domain.repositories.rental_repository import (
    RENTAL_APPLICATION_REPOSITORY,
    RentalApplicationRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase
from loka.shared.domain.errors import InvariantViolation, ResourceNotFound
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class ExpressRentalInterestCommand:
    property_id: uuid.UUID
    tenant_id: uuid.UUID
    message: str | None = None


class ExpressRentalInterestUseCase(UseCase[ExpressRentalInterestCommand, RentalApplicationView]):
    name = "express_rental_interest"

    def __init__(self, uow: UnitOfWork, *, properties: PropertyDirectory) -> None:
        super().__init__(uow)
        self._properties = properties

    async def execute(  # type: ignore[override]
        self, command: ExpressRentalInterestCommand, *, now: datetime
    ) -> RentalApplicationView:
        snapshot = await self._properties.snapshot(command.property_id)
        if snapshot is None:
            raise ResourceNotFound(
                "property not found", context={"property_id": str(command.property_id)}
            )
        if not snapshot.is_available:
            raise InvariantViolation(
                "property is not available for a rental application",
                context={"property_id": str(command.property_id)},
            )

        repository: RentalApplicationRepository = self._uow.repository(
            RENTAL_APPLICATION_REPOSITORY
        )
        application = RentalApplication(
            application_id=new_id(),
            property_id=snapshot.property_id,
            landlord_id=snapshot.landlord_id,
            tenant_id=command.tenant_id,
            message=command.message,
            now=now,
        )
        await repository.add(application)
        self._uow.collect(application)
        await self._uow.commit()
        return RentalApplicationView.from_aggregate(application)