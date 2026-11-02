"""Two members who both said yes, asking each other questions through Kora.

Before a match is confirmed each may ask the other questions and trade a few
photos, relayed by Kora: their numbers stay hidden until both confirm. The
exchange is bounded — ``MESSAGES_PER_PACK`` messages and ``PHOTOS_PER_PACK``
photos each — and a member who wants more buys another pack for 1 000 FCFA.
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

    def close(self) -> None:
        self.status = CLOSED
