"""What Kora refuses to pass between two members who have not both confirmed.

A number, an account or a link handed over in a relayed message would give a
contact without the other's consent and without the unlock: it is never
relayed, whatever the wording. Numbers spelled out in words count too.
"""

from __future__ import annotations

import re
import unicodedata

_CHANNELS = re.compile(
    r"\b(num(e|é)ro|numb(er)?|whats?app|wa\s*\.\s*me|watsap|facebook|fb|insta(gram)?|"
    r"snap(chat)?|tik\s*tok|telegram|signal|messenger|e-?mail|gmail|yahoo|hotmail)\b"
    r"|@\w|https?://|www\.|\.com\b"
)
_DIGITS = re.compile(r"\d(?:[\s.\-/]*\d){7,}")
_NUMBER_WORDS = re.compile(
    # « un » and « one » left out: they are articles far more often than digits.
    r"\b(zero|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|two|three|four|five|"
    r"seven|eight|nine)\b"
)


def _folded(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").casefold()


def gives_contact(text: str) -> bool:
    """Whether a message hands over, or asks for, a way to reach someone."""
    folded = _folded(text)
    if _CHANNELS.search(text.casefold()) or _CHANNELS.search(folded):
        return True
    if _DIGITS.search(folded):
        return True
    return len(_NUMBER_WORDS.findall(folded)) >= 6
