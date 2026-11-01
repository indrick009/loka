"""Every city, town and quarter of Cameroon, behind the Douala/Yaoundé index.

The pricing list (``market.NEIGHBOURHOODS``) and the Douala/Yaoundé
gazetteer (``districts``) stay first: they carry aliases and decisions made
by hand. This national index answers only what they do not know — « Obili »
is in Yaoundé, « Sangmélima » is a town, « Molyko » is in Buéa — so a
traveller to Edéa or a tenant in Bafoussam is no longer told « je ne connais
pas ce lieu ». Built from OpenStreetMap and Wikipedia (``data/cameroon.json``,
see ``datasets/README.md``), offline like the rest of the gazetteer.

A name known in two cities resolves to neither, as everywhere else here:
guessing would send someone to the wrong end of the country.
"""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any

_DATA = Path(__file__).with_name("data") / "cameroon.json"


def _key(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    folded = folded.casefold().replace("'", " ").replace("-", " ")
    return re.sub(r"\s+", " ", folded).strip()


@cache
def _index() -> tuple[dict[str, str], dict[str, list[tuple[str, str, float, float]]]]:
    data: dict[str, Any] = json.loads(_DATA.read_text()) if _DATA.exists() else {}
    cities: dict[str, str] = {}
    for city in data.get("cities", []):
        for name in (city["name"], *city.get("aliases", [])):
            cities.setdefault(_key(name), city["name"])
    quarters: dict[str, list[tuple[str, str, float, float]]] = {}
    for name, city, lat, lon, _approx in data.get("quarters", []):
        quarters.setdefault(_key(name), []).append((name, city, lat, lon))
    return cities, quarters


def city_named(text: str) -> str | None:
    """The town a name designates, as OpenStreetMap spells it, or ``None``."""
    return _index()[0].get(_key(text))


def _one(text: str, city: str | None) -> tuple[str, str, float, float] | None:
    found = [
        q for q in _index()[1].get(_key(text), []) if city is None or _key(q[1]) == _key(city)
    ]
    return found[0] if len({q[1] for q in found}) == 1 else None


def quarter_city(text: str) -> str | None:
    """The one city a quarter name belongs to; ``None`` if unknown or in several.

    « Djeleng 2 » is read as Djeleng when the numbered name is unknown — but
    only spelling is relaxed here, never similarity: without a city, a near
    name could belong to any town.
    """
    found = _one(text, None)
    if found is None:
        name, _rank = _base(text)
        cities = {
            q[1]
            for qs in _index()[1].values()
            for q in qs
            if len(name) >= 4 and _base(q[0])[0] == name
        }
        return next(iter(cities)) if len(cities) == 1 else None
    return found[1]


def quarter(text: str, *, city: str | None = None) -> tuple[str, float, float] | None:
    """A quarter's spelling and centre, in the given city when one is given.

    Inside a known city the name is also read loosely, because that is how it
    is typed: « Djeleng 2 » for Ndjéleng II, « Tougan Ville » for Toungang.
    Never without a city — a loose match across the country would move a
    tenant to another region.
    """
    found = _one(text, city) or (_loose(text, city) if city else None)
    return (found[0], found[2], found[3]) if found else None


_SUFFIX = re.compile(r"\b(ville|village|centre|center|quartier|carrefour)\b")
_ROMAN = {"1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v", "un": "i", "deux": "ii"}


def _base(text: str) -> tuple[str, str]:
    """The name without its rank or « ville », and the rank (« ii ») if any."""
    words = _SUFFIX.sub(" ", _key(text)).split()
    rank = ""
    if len(words) > 1 and (words[-1] in _ROMAN or words[-1] in _ROMAN.values()):
        rank = _ROMAN.get(words[-1], words[-1])
        words = words[:-1]
    name = "".join(words)
    # « Ndjéleng » and « Djeleng » are one place: the nasal is often dropped.
    if len(name) > 3 and name[0] in "nm" and name[1] not in "aeiouy":
        name = name[1:]
    return name, rank


def _loose(text: str, city: str) -> tuple[str, str, float, float] | None:
    name, rank = _base(text)
    if len(name) < 4:
        return None
    in_city = [q for qs in _index()[1].values() for q in qs if _key(q[1]) == _key(city)]
    scored = []
    for q in in_city:
        other, other_rank = _base(q[0])
        score = difflib.SequenceMatcher(None, name, other).ratio()
        if score >= 0.8:
            # Same rank first (Djeleng 2 → Ndjéleng II), then the plain name.
            bonus = 0.1 if other_rank == rank else 0.05 if not other_rank else 0.0
            scored.append((score + bonus, q))
    if not scored:
        return None
    scored.sort(key=lambda s: -s[0])
    best = [q for s, q in scored if s == scored[0][0]]
    return best[0] if len({(q[2], q[3]) for q in best}) == 1 else None
