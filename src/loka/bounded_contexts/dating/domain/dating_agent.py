"""The model conducts a member's dating thread; the code keeps its rules.

Decision of 27 Nov 2026 (« es que c'est piloté par l'IA ? … l'IA n'aurait pas de
problème à comprendre un non »): every message of a member — outside a talk
and outside a payment — is read by the model with the whole situation: the
person proposed, the member's answers and their reasons, who waits on whom, who
is left to propose. The model chooses one move and writes its own words. The
code carries the move out: it alone records an answer, picks who is proposed
(the matching), keeps contacts hidden and charges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from loka.bounded_contexts.dating.domain.talk_reader import TalkLine

# ANSWER_PROPOSAL: a yes or a no to the person proposed (with the reason when
# said). REASON: why the member turned down the last person refused. NEXT: show
# someone. SHOW_PHOTO: the photo of the person proposed again. SET_LOOKING:
# where to look (QUARTIER, VILLE, PAYS, ETRANGER, PEU_IMPORTE). ADD_WISHES: what
# the member wants in the other, in their words. ANSWER: idia answers, nothing
# else changes. WITHDRAW: the member asked, plainly, to delete the profile.
MOVES = (
    "ANSWER_PROPOSAL", "REASON", "NEXT", "SHOW_PHOTO", "SET_LOOKING", "ADD_WISHES",
    "ANSWER", "WITHDRAW",
)


@dataclass(frozen=True, slots=True)
class DatingSituation:
    member: str
    # The member's own profile, as idia knows it: card, criteria, where they look.
    profile: str
    # The person proposed and waiting for a yes or a no, or None.
    proposed: str | None
    # The member's latest answers, newest first: « Helena, 20 ans — non (physique) ».
    answers: list[str]
    # Yeses given and not answered yet, matches, talks: where things stand.
    standing: str
    # How many people are left to propose, and why none when there is none.
    left: str
    thread: list[TalkLine]
    message: str


@dataclass(frozen=True, slots=True)
class DatingMove:
    move: str
    reply: str | None = None
    liked: bool | None = None
    reason: str | None = None
    looking: list[str] = field(default_factory=list)
    wishes: str | None = None
    age_min: int | None = None
    age_max: int | None = None


class DatingAgent(Protocol):
    async def move(self, situation: DatingSituation) -> DatingMove | None:
        """``None`` when the model could not be asked: the thread falls back."""
