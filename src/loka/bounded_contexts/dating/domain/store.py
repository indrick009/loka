"""The port Dating persists through."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.dating.domain.profile import Profile


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
