"""The model conducts a talk between two members; the code keeps its rules.

Decision of 26 Nov 2026 (« on fait tout le travail au lieu de laisser l'IA le
faire »): the model reads each message with what it knows of the talk and
chooses a move — pass words on, answer the member itself, show the other's
profile photo again, close, confirm, sell more messages — and writes its own
answers. Every move is carried out by the talk's code, which alone relays,
counts, refuses contacts and charges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from loka.bounded_contexts.dating.domain.talk_reader import TalkLine

# RELAY: words for the other. ANSWER: idia answers the member, nothing goes.
# SEND_KEPT / DROP_KEPT: the words idia kept, sent or dropped. SHOW_PHOTO: the
# other's profile photo again. END: stop with the other. CONFIRM: go ahead.
# BUY_MORE: the member wants more messages. CONTACT: a way to reach each other.
MOVES = (
    "RELAY", "ANSWER", "SEND_KEPT", "DROP_KEPT", "SHOW_PHOTO", "END", "CONFIRM",
    "BUY_MORE", "CONTACT",
)


@dataclass(frozen=True, slots=True)
class TalkSituation:
    member: str
    other: str
    # What the member was shown of the other when proposed, nothing more.
    other_card: str
    thread: list[TalkLine]
    message: str
    kept: str | None
    messages_left: int
    photos_left: int
    member_confirmed: bool
    other_confirmed: bool


@dataclass(frozen=True, slots=True)
class TalkMove:
    move: str
    # RELAY: the exact words for the other.
    relay_text: str | None = None
    # ANSWER (and any move): what idia says to the member, in its own words.
    reply: str | None = None
    # ANSWER: keep the message, to send if the member then says so.
    keep: bool = False


class TalkAgent(Protocol):
    async def move(self, situation: TalkSituation) -> TalkMove | None:
        """``None`` when the model could not be asked: the talk falls back."""
