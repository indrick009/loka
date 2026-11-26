"""The port Dating reads a profile through: what the member is and wants."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from loka.bounded_contexts.dating.domain.attributes import Wish
from loka.bounded_contexts.dating.domain.profile import Profile


@dataclass(frozen=True, slots=True)
class ProfileReading:
    traits: dict[str, str] = field(default_factory=dict)
    wishes: tuple[Wish, ...] = ()


class ProfileReader(Protocol):
    async def read(self, profile: Profile, keys: dict[str, str]) -> ProfileReading | None:
        """``keys``: the vocabulary, key → what it means; the reader may add keys.
        ``None`` when the model cannot answer: the profile is ranked without."""
