"""Composition root.

Single place where adapters are bound to ports and use cases are assembled.
Nothing else in the codebase builds a repository by hand, so swapping an adapter
for a test double stays a local change.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.landlord.infrastructure.directory import (
    SqlAlchemyLandlordDirectory,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    MESSAGING_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyUseCase,
)
from loka.bounded_contexts.property.infrastructure.composition import (
    PROPERTY_REPOSITORY_FACTORIES,
)
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

LANDLORD_DIRECTORY = "landlord_directory"

_CONTEXT_FACTORIES: tuple[dict[str, Any], ...] = (
    {LANDLORD_DIRECTORY: lambda uow: SqlAlchemyLandlordDirectory(uow.session)},
    PROPERTY_REPOSITORY_FACTORIES,
    MESSAGING_REPOSITORY_FACTORIES,
)


def repository_factories() -> dict[str, Any]:
    """Every repository reachable through ``UnitOfWork.repository(name)``."""
    merged: dict[str, Any] = {}
    for factories in _CONTEXT_FACTORIES:
        merged.update(factories)
    return merged


def unit_of_work(database: Database) -> SqlAlchemyUnitOfWork:
    """A unit of work wired to the real SQLAlchemy adapters."""
    return SqlAlchemyUnitOfWork(database.sessions, repository_factories=repository_factories())


def publish_property_use_case(
    uow: SqlAlchemyUnitOfWork, *, max_active_properties: int = 25
) -> PublishPropertyUseCase:
    """Resolve the publication ports from the caller's unit of work.

    Taking the unit of work as an argument keeps the whole use case inside one
    transaction: the aggregate, the quota query and the verification lookup all
    read through the same session.
    """
    return PublishPropertyUseCase(
        uow,
        landlords=uow.repository(LANDLORD_DIRECTORY),
        max_active_properties=max_active_properties,
    )
