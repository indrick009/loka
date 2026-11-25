"""Whether a dating photo is a real photo of the member: the port Dating asks.

The photo rule (no AI, no filter, no picture taken elsewhere) was only said;
an AI-generated face got through (journal of 24 Nov 2026, 19:27, « man+1.jpg »).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Why a photo is refused, as the model names it.
# OTHER_SEX: a man registered with a woman's picture « comme modèle » was
# proposed to women (journal of 25 Nov 2026, 17:52, Al-harazi).
REASONS = ("AI", "STOCK", "CELEBRITY", "NO_FACE", "GROUP", "CHILD", "NOT_PHOTO", "OTHER_SEX")


@dataclass(frozen=True, slots=True)
class PhotoVerdict:
    ok: bool
    reason: str | None = None


class PhotoCheck(Protocol):
    async def check(self, object_key: str, *, sex: str | None = None) -> PhotoVerdict:
        """``sex`` is the one declared, HOMME or FEMME, when known.

        A model that cannot answer lets the photo through: nobody is blocked by it."""
