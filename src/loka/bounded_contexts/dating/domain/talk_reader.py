"""Who a message written during a talk is for: the port.

One job, one small prompt: the general reading of a message, busy with every
service, took « tu aimes quoi le week-end ? » for a question to idia.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# MATCH: for the other member. IDIA: for idia. END: stop with this person.
# CONFIRM: go ahead. SEND / KEEP: pass on, or not, the message idia kept.
# UNCLEAR: the model cannot tell, idia asks.
DECISIONS = ("MATCH", "IDIA", "END", "CONFIRM", "SEND", "KEEP", "UNCLEAR")


@dataclass(frozen=True, slots=True)
class TalkLine:
    speaker: str  # "member" (the one writing now) or "idia"
    text: str


@dataclass(frozen=True, slots=True)
class TalkReading:
    decision: str
    relay_text: str | None = None
    reply: str | None = None


class TalkReader(Protocol):
    async def read(
        self,
        *,
        member: str,
        other: str,
        thread: list[TalkLine],
        kept: str | None,
        message: str,
    ) -> TalkReading | None:
        """``None`` when the model could not be asked: the caller falls back."""
