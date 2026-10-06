"""SQLAlchemy implementation of the property write model."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.infrastructure.mappers.property_mapper import (
    from_row,
    media_to_row,
    to_row,
)
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertyMediaRow,
    PropertyRow,
)
from loka.shared.application.unit_of_work import SupportsAfterCommit
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyPropertyRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, property_id: uuid.UUID) -> Property | None:
        row = await self._session.get(PropertyRow, property_id)
        if row is None:
            return None
        media_result = await self._session.execute(
            select(PropertyMediaRow)
            .where(PropertyMediaRow.property_id == property_id)
            .order_by(PropertyMediaRow.position)
        )
        media = list(media_result.scalars())
        return from_row(row, media)

    async def get_many(self, property_ids: list[uuid.UUID]) -> list[Property]:
        if not property_ids:
            return []
        result = await self._session.execute(
            select(PropertyRow).where(PropertyRow.id.in_(property_ids))
        )
        media_result = await self._session.execute(
            select(PropertyMediaRow).where(PropertyMediaRow.property_id.in_(property_ids))
        )
        by_property: dict[uuid.UUID, list[PropertyMediaRow]] = {}
        for item in media_result.scalars():
            by_property.setdefault(item.property_id, []).append(item)
        return [from_row(row, by_property.get(row.id, [])) for row in result.scalars()]

    async def add(self, prop: Property) -> None:
        payload = to_row(prop)
        self._session.add(PropertyRow(**payload))
        self._mark_persisted(prop)
        await self._replace_media(prop)

    async def save(self, prop: Property, *, expected_version: int | None = None) -> None:
        """Optimistic locking: the write only lands if nobody moved the row."""
        guard = expected_version if expected_version is not None else prop.persisted_version
        payload = to_row(prop)
        payload.pop("id")
        payload.pop("created_at")
        payload["version"] = prop.version
        stmt = (
            update(PropertyRow)
            .where(PropertyRow.id == prop.id)
            .where(PropertyRow.version == guard)
            .values(**payload)
            .returning(PropertyRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "property was modified concurrently",
                context={"property_id": str(prop.id), "expected_version": guard},
            )
        self._mark_persisted(prop)
        await self._replace_media(prop)

    def _mark_persisted(self, prop: Property) -> None:
        """Record the new baseline version once the write is durable.

        Marking it eagerly would make the aggregate claim a version the database
        never committed, and a rollback would leave it permanently un-saveable.
        """
        version = prop.version
        if self._unit_of_work is None:
            prop.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: prop.mark_persisted(version))

    async def _replace_media(self, prop: Property) -> None:
        """Rewrite the media set so the rows match the aggregate exactly.

        Media identity lives in the aggregate, so delete-then-insert keeps ids
        stable while still persisting status, checksum and position changes. An
        empty media list is a removal, not a no-op.
        """
        await self._session.execute(
            delete(PropertyMediaRow).where(PropertyMediaRow.property_id == prop.id)
        )
        for media in prop.media:
            self._session.add(
                PropertyMediaRow(**media_to_row(media, prop.id, prop.landlord_id))
            )
        await self._session.flush()

    async def active_count_for_landlord(self, landlord_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PropertyRow)
            .where(
                PropertyRow.landlord_id == landlord_id,
                PropertyRow.status.in_(["AVAILABLE", "RESERVED", "DRAFT"]),
            )
        )
        return int(result.scalar_one())

    async def median_rent(
        self, *, city: str, neighbourhood: str | None, property_type: str | None
    ) -> int | None:
        """Median of comparable live listings, used by the price anomaly check."""
        statement = select(PropertyRow.public_price_xaf).where(
            PropertyRow.status == "AVAILABLE",
            PropertyRow.city == city,
            PropertyRow.public_price_xaf.isnot(None),
        )
        if neighbourhood:
            statement = statement.where(PropertyRow.neighbourhood == neighbourhood)
        if property_type:
            statement = statement.where(PropertyRow.property_type == property_type)
        statement = statement.order_by(PropertyRow.public_price_xaf).limit(1000)
        result = await self._session.execute(statement)
        prices = sorted(row for row in result.scalars() if row is not None)
        if not prices:
            return None
        middle = len(prices) // 2
        if len(prices) % 2:
            return int(prices[middle])
        return int((prices[middle - 1] + prices[middle]) / 2)

    async def stale_availability(
        self, *, confirmed_before: datetime, limit: int = 100
    ) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(PropertyRow.id)
            .where(
                PropertyRow.status == "AVAILABLE",
                PropertyRow.last_availability_confirmed_at < confirmed_before,
            )
            .order_by(PropertyRow.last_availability_confirmed_at)
            .limit(limit)
        )
        return list(result.scalars())