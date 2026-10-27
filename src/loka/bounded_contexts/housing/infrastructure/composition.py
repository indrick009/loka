"""Infrastructure wiring for the Housing context.

The application layer resolves repositories through the unit of work, so the
mapping from a repository name to its adapter lives here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.housing.domain.repositories.landlord_repository import (
    LANDLORD_PROFILE_REPOSITORY,
)
from loka.bounded_contexts.housing.infrastructure.persistence.availability_check_repository import (
    SqlAlchemyAvailabilityCheckRepository,
)
from loka.bounded_contexts.housing.infrastructure.persistence.landlord_repository import (
    SqlAlchemyLandlordDirectory,
    SqlAlchemyLandlordProfileRepository,
)
from loka.bounded_contexts.housing.infrastructure.persistence.property_quota import (
    SqlAlchemyPropertyQuota,
)
from loka.bounded_contexts.housing.infrastructure.persistence.property_repository import (
    SqlAlchemyPropertyRepository,
)
from loka.bounded_contexts.housing.infrastructure.persistence.property_search_repository import (
    PropertySearchRepository,
)
from loka.bounded_contexts.housing.infrastructure.persistence.search_projector import (
    SqlAlchemySearchProjector,
)
from loka.bounded_contexts.housing.infrastructure.persistence.tenant_demand_repository import (
    SqlAlchemyTenantDemandRepository,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

PROPERTY_REPOSITORY = "property"
PROPERTY_QUOTA_REPOSITORY = "property_quota"
PROPERTY_SEARCH_PROJECTOR = "property_search_projector"
PROPERTY_SEARCH_REPOSITORY = "property_search"
AVAILABILITY_CHECK_REPOSITORY = "availability_check"
TENANT_DEMAND_REPOSITORY = "tenant_demand"
LANDLORD_DIRECTORY = "landlord_directory"


def _property(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPropertyRepository(uow.session, unit_of_work=uow)


def _property_quota(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPropertyQuota(uow.session)


def _property_search_projector(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemySearchProjector(uow.session)


def _property_search(uow: SqlAlchemyUnitOfWork) -> Any:
    return PropertySearchRepository(uow.session)


def _availability_check(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyAvailabilityCheckRepository(uow.session)


def _tenant_demand(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyTenantDemandRepository(uow.session)


def _landlord_directory(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyLandlordDirectory(uow.session)


def _landlord_profile(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyLandlordProfileRepository(uow.session, unit_of_work=uow)


PROPERTY_REPOSITORY_FACTORIES: dict[str, Any] = {
    PROPERTY_REPOSITORY: _property,
    PROPERTY_QUOTA_REPOSITORY: _property_quota,
    PROPERTY_SEARCH_PROJECTOR: _property_search_projector,
    PROPERTY_SEARCH_REPOSITORY: _property_search,
    AVAILABILITY_CHECK_REPOSITORY: _availability_check,
    TENANT_DEMAND_REPOSITORY: _tenant_demand,
    LANDLORD_DIRECTORY: _landlord_directory,
    LANDLORD_PROFILE_REPOSITORY: _landlord_profile,
}
