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
    """The one city a quarter name belongs to; ``None`` if unknown or in several."""
    found = _one(text, None)
    return found[1] if found else None


def quarter(text: str, *, city: str | None = None) -> tuple[str, float, float] | None:
    """A quarter's spelling and centre, in the given city when one is given."""
    found = _one(text, city)
    return (found[0], found[2], found[3]) if found else None
