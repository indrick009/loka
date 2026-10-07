"""Infrastructure wiring for the Property context.

The application layer resolves repositories through the unit of work, so the
mapping from a repository name to its adapter lives here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.property.infrastructure.persistence.property_quota import (
    SqlAlchemyPropertyQuota,
)
from loka.bounded_contexts.property.infrastructure.persistence.property_repository import (
    SqlAlchemyPropertyRepository,
)
from loka.bounded_contexts.property.infrastructure.persistence.property_search_repository import (
    PropertySearchRepository,
)
from loka.bounded_contexts.property.infrastructure.persistence.search_projector import (
    SqlAlchemySearchProjector,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

PROPERTY_REPOSITORY = "property"
PROPERTY_QUOTA_REPOSITORY = "property_quota"
PROPERTY_SEARCH_PROJECTOR = "property_search_projector"
PROPERTY_SEARCH_REPOSITORY = "property_search"


def _property(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPropertyRepository(uow.session, unit_of_work=uow)


def _property_quota(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPropertyQuota(uow.session)


def _property_search_projector(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemySearchProjector(uow.session)


def _property_search(uow: SqlAlchemyUnitOfWork) -> Any:
    return PropertySearchRepository(uow.session)


PROPERTY_REPOSITORY_FACTORIES: dict[str, Any] = {
    PROPERTY_REPOSITORY: _property,
    PROPERTY_QUOTA_REPOSITORY: _property_quota,
    PROPERTY_SEARCH_PROJECTOR: _property_search_projector,
    PROPERTY_SEARCH_REPOSITORY: _property_search,
}
