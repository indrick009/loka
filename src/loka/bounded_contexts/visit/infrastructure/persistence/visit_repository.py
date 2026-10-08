"""SQLAlchemy implementation of the visit write model."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.visit.domain.entities.visit import Visit
from loka.bounded_contexts.visit.infrastructure.mappers.visit_mapper import (
    from_row,
    to_row,
)
from loka.bounded_contexts.visit.infrastructure.persistence.models import VisitRow
from loka.shared.application.unit_of_work import SupportsAfterCommit
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyVisitRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, visit_id: uuid.UUID) -> Visit | None:
        row = await self._session.get(VisitRow, visit_id)
        return from_row(row) if row is not None else None

    async def add(self, visit: Visit) -> None:
        self._session.add(VisitRow(**to_row(visit)))
        self._mark_persisted(visit)

    async def save(self, visit: Visit, *, expected_version: int | None = None) -> None:
        guard = expected_version if expected_version is not None else visit.persisted_version
        payload = to_row(visit)
        payload.pop("id")
        payload.pop("created_at")
        payload["revision"] = visit.version
        stmt = (
            update(VisitRow)
            .where(VisitRow.id == visit.id)
            .where(VisitRow.revision == guard)
            .values(**payload)
            .returning(VisitRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "visit was modified concurrently",
                context={"visit_id": str(visit.id), "expected_version": guard},
            )
        self._mark_persisted(visit)

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[Visit]:
        result = await self._session.execute(
            select(VisitRow)
            .where(VisitRow.tenant_id == tenant_id)
            .order_by(VisitRow.created_at.desc())
        )
        return [from_row(row) for row in result.scalars()]

    async def list_for_landlord(self, landlord_id: uuid.UUID) -> list[Visit]:
        result = await self._session.execute(
            select(VisitRow)
            .where(VisitRow.landlord_id == landlord_id)
            .order_by(VisitRow.created_at.desc())
        )
        return [from_row(row) for row in result.scalars()]

    def _mark_persisted(self, visit: Visit) -> None:
        version = visit.version
        if self._unit_of_work is None:
            visit.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: visit.mark_persisted(version))