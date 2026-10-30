"""Descriptions first, location as a filter.

The score is the share of meaningful words two descriptions have in common:
crude, but inspectable — it is stored with each proposal, so a bad match can be
explained.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from loka.bounded_contexts.dating.domain.profile import Profile
from loka.shared.domain.knowledge.market import normalize

_STOPWORDS = frozenset(
    (  # noqa: SIM905 - a word list reads best as text
        "je j me m moi tu te t toi il elle on nous vous ils elles le la les l un une des du "
        "de d et ou mais donc or ni car que qu qui quoi dont a au aux en dans par pour sur "
        "avec sans sous ce cet cette ces mon ma mes ton ta tes son sa ses notre nos votre vos "
        "leur leurs suis es est sommes etes sont ai as avons avez ont etre avoir fait faire "
        "tres plus bien aussi pas ne n y se s si quelqu quelque chose comme tout tous toute "
        "toutes i my you your he she we they the an and but of to in for with is am are be "
        "looking cherche recherche personne "
    ).split()
)


def words(text: str) -> frozenset[str]:
    return frozenset(
        word
        for word in re.findall(r"[a-z]+", normalize(text))
        if len(word) > 2 and word not in _STOPWORDS
    )


def affinity(a: Profile, b: Profile) -> float:
    left, right = words(a.description), words(b.description)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def best_candidates(
    member: Profile, others: Iterable[Profile], *, excluded: set[str]
) -> list[tuple[Profile, float]]:
    """Profiles this member could meet, best described match first."""
    ranked = [
        (other, affinity(member, other))
        for other in others
        if str(other.profile_id) not in excluded and member.could_meet(other)
    ]
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked
