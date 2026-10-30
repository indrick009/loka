"""A dating profile: the eight fields, and who it may be shown to.

Residence is country-first: a member may live outside Cameroon, so it is never
forced through the Cameroonian district index.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.knowledge.market import normalize

ADULT_AGE = 18
SEXES = ("HOMME", "FEMME")
# Where a member looks for love. Several may be chosen; "PEU_IMPORTE" is all.
LOOKING = ("QUARTIER", "VILLE", "PAYS", "ETRANGER", "PEU_IMPORTE")


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
        )

    @property
    def sought_sex(self) -> str:
        """Who they want to meet: what the description said, else the other sex."""
        if self.seeking:
            return self.seeking
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

    def could_meet(self, other: Profile) -> bool:
        """Both ways: the sex each seeks, and each lives where the other looks.

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
