"""SQLAlchemy implementation of ``DatingStore``."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.infrastructure.models import DatingAnswerRow, DatingProfileRow

ACTIVE = "ACTIVE"
REFUSED_MINOR = "REFUSED_MINOR"


def _profile(row: DatingProfileRow) -> Profile:
    return Profile(
        profile_id=row.id,
        user_id=row.user_id,
        first_name=row.first_name,
        age=row.age,
        sex=row.sex,
        profession=row.profession,
        country=row.country,
        city=row.city,
        photo_key=row.photo_key,
        description=row.description,
        looking=tuple(row.looking or ()),
        seeking=row.seeking,
        created_at=row.created_at,
    )


class SqlAlchemyDatingStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _row_of(self, user_id: uuid.UUID) -> DatingProfileRow | None:
        return (
            await self._session.execute(
                select(DatingProfileRow).where(DatingProfileRow.user_id == user_id)
            )
        ).scalar_one_or_none()

    async def profile_of(self, user_id: uuid.UUID) -> Profile | None:
        row = await self._row_of(user_id)
        return _profile(row) if row is not None and row.status == ACTIVE else None

    async def profile(self, profile_id: uuid.UUID) -> Profile | None:
        row = await self._session.get(DatingProfileRow, profile_id)
        return _profile(row) if row is not None and row.status == ACTIVE else None

    async def add(self, profile: Profile) -> None:
        self._session.add(
            DatingProfileRow(
                id=profile.profile_id,
                user_id=profile.user_id,
                status=ACTIVE,
                first_name=profile.first_name,
                age=profile.age,
                sex=profile.sex,
                profession=profile.profession,
                country=profile.country,
                city=profile.city,
                photo_key=profile.photo_key,
                description=profile.description,
                looking=list(profile.looking),
                seeking=profile.seeking,
                created_at=profile.created_at,
            )
        )
        await self._session.flush()

    async def refused_minor(self, user_id: uuid.UUID) -> bool:
        row = await self._row_of(user_id)
        return row is not None and row.status == REFUSED_MINOR

    async def refuse_minor(self, user_id: uuid.UUID, *, now: datetime) -> None:
        """Remember the refusal, and nothing else about the person."""
        if await self._row_of(user_id) is None:
            self._session.add(
                DatingProfileRow(
                    id=uuid.uuid4(), user_id=user_id, status=REFUSED_MINOR, created_at=now
                )
            )
            await self._session.flush()

    async def members(self) -> list[Profile]:
        rows = (
            await self._session.execute(
                select(DatingProfileRow).where(DatingProfileRow.status == ACTIVE)
            )
        ).scalars()
        return [_profile(row) for row in rows]

    async def answered(self, profile_id: uuid.UUID) -> set[str]:
        rows = await self._session.execute(
            select(DatingAnswerRow.to_profile).where(DatingAnswerRow.from_profile == profile_id)
        )
        return {str(value) for value in rows.scalars()}

    async def answer(
        self,
        *,
        from_profile: uuid.UUID,
        to_profile: uuid.UUID,
        liked: bool,
        score: float,
        now: datetime,
    ) -> None:
        existing = (
            await self._session.execute(
                select(DatingAnswerRow).where(
                    DatingAnswerRow.from_profile == from_profile,
                    DatingAnswerRow.to_profile == to_profile,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.liked = liked
        else:
            self._session.add(
                DatingAnswerRow(
                    id=uuid.uuid4(),
                    from_profile=from_profile,
                    to_profile=to_profile,
                    liked=liked,
                    score=score,
                    created_at=now,
                )
            )
        await self._session.flush()

    async def likes(self, from_profile: uuid.UUID, to_profile: uuid.UUID) -> bool | None:
        value = (
            await self._session.execute(
                select(DatingAnswerRow.liked).where(
                    DatingAnswerRow.from_profile == from_profile,
                    DatingAnswerRow.to_profile == to_profile,
                )
            )
        ).scalar_one_or_none()
        return None if value is None else bool(value)
