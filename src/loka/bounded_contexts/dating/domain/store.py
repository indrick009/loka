"""The port Dating persists through."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.talk import Talk


class DatingStore(Protocol):
    async def profile_of(self, user_id: uuid.UUID) -> Profile | None: ...

    async def profile(self, profile_id: uuid.UUID) -> Profile | None: ...

    async def add(self, profile: Profile) -> None: ...

    async def refused_minor(self, user_id: uuid.UUID) -> bool: ...

    async def refuse_minor(self, user_id: uuid.UUID, *, now: datetime) -> None: ...

    async def members(self) -> list[Profile]: ...

    async def answered(self, profile_id: uuid.UUID) -> set[str]:
        """Profiles this member already said yes or no to."""

    async def answer(
        self,
        *,
        from_profile: uuid.UUID,
        to_profile: uuid.UUID,
        liked: bool,
        score: float,
        now: datetime,
    ) -> None: ...

    async def likes(self, from_profile: uuid.UUID, to_profile: uuid.UUID) -> bool | None:
        """``True``/``False`` when answered, ``None`` when never proposed."""

    async def talk(self, talk_id: uuid.UUID) -> Talk | None: ...

    async def open_talk_of(self, profile_id: uuid.UUID) -> Talk | None:
        """The exchange this member is in, if one is open."""

    async def ever_talked(self, a: uuid.UUID, b: uuid.UUID) -> bool: ...

    async def save_talk(self, talk: Talk) -> None: ...

    async def liked_by_both(self, profile_id: uuid.UUID) -> list[uuid.UUID]:
        """Profiles that liked this member and that he liked back, oldest first."""
