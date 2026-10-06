"""Quota query used by publication to cap a landlord's active catalogue."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.infrastructure.persistence.models import PropertyRow

ACTIVE_STATUSES = ("DRAFT", "AVAILABLE", "RESERVED")


class SqlAlchemyPropertyQuota:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def active_count(
        self, landlord_id: uuid.UUID, *, exclude_property_id: uuid.UUID | None = None
    ) -> int:
        statement = select(func.count()).select_from(PropertyRow).where(
            PropertyRow.landlord_id == landlord_id, PropertyRow.status.in_(ACTIVE_STATUSES)
        )
        if exclude_property_id is not None:
            statement = statement.where(PropertyRow.id != exclude_property_id)
        result = await self._session.execute(statement)
        return int(result.scalar_one())
