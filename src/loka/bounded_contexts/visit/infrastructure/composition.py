"""Infrastructure wiring for the Visit context."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.visit.application.ports import VisitPropertyDirectory
from loka.bounded_contexts.visit.domain.repositories.visit_repository import VISIT_REPOSITORY
from loka.bounded_contexts.visit.infrastructure.persistence.visit_repository import (
    SqlAlchemyVisitRepository,
)
from loka.bounded_contexts.visit.infrastructure.property_directory import (
    SqlAlchemyVisitPropertyDirectory,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

VISIT_PROPERTY_DIRECTORY = "visit_property_directory"


def _visit(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyVisitRepository(uow.session, unit_of_work=uow)


def _property_directory(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyVisitPropertyDirectory(uow)


VISIT_REPOSITORY_FACTORIES: dict[str, Any] = {
    VISIT_REPOSITORY: _visit,
    VISIT_PROPERTY_DIRECTORY: _property_directory,
}


def property_directory(uow: Any) -> VisitPropertyDirectory:
    resolved: VisitPropertyDirectory = uow.repository(VISIT_PROPERTY_DIRECTORY)
    return resolved