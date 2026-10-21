"""Name a place from a point, and a point from a name.

Two questions the conversation keeps asking once a WhatsApp location exists:
which district is this pin in, and where roughly is the district the user
typed. Both are answered from the offline gazetteer in ``districts`` — no
network call, no position leaving the platform.
"""

from __future__ import annotations

from loka.bounded_contexts.ai.domain.knowledge.districts import ALIASES, DISTRICTS, District
from loka.bounded_contexts.ai.domain.knowledge.market import distance_km, normalize

# A pin further than this from every known centre is on the outskirts: naming
# the nearest district would place it somewhere it is not.
NEAREST_DISTRICT_MAX_KM = 2.5

_BY_NAME: dict[str, list[District]] = {}
for _district in DISTRICTS:
    _BY_NAME.setdefault(normalize(_district.name), []).append(_district)
for _alias, _target in ALIASES.items():
    _BY_NAME.setdefault(normalize(_alias), list(_BY_NAME.get(normalize(_target), [])))


def district_centre(name: str, *, city: str | None = None) -> tuple[float, float] | None:
    """The (latitude, longitude) of a named district, or ``None``.

    ``None`` too when the name exists in several cities and none is given:
    guessing would put a tenant's search on the wrong side of the country.
    """
    candidates = [
        district
        for district in _BY_NAME.get(normalize(name), [])
        if city is None or normalize(district.city) == normalize(city)
    ]
    if len({district.city for district in candidates}) != 1:
        return None
    return candidates[0].latitude, candidates[0].longitude


def nearest_district(
    latitude: float,
    longitude: float,
    *,
    within_km: float = NEAREST_DISTRICT_MAX_KM,
) -> str | None:
    """The district whose centre is closest to a pin, if one is close enough."""
    best: tuple[float, str] | None = None
    for district in DISTRICTS:
        distance = distance_km(latitude, longitude, district.latitude, district.longitude)
        if distance <= within_km and (best is None or distance < best[0]):
            best = (distance, district.name)
    return best[1] if best is not None else None
