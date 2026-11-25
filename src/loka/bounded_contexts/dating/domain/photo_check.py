"""Whether a dating photo is a real photo of the member: the port Dating asks.

The photo rule (no AI, no filter, no picture taken elsewhere) was only said;
an AI-generated face got through (journal of 24 Nov 2026, 19:27, « man+1.jpg »).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Why a photo is refused, as the model names it.
REASONS = ("AI", "STOCK", "CELEBRITY", "NO_FACE", "GROUP", "CHILD", "NOT_PHOTO")


@dataclass(frozen=True, slots=True)
class PhotoVerdict:
    ok: bool
    reason: str | None = None


class PhotoCheck(Protocol):
    async def check(self, object_key: str) -> PhotoVerdict:
        """A model that cannot answer lets the photo through: nobody is blocked by it."""
