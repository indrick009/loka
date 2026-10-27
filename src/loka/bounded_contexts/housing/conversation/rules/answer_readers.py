"""Readers for the answers that hold more than one value."""

from __future__ import annotations

import re
from typing import Any

from loka.bounded_contexts.housing.conversation.rules.answer_rules import (
    _ANY_MONTHS,
    _CAUTION_MONTHS,
    _MAX_ENTRY_MONTHS,
    _NO_CAUTION,
)


def _security_answer(normalized: str) -> dict[str, bool] | None:
    """ "gardien oui parking non", "pas de gardien", "il y a un parking"."""
    stated: dict[str, bool] = {}
    for axis, word in (("security", "gardien"), ("parking", "parking")):
        absent = rf"\b(pas de|sans|aucun|no)\s+{word}\b|\b{word}\s*:?\s*(non|no|pas)\b"
        present = rf"\b{word}\s*:?\s*(oui|yes|ok)\b|\b(un|le|avec|a|y a)\s+{word}\b"
        if re.search(absent, normalized):
            stated[axis] = False
        elif re.search(present, normalized):
            stated[axis] = True
    return stated or None


def _entry_months(normalized: str) -> dict[str, Any] | None:
    """The advance and, when said, the caution, from one answer.

    ``None`` unless an advance is found: a caution alone is not an answer to
    "combien de mois à l'entrée ?", and the model reads what the rule cannot.
    """
    deposit: int | None = None
    if _NO_CAUTION.search(normalized):
        deposit = 0
    caution = _CAUTION_MONTHS.search(normalized)
    rest = normalized
    if caution is not None:
        deposit = int(caution.group("a") or caution.group("b"))
        rest = normalized[: caution.start()] + " " + normalized[caution.end() :]
    advance = _ANY_MONTHS.search(rest)
    if advance is None:
        return None
    months = int(advance.group(1))
    if months > _MAX_ENTRY_MONTHS or (deposit is not None and deposit > _MAX_ENTRY_MONTHS):
        return None
    entities: dict[str, Any] = {"advance_months": months}
    if deposit is not None:
        entities["deposit_months"] = deposit
    return entities
