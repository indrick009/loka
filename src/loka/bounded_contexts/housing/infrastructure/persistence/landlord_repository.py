"""SQLAlchemy adapters for the landlord directory.

Only the columns the profile still carries are written; the ones left from
identity verification keep their database defaults.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.housing.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.housing.infrastructure.persistence.models import LandlordProfileRow
from loka.shared.application.unit_of_work import SupportsAfterCommit


class SqlAlchemyLandlordProfileRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def add(self, profile: LandlordProfile) -> None:
        self._session.add(
            LandlordProfileRow(
                id=profile.profile_id,
                user_id=profile.user_id,
                display_name=profile.display_name,
                revision=profile.version,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
        )
        version = profile.version
        if self._unit_of_work is None:
            profile.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: profile.mark_persisted(version))


class SqlAlchemyLandlordDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(LandlordProfileRow.id).where(LandlordProfileRow.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def user_id_for_profile(self, landlord_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(LandlordProfileRow.user_id).where(LandlordProfileRow.id == landlord_id)
        )
        return result.scalar_one_or_none()
