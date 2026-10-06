"""SQLAlchemy adapter for :class:`LandlordDirectory`.

Backed by ``landlord_profiles.verification_status``; a suspended landlord is
treated as unverified so a sanction immediately blocks new publications.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.landlord.domain.entities.verification_request import VerificationStatus
from loka.bounded_contexts.landlord.infrastructure.persistence.models import LandlordProfileRow


class SqlAlchemyLandlordDirectory:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def profile_id_for_user(self, user_id: uuid.UUID) -> uuid.UUID | None:
        result = await self._session.execute(
            select(LandlordProfileRow.id).where(LandlordProfileRow.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def is_verified(self, landlord_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(LandlordProfileRow.verification_status).where(
                LandlordProfileRow.id == landlord_id
            )
        )
        status = result.scalar_one_or_none()
        return status == VerificationStatus.VERIFIED.value
