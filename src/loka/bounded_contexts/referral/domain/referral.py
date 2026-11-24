"""Referral: a member who has paid once shares a link; 10 % of every purchase
made by the people who came through it is credited to them, for life.

Nothing is paid out: the credit is spent in the idia app when it opens. A
member asking for their money is told that the team contacts them once
``CONTACT_THRESHOLD_XAF`` is reached.

Only a number that never wrote to idia before can be referred, and only once:
friends already on idia cannot « refer » each other after the fact.
"""

from __future__ import annotations

import re
import secrets
from urllib.parse import quote

RATE_PERCENT = 10
CONTACT_THRESHOLD_XAF = 2_000

# No 0/O, 1/I/L: a code is read on a phone screen and sometimes retyped.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE = re.compile(r"\bIDIA-([A-Z0-9]{5})\b", re.IGNORECASE)
# What the prefilled message says around the code: nothing to analyse.
_INVITATION_WORDS = frozenset(
    (  # noqa: SIM905 - a word list reads best as text
        "bonjour bonsoir salut hello hi coucou cc idia je viens de la part du d "
        "from i come on behalf of"
    ).split()
)


def new_code() -> str:
    return "IDIA-" + "".join(secrets.choice(_ALPHABET) for _ in range(5))


def code_in(text: str) -> str | None:
    found = _CODE.search(text or "")
    return f"IDIA-{found.group(1).upper()}" if found else None


def only_invitation(text: str) -> bool:
    """The prefilled message alone: the welcome answers it, no model needed."""
    rest = _CODE.sub(" ", text or "").casefold()
    return set(re.findall(r"[a-zà-ÿ]+", rest)) <= _INVITATION_WORDS


def commission(amount_xaf: int) -> int:
    return max(amount_xaf, 0) * RATE_PERCENT // 100


def invitation_text(code: str) -> str:
    return f"Bonjour idia 👋 Je viens de la part de {code}"


def link(code: str, idia_phone: str) -> str:
    digits = "".join(c for c in idia_phone if c.isdigit())
    return f"https://wa.me/{digits}?text={quote(invitation_text(code))}"
