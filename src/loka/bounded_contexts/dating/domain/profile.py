"""A dating profile: the eight fields, and who it may be shown to.

Residence is country-first: a member may live outside Cameroon, so it is never
forced through the Cameroonian district index.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loka.bounded_contexts.dating.domain.attributes import MUST, PREFER, Wish
from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.knowledge.market import normalize

ADULT_AGE = 18
SEXES = ("HOMME", "FEMME")
# Where a member looks for love. Several may be chosen; "PEU_IMPORTE" is all.
LOOKING = ("QUARTIER", "VILLE", "PAYS", "ETRANGER", "PEU_IMPORTE")


# Set in the traits when a member insists on the age range: it then keeps out.
AGE_STRICT = "_age_strict"


@dataclass(frozen=True, slots=True)
class Profile:
    profile_id: uuid.UUID
    user_id: uuid.UUID
    first_name: str
    age: int
    sex: str
    profession: str
    country: str
    city: str
    photo_key: str
    description: str
    looking: tuple[str, ...]
    seeking: str | None
    created_at: datetime
    # The age sought, inclusive; ``None`` is no bound. Asked after the
    # description: « plus de 26 ans » was only read by the matching, and men of
    # 22 to 25 were proposed (journal of 26 Nov 2026, 07:48).
    age_min: int | None = None
    age_max: int | None = None
    # Read by a model from the profile (see ``attributes``): what the member is,
    # and what they want in the other. Empty until read.
    traits: dict[str, str] = field(default_factory=dict)
    wishes: tuple[Wish, ...] = ()

    def all_traits(self) -> dict[str, str]:
        """The traits read, with what the profile itself says."""
        return {
            **self.traits,
            "age": str(self.age),
            "metier": self.profession,
            "ville": self.city,
            "pays": self.country,
        }

    def all_wishes(self) -> tuple[Wish, ...]:
        """The wishes read, with the age range asked as a preference — a must
        once the member insisted on it (``AGE_STRICT``)."""
        asked = (self.age_min, self.age_max) != (None, None)
        strict = asked and bool(self.traits.get(AGE_STRICT))
        if not asked or (not strict and any(wish.key == "age" for wish in self.wishes)):
            return self.wishes
        age = Wish(
            key="age", low=self.age_min, high=self.age_max, strength=MUST if strict else PREFER
        )
        return (*(wish for wish in self.wishes if wish.key != "age"), age)

    @classmethod
    def create(cls, *, user_id: uuid.UUID, now: datetime, fields: dict[str, Any]) -> Profile:
        age = fields["age"]
        if not isinstance(age, int) or age < ADULT_AGE:
            raise ValidationFailed("dating is for adults only")
        if fields["sex"] not in SEXES:
            raise ValidationFailed("unknown sex")
        looking = tuple(str(item) for item in fields["looking"])
        if not looking or any(item not in LOOKING for item in looking):
            raise ValidationFailed("unknown search area")
        return cls(
            profile_id=uuid.uuid4(),
            user_id=user_id,
            first_name=str(fields["first_name"]),
            age=age,
            sex=str(fields["sex"]),
            profession=str(fields["profession"]),
            country=str(fields["country"]),
            city=str(fields["city"]),
            photo_key=str(fields["photo_key"]),
            description=str(fields["description"]),
            looking=looking,
            seeking=str(fields["seeking"]) if fields.get("seeking") in SEXES else None,
            created_at=now,
            age_min=_bound(fields.get("ages"), "min"),
            age_max=_bound(fields.get("ages"), "max"),
        )

    @property
    def sought_sex(self) -> str:
        """Always the other sex: idia Rencontres proposes no same-sex match.

        ``seeking`` is no longer read (decision of 24 Nov 2026); older rows may
        still carry it.
        """
        return "FEMME" if self.sex == "HOMME" else "HOMME"

    def accepts_place_of(self, other: Profile) -> bool:
        """Whether ``other`` lives where this member looks for love."""
        if "PEU_IMPORTE" in self.looking:
            return True
        same_country = normalize(self.country) == normalize(other.country)
        same_city = same_country and normalize(self.city) == normalize(other.city)
        return (
            (("QUARTIER" in self.looking or "VILLE" in self.looking) and same_city)
            or ("PAYS" in self.looking and same_country)
            or ("ETRANGER" in self.looking and not same_country)
        )

    def accepts_age_of(self, other: Profile) -> bool:
        """Whether ``other``'s age is in the range this member seeks."""
        return (self.age_min is None or other.age >= self.age_min) and (
            self.age_max is None or other.age <= self.age_max
        )

    def looks_like(self, other: Profile) -> bool:
        """Another number, but seemingly the same person: same first name, sex
        and age, and the same work or town. Evrad, 24, carreleur, had a
        profile in Dschang and another in Bafoussam (26 Nov 2026)."""
        first = normalize(self.first_name).split()[:1]
        return (
            other.user_id != self.user_id
            and bool(first)
            and first == normalize(other.first_name).split()[:1]
            and other.sex == self.sex
            and other.age == self.age
            and (
                normalize(other.profession) == normalize(self.profession)
                or normalize(other.city) == normalize(self.city)
            )
        )

    def could_meet(self, other: Profile) -> bool:
        """Both ways: the sex each seeks, and each lives where the other looks.

        The age sought and the other wishes are scored, not filtered (see
        ``attributes``): « plus de 26 ans » kept out a man of 25 who fitted the
        rest (decision of 26 Nov 2026).

        Reciprocity is decided here, once: a proposal is only made when both
        members' location preferences include the other's residence.
        """
        return (
            other.user_id != self.user_id
            and other.sex == self.sought_sex
            and self.sex == other.sought_sex
            and self.accepts_place_of(other)
            and other.accepts_place_of(self)
        )


def _bound(ages: object, name: str) -> int | None:
    """One end of an age range ``{"min": 26, "max": None}``; anything else is none."""
    value = ages.get(name) if isinstance(ages, dict) else None
    return value if isinstance(value, int) and ADULT_AGE <= value <= 99 else None
