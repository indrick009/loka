"""SQLAlchemy implementation of ``DatingStore``."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.talk import OPEN, Side, Talk
from loka.bounded_contexts.dating.infrastructure.models import (
    DatingAnswerRow,
    DatingProfileRow,
    DatingTalkRow,
)

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


def _side(profile_id: uuid.UUID, raw: dict[str, int]) -> Side:
    return Side(
        profile_id=profile_id,
        sent=int(raw.get("sent", 0)),
        photos=int(raw.get("photos", 0)),
        packs=int(raw.get("packs", 1)),
        confirmed=bool(raw.get("confirmed", 0)),
    )


def _raw(side: Side) -> dict[str, int]:
    return {
        "sent": side.sent,
        "photos": side.photos,
        "packs": side.packs,
        "confirmed": int(side.confirmed),
    }


def _talk(row: DatingTalkRow) -> Talk:
    return Talk(
        talk_id=row.id,
        first=_side(row.first_profile, row.first_side or {}),
        second=_side(row.second_profile, row.second_side or {}),
        status=row.status,
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

    async def talk(self, talk_id: uuid.UUID) -> Talk | None:
        row = await self._session.get(DatingTalkRow, talk_id)
        return _talk(row) if row is not None else None

    async def open_talk_of(self, profile_id: uuid.UUID) -> Talk | None:
        row = (
            await self._session.execute(
                select(DatingTalkRow)
                .where(
                    DatingTalkRow.status == OPEN,
                    or_(
                        DatingTalkRow.first_profile == profile_id,
                        DatingTalkRow.second_profile == profile_id,
                    ),
                )
                .order_by(DatingTalkRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _talk(row) if row is not None else None

    async def ever_talked(self, a: uuid.UUID, b: uuid.UUID) -> bool:
        found = await self._session.scalar(
            select(DatingTalkRow.id).where(
                or_(
                    and_(DatingTalkRow.first_profile == a, DatingTalkRow.second_profile == b),
                    and_(DatingTalkRow.first_profile == b, DatingTalkRow.second_profile == a),
                )
            )
        )
        return found is not None

    async def save_talk(self, talk: Talk) -> None:
        row = await self._session.get(DatingTalkRow, talk.talk_id)
        if row is None:
            row = DatingTalkRow(
                id=talk.talk_id,
                first_profile=talk.first.profile_id,
                second_profile=talk.second.profile_id,
                created_at=talk.created_at,
            )
            self._session.add(row)
        row.status = talk.status
        row.first_side = _raw(talk.first)
        row.second_side = _raw(talk.second)
        await self._session.flush()

    async def liked_by_both(self, profile_id: uuid.UUID) -> list[uuid.UUID]:
        mine = aliased(DatingAnswerRow)
        rows = await self._session.execute(
            select(DatingAnswerRow.from_profile)
            .where(
                DatingAnswerRow.to_profile == profile_id,
                DatingAnswerRow.liked.is_(True),
                DatingAnswerRow.from_profile.in_(
                    select(mine.to_profile).where(
                        mine.from_profile == profile_id, mine.liked.is_(True)
                    )
                ),
            )
            .order_by(DatingAnswerRow.created_at)
        )
        return list(rows.scalars())
