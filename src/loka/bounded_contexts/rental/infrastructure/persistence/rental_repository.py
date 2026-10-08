"""SQLAlchemy implementation of the rental application write model."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.rental.domain.entities.rental_application import (
    RentalApplication,
)
from loka.bounded_contexts.rental.infrastructure.mappers.rental_mapper import (
    from_row,
    to_row,
)
from loka.bounded_contexts.rental.infrastructure.persistence.models import (
    RentalApplicationRow,
)
from loka.shared.application.unit_of_work import SupportsAfterCommit
from loka.shared.domain.errors import ConcurrencyConflict

_OPEN_STATUSES = ("INTERESTED", "NEGOTIATING", "ACCEPTED")


class SqlAlchemyRentalApplicationRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, application_id: uuid.UUID) -> RentalApplication | None:
        row = await self._session.get(RentalApplicationRow, application_id)
        return from_row(row) if row is not None else None

    async def add(self, application: RentalApplication) -> None:
        self._session.add(RentalApplicationRow(**to_row(application)))
        self._mark_persisted(application)

    async def save(
        self, application: RentalApplication, *, expected_version: int | None = None
    ) -> None:
        guard = (
            expected_version
            if expected_version is not None
            else application.persisted_version
        )
        payload = to_row(application)
        payload.pop("id")
        payload.pop("created_at")
        payload["revision"] = application.version
        stmt = (
            update(RentalApplicationRow)
            .where(RentalApplicationRow.id == application.id)
            .where(RentalApplicationRow.revision == guard)
            .values(**payload)
            .returning(RentalApplicationRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "rental application was modified concurrently",
                context={
                    "application_id": str(application.id),
                    "expected_version": guard,
                },
            )
        self._mark_persisted(application)

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[RentalApplication]:
        result = await self._session.execute(
            select(RentalApplicationRow)
            .where(RentalApplicationRow.tenant_id == tenant_id)
            .order_by(RentalApplicationRow.created_at.desc())
        )
        return [from_row(row) for row in result.scalars()]

    async def list_for_landlord(self, landlord_id: uuid.UUID) -> list[RentalApplication]:
        result = await self._session.execute(
            select(RentalApplicationRow)
            .where(RentalApplicationRow.landlord_id == landlord_id)
            .order_by(RentalApplicationRow.created_at.desc())
        )
        return [from_row(row) for row in result.scalars()]

    async def open_for_property(self, property_id: uuid.UUID) -> list[RentalApplication]:
        result = await self._session.execute(
            select(RentalApplicationRow)
            .where(RentalApplicationRow.property_id == property_id)
            .where(RentalApplicationRow.status.in_(_OPEN_STATUSES))
        )
        return [from_row(row) for row in result.scalars()]

    def _mark_persisted(self, application: RentalApplication) -> None:
        version = application.version
        if self._unit_of_work is None:
            application.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: application.mark_persisted(version))