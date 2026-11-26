"""Location, sex and absolute wishes as filters; then, both ways, how likely
each is to say yes to the other: descriptions, age, wishes (see ``attributes``)
and faces (see ``faces``).

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
from datetime import datetime

from loka.bounded_contexts.dating.domain.attributes import fit
from loka.bounded_contexts.dating.domain.faces import Vector, appeal_to, taste
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


# How much a face being the member's type moves their interest (see ``faces``).
FACE_WEIGHT = 0.4
# Interest before anything is known: what the scores add to or take from.
_NEUTRAL = 0.5
_FLOOR = 0.05


def interest(a: Profile, b: Profile, taste_of_a: Vector | None = None) -> float:
    """How likely ``a`` is to say yes to ``b``: their words in common, the age
    gap, ``a``'s wishes against what ``b`` is, ``b``'s face against ``a``'s
    taste. Never below a floor: unknown is not « no »."""
    _, wanted = fit(a.all_wishes(), b.all_traits())
    score = (
        _NEUTRAL
        + affinity(a, b)
        + AGE_WEIGHT * (age_fit(a, b) - 0.5)
        + wanted
        + FACE_WEIGHT * appeal_to(taste_of_a, b.face)
    )
    return max(score, _FLOOR)


def activity(last: datetime | None, now: datetime | None) -> float:
    """Someone who wrote lately answers: they come first."""
    if last is None or now is None:
        return 0.85
    days = (now - last).total_seconds() / 86400
    if days <= 3:
        return 1.0
    return 0.85 if days <= 7 else 0.7


def best_candidates(
    member: Profile,
    others: Iterable[Profile],
    *,
    excluded: set[str],
    refusals: Mapping[uuid.UUID, tuple[int, int]] | None = None,
    tastes: Mapping[uuid.UUID, Vector | None] | None = None,
    last_wrote: Mapping[uuid.UUID, datetime] | None = None,
    now: datetime | None = None,
) -> list[tuple[Profile, float]]:
    """Profiles this member could meet, best match first.

    Reciprocal (decision of 26 Nov 2026): the chance the member says yes to
    them times the chance they say yes back — a profile the member would like
    but who would never like the member is no match. Then how often the
    profile was turned down (``refusals``) and how lately it wrote.
    """
    seen = refusals or {}
    taste_of = tastes or {}
    wrote = last_wrote or {}
    ranked: list[tuple[Profile, float]] = []
    for other in others:
        if str(other.profile_id) in excluded or not member.could_meet(other):
            continue
        kept, _ = wishes_fit(member, other)
        if not kept:
            continue
        both = interest(member, other, taste_of.get(member.profile_id)) * interest(
            other, member, taste_of.get(other.profile_id)
        )
        score = (
            both
            * appeal(*seen.get(other.profile_id, (0, 0)))
            * activity(wrote.get(other.user_id), now)
        )
        ranked.append((other, round(score, 4)))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return ranked


def tastes_of(
    members: Iterable[Profile], answers: Mapping[uuid.UUID, list[tuple[uuid.UUID, bool]]]
) -> dict[uuid.UUID, Vector | None]:
    """Each member's taste in faces, from the faces they answered."""
    faces = {profile.profile_id: profile.face for profile in members}
    return {
        source: taste((faces[target], liked) for target, liked in given if faces.get(target))
        for source, given in answers.items()
    }


def wishes_fit(a: Profile, b: Profile) -> tuple[bool, float]:
    """Both ways: each one's wishes against what the other is."""
    kept_a, score_a = fit(a.all_wishes(), b.all_traits())
    kept_b, score_b = fit(b.all_wishes(), a.all_traits())
    return kept_a and kept_b, score_a + score_b
