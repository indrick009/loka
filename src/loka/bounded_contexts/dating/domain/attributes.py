"""What a member is, and what they want in the other: scored, not filtered.

A fixed filter per criterion was too rigid — « plus de 26 ans » excluded a man
of 25 who fitted everything else — and one profile question per criterion
too long (decision of 26 Nov 2026). So a model reads each profile once into
``traits`` (what the member is: « religion » = « chrétienne ») and ``wishes``
(what they want or avoid in the other, and how strongly). The vocabulary
starts with ``BASE_KEYS`` and grows: a key the model creates for one member
(« sport », « tatouages ») is offered for every profile read after.

A wish the other side cannot be checked against counts for nothing. Only a
``MUST`` wish contradicted keeps two people apart; a ``PREFER`` one moves the
order.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from loka.shared.domain.knowledge.market import normalize

MUST = "must"
PREFER = "prefer"
STRENGTHS = (MUST, PREFER)

# The keys every reading starts from; others are added as members bring them.
BASE_KEYS: dict[str, str] = {
    "age": "âge en années (nombre)",
    "taille": "taille en centimètres (nombre)",
    "metier": "métier ou domaine de travail",
    "religion": "religion, ou « aucune »",
    "enfants": "a des enfants : « non », ou leur nombre",
    "veut_enfants": "veut des enfants : « oui » / « non »",
    "tabac": "fume : « oui » / « non »",
    "alcool": "boit : « oui » / « non » / « un peu »",
    "situation": "célibataire, divorcé(e), veuf/veuve…",
    "ville": "ville où la personne vit",
}

# How much one satisfied or contradicted preference moves the ranking, next
# to word affinity (rarely above 0.3) and the age weight (0.5).
PREFER_WEIGHT = 0.3

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass(frozen=True, slots=True)
class Wish:
    """What a member wants (``wants``) or avoids in the other, for one key.

    ``values`` are words to find in the other's trait; ``low`` and ``high``
    bound a number (age, height). Either may be empty.
    """

    key: str
    values: tuple[str, ...] = ()
    low: float | None = None
    high: float | None = None
    wants: bool = True
    strength: str = PREFER

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "values": list(self.values),
            "low": self.low,
            "high": self.high,
            "wants": self.wants,
            "strength": self.strength,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, object]) -> Wish | None:
        key = key_of(raw.get("key"))
        if not key:
            return None
        values = raw.get("values")
        return cls(
            key=key,
            values=tuple(
                normalize(str(v)) for v in (values if isinstance(values, list) else []) if v
            ),
            low=_number(raw.get("low")),
            high=_number(raw.get("high")),
            wants=raw.get("wants") is not False,
            strength=MUST if raw.get("strength") == MUST else PREFER,
        )

    def met_by(self, trait: str | None) -> bool | None:
        """``True`` met, ``False`` contradicted, ``None`` when it cannot be told."""
        if not trait:
            return None
        if self.low is not None or self.high is not None:
            found = _number(trait)
            if found is None:
                return None
            inside = (self.low is None or found >= self.low) and (
                self.high is None or found <= self.high
            )
            return inside if self.wants else not inside
        if not self.values:
            return None
        said = normalize(trait)
        hit = any(value and (value in said or said in value) for value in self.values)
        return hit if self.wants else not hit


def key_of(raw: object) -> str:
    """« Métier » and « metier » are one key: lower case, no accent, underscores."""
    return re.sub(r"[^a-z0-9]+", "_", normalize(str(raw or ""))).strip("_")[:30]


def _number(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw)
    found = _NUMBER.search(str(raw or ""))
    return float(found.group().replace(",", ".")) if found else None


def fit(wishes: tuple[Wish, ...], traits: Mapping[str, str]) -> tuple[bool, float]:
    """``(kept, score)``: ``kept`` is false when a MUST wish is contradicted;
    ``score`` adds ``PREFER_WEIGHT`` per preference met and takes it per one
    contradicted."""
    score = 0.0
    for wish in wishes:
        met = wish.met_by(traits.get(wish.key))
        if met is None:
            continue
        if wish.strength == MUST and not met:
            return False, 0.0
        if wish.strength == PREFER:
            score += PREFER_WEIGHT if met else -PREFER_WEIGHT
    return True, score
