"""Location and sex as filters; then age, descriptions and each one's wishes (see
``attributes``).

In Cameroon a woman most often looks for a man older than her: a couple where
he is 0 to 10 years older comes first, a larger or reversed gap after
(decision of 24 Nov 2026). The descriptions add the share of meaningful words
they have in common: crude, but inspectable. The model then reads the best few
both ways (see ``judge``); this order is what is used when it cannot.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping

from loka.bounded_contexts.dating.domain.attributes import fit
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


# The age weight next to word affinity, which rarely goes above 0.3.
AGE_WEIGHT = 0.5
# A profile often turned down comes later (decision of 24 Nov 2026). The rate
# is smoothed: two « non » to a profile seen twice is not yet a verdict.
REJECTION_WEIGHT = 0.5
_PRIOR_VIEWS = 3


def appeal(refused: int, seen: int) -> float:
    """1 for a profile nobody turned down, down to 0.5 for one everybody did."""
    rate = refused / (seen + _PRIOR_VIEWS) if seen > 0 else 0.0
    return 1 - REJECTION_WEIGHT * min(rate, 1.0)


def age_fit(a: Profile, b: Profile) -> float:
    """How usual the age gap is here: the man older by 0 to 10 years is best."""
    man, woman = (a, b) if a.sex == "HOMME" else (b, a)
    gap = man.age - woman.age
    if 0 <= gap <= 10:
        return 1.0
    if 10 < gap <= 15:
        return 0.7
    if -3 <= gap < 0:
        return 0.5
    if gap > 15:
        return 0.3
    return 0.2


def best_candidates(
    member: Profile,
    others: Iterable[Profile],
    *,
    excluded: set[str],
    refusals: Mapping[uuid.UUID, tuple[int, int]] | None = None,
) -> list[tuple[Profile, float]]:
    """Profiles this member could meet, best match first.

    ``refusals`` gives, per profile, how many « non » it got out of how many
    answers: a profile often turned down comes later.
    """
    seen = refusals or {}
    ranked: list[tuple[Profile, float]] = []
    for other in others:
        if str(other.profile_id) in excluded or not member.could_meet(other):
            continue
        kept, wanted = wishes_fit(member, other)
        if not kept:
            continue
        base = affinity(member, other) + AGE_WEIGHT * age_fit(member, other) + wanted
        ranked.append((other, max(base, 0.0) * appeal(*seen.get(other.profile_id, (0, 0)))))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def wishes_fit(a: Profile, b: Profile) -> tuple[bool, float]:
    """Both ways: each one's wishes against what the other is."""
    kept_a, score_a = fit(a.all_wishes(), b.all_traits())
    kept_b, score_b = fit(b.all_wishes(), a.all_traits())
    return kept_a and kept_b, score_a + score_b
