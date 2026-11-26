"""Two members who both said yes, asking each other questions through idia.

Before a match is confirmed each may ask the other questions and trade a few
photos, relayed by idia: their numbers stay hidden until both confirm. The
exchange is bounded — ``MESSAGES_PER_PACK`` messages and ``PHOTOS_PER_PACK``
photos each — and a member who wants more buys another pack for the fee.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

MESSAGES_PER_PACK = 10
PHOTOS_PER_PACK = 5

OPEN = "OPEN"
MATCHED = "MATCHED"
CLOSED = "CLOSED"


@dataclass(slots=True)
class Side:
    profile_id: uuid.UUID
    sent: int = 0
    photos: int = 0
    packs: int = 1
    confirmed: bool = False
    # When this member last wrote, sent a photo or confirmed (epoch seconds):
    # whoever acted last waits on the other (see ``Talk.waiting``).
    last_at: int = 0

    @property
    def messages_left(self) -> int:
        return max(self.packs * MESSAGES_PER_PACK - self.sent, 0)

    @property
    def photos_left(self) -> int:
        return max(self.packs * PHOTOS_PER_PACK - self.photos, 0)


@dataclass(slots=True)
class Talk:
    talk_id: uuid.UUID
    first: Side
    second: Side
    status: str
    created_at: datetime

    @classmethod
    def open(cls, a: uuid.UUID, b: uuid.UUID, *, now: datetime) -> Talk:
        return cls(talk_id=uuid.uuid4(), first=Side(a), second=Side(b), status=OPEN, created_at=now)

    @property
    def is_open(self) -> bool:
        return self.status == OPEN

    def side(self, profile_id: uuid.UUID) -> Side:
        return self.first if self.first.profile_id == profile_id else self.second

    def other(self, profile_id: uuid.UUID) -> Side:
        return self.second if self.first.profile_id == profile_id else self.first

    def confirm(self, profile_id: uuid.UUID) -> bool:
        """Record one member's yes; ``True`` once both said it: the match."""
        self.side(profile_id).confirmed = True
        if self.first.confirmed and self.second.confirmed:
            self.status = MATCHED
        return self.status == MATCHED

    def acted(self, profile_id: uuid.UUID, *, now: datetime) -> None:
        self.side(profile_id).last_at = int(now.timestamp())

    def waiting(self) -> tuple[Side, Side] | None:
        """``(waiting, silent)``: the member who acted last and has had no word
        back since, and the one he waits on; ``None`` while nobody waits."""
        first, second = self.first, self.second
        if first.last_at == second.last_at == 0:
            return self._waiting_before_last_at()
        if first.last_at == second.last_at:
            return None
        return (first, second) if first.last_at > second.last_at else (second, first)

    def _waiting_before_last_at(self) -> tuple[Side, Side] | None:
        """Talks opened before ``last_at`` was kept: whoever confirmed, else
        whoever wrote more, waits. Morel had confirmed to Margaret, who never
        answered, and nobody was told (journal of 26 Nov 2026)."""
        first, second = self.first, self.second
        if first.confirmed != second.confirmed:
            return (first, second) if first.confirmed else (second, first)
        said_first, said_second = first.sent + first.photos, second.sent + second.photos
        if said_first == said_second:
            return None
        return (first, second) if said_first > said_second else (second, first)

    def close(self) -> None:
        self.status = CLOSED
