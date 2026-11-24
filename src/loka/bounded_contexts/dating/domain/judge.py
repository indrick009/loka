"""The model's reading of a few candidates, both ways: the port Dating asks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from loka.bounded_contexts.dating.domain.profile import Profile

# How many of the best-ranked candidates the model reads.
SHORTLIST = 8


@dataclass(frozen=True, slots=True)
class Verdict:
    profile_id: uuid.UUID
    score: float  # 0 to 1
    reason: str


class MatchJudge(Protocol):
    async def rank(self, member: Profile, candidates: list[Profile]) -> list[Verdict] | None:
        """Best first; ``None`` when the model could not answer."""
