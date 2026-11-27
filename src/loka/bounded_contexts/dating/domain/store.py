"""The port Dating persists through."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.dating.domain.attributes import Wish
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.talk import Talk


class DatingStore(Protocol):
    async def profile_of(self, user_id: uuid.UUID) -> Profile | None: ...

    async def profile(self, profile_id: uuid.UUID) -> Profile | None: ...

    async def add(self, profile: Profile) -> None: ...

    async def refused_minor(self, user_id: uuid.UUID) -> bool: ...

    async def refuse_minor(self, user_id: uuid.UUID, *, now: datetime) -> None: ...

    async def named(self, user_id: uuid.UUID) -> Profile | None:
        """The profile, active or in a relation."""

    async def in_relation(self, user_id: uuid.UUID) -> bool:
        """Contacts exchanged with a match: proposed to nobody, shown nobody."""

    async def start_relation(self, *user_ids: uuid.UUID) -> None: ...

    async def back_on_market(self, user_id: uuid.UUID) -> None:
        """It did not work out: proposed again, the former match excepted."""

    async def said_yes_to(self, profile_id: uuid.UUID) -> list[Profile]:
        """Active profiles this member said yes to, latest first."""

    async def withdraw(self, user_id: uuid.UUID) -> None:
        """The member asked it: the profile is erased, a new one can be made."""

    async def update_photo(self, user_id: uuid.UUID, photo_key: str) -> None: ...

    async def passed_on(self, profile_id: uuid.UUID) -> list[Profile]:
        """Active profiles this member said no to, latest first."""

    async def update_attributes(
        self, user_id: uuid.UUID, traits: dict[str, str], wishes: tuple[Wish, ...]
    ) -> None:
        """What the member is and wants, as read from the profile."""

    async def attribute_keys(self) -> set[str]:
        """The keys already used by any profile."""

    async def update_ages(self, user_id: uuid.UUID, low: int | None, high: int | None) -> None:
        """The age range sought; ``None`` ends are no bound."""

    async def update_looking(self, user_id: uuid.UUID, looking: tuple[str, ...]) -> None: ...

    async def refusals(self) -> dict[uuid.UUID, tuple[int, int]]:
        """Per profile: the « non » it got, and all the answers it got."""

    async def members(self) -> list[Profile]: ...

    async def answered(self, profile_id: uuid.UUID) -> set[str]:
        """Profiles this member already said yes or no to."""

    async def passed_by(self, profile_id: uuid.UUID) -> set[str]:
        """Profiles that already said no to this member."""

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

    async def open_talks(self) -> list[Talk]: ...

    async def unanswered_yeses(
        self, before: datetime, since: datetime
    ) -> list[tuple[Profile, Profile]]:
        """``(waiting, silent)``: yeses given between ``since`` and ``before`` the
        other never answered."""

    async def recent_answers(
        self, profile_id: uuid.UUID, limit: int = 6
    ) -> list[tuple[Profile, bool, str | None]]:
        """The member's latest answers, newest first: who, yes or no, why."""

    async def note_reason(
        self, from_profile: uuid.UUID, to_profile: uuid.UUID, reason: str
    ) -> None:
        """Why the member said no to that profile, in their words."""

    async def update_description(self, user_id: uuid.UUID, description: str) -> None:
        """The member's own words, completed with what they added later."""

    async def matched_since(self, profile_id: uuid.UUID, since: datetime) -> bool:
        """A match opened since ``since``: the member is not shown anyone else."""

    async def update_face(self, user_id: uuid.UUID, face: tuple[float, ...]) -> None: ...

    async def all_answers(self) -> dict[uuid.UUID, list[tuple[uuid.UUID, bool]]]:
        """Per profile, every answer it gave: ``(to_profile, liked)``."""

    async def last_wrote(self) -> dict[uuid.UUID, datetime]:
        """Per user, when they last wrote to idia."""

    async def liked_by_both(self, profile_id: uuid.UUID) -> list[uuid.UUID]:
        """Profiles that liked this member and that he liked back, oldest first."""
