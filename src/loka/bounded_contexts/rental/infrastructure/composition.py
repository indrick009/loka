"""Infrastructure wiring for the Rental context."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.rental.application.ports import PropertyDirectory
from loka.bounded_contexts.rental.domain.repositories.rental_repository import (
    RENTAL_APPLICATION_REPOSITORY,
)
from loka.bounded_contexts.rental.infrastructure.persistence.rental_repository import (
    SqlAlchemyRentalApplicationRepository,
)
from loka.bounded_contexts.rental.infrastructure.property_directory import (
    SqlAlchemyPropertyDirectory,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

RENTAL_PROPERTY_DIRECTORY = "rental_property_directory"


def _rental_application(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyRentalApplicationRepository(uow.session, unit_of_work=uow)


def _property_directory(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPropertyDirectory(uow)


RENTAL_REPOSITORY_FACTORIES: dict[str, Any] = {
    RENTAL_APPLICATION_REPOSITORY: _rental_application,
    RENTAL_PROPERTY_DIRECTORY: _property_directory,
}


def property_directory(uow: Any) -> PropertyDirectory:
    resolved: PropertyDirectory = uow.repository(RENTAL_PROPERTY_DIRECTORY)
    return resolved