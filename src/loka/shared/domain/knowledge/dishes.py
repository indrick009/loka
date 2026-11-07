"""Local dish names, so "ndole", "Ndolè" and "ndolé" are one dish.

Food search reads this: a client asking for "poulet braisé" must find the
restaurant that wrote "poulet grillé". Aliases are matched on whole words,
longest first, after folding accents and case.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from loka.shared.domain.knowledge.market import normalize


@dataclass(frozen=True, slots=True)
class Dish:
    key: str
    name: str
    aliases: tuple[str, ...]


DISHES: tuple[Dish, ...] = (
    Dish("ndole", "Ndolè", ("ndole",)),
    Dish("poulet_dg", "Poulet DG", ("poulet dg", "dg")),
    Dish(
        "poulet_braise", "Poulet braisé", ("poulet braise", "poulet grille", "poulet a la braise")
    ),
    Dish("poisson_braise", "Poisson braisé", (
        "poisson braise", "poisson grille", "bar braise", "maquereau braise", "carpe braisee",
        "poisson a la braise",
    )),
    Dish("porc_braise", "Porc braisé", ("porc braise", "porc grille", "porc")),
    Dish("soya", "Soya", ("soya", "suya", "brochette", "brochettes")),
    Dish("eru", "Eru", ("eru", "eru water fufu", "eru waterfufu")),
    Dish("okok", "Okok", ("okok",)),
    Dish("koki", "Koki", ("koki",)),
    Dish("achu", "Achu", ("achu", "sauce jaune")),
    Dish("mbongo", "Mbongo tchobi", ("mbongo", "mbongo tchobi")),
    Dish("sanga", "Sanga", ("sanga",)),
    Dish("kondre", "Kondrè", ("kondre",)),
    Dish("ekwang", "Ekwang", ("ekwang", "ekwan")),
    Dish("folere", "Folléré", ("folere", "follere")),
    Dish("pile", "Pilé", ("pile", "pommes pilees")),
    Dish("couscous", "Couscous", ("couscous", "couscous mais", "couscous manioc")),
    Dish("bobolo", "Bâton de manioc", ("bobolo", "baton de manioc", "miondo")),
    Dish("bhb", "Beignets haricots bouillie", ("bhb", "beignet haricot", "beignets haricots")),
    Dish("riz", "Riz", ("riz", "riz sauce", "riz gras", "riz jollof", "jollof")),
    Dish("plantain", "Plantains", ("plantain", "plantains", "alloco", "frites de plantain")),
    Dish("spaghetti", "Omelette spaghetti", ("omelette spaghetti", "spaghetti", "spag")),
    Dish("haricot", "Haricots", ("haricot", "haricots")),
    Dish("pepper_soup", "Pepper soup", ("pepper soup", "bouillon", "soupe")),
    Dish("chawarma", "Chawarma", ("chawarma", "shawarma")),
    Dish("poulet", "Poulet", ("poulet",)),
    Dish("poisson", "Poisson", ("poisson",)),
)

_ALIASES = sorted(
    ((normalize(alias), dish) for dish in DISHES for alias in dish.aliases),
    key=lambda item: -len(item[0]),
)
_BY_KEY = {dish.key: dish for dish in DISHES}


def dish_key(text: str | None) -> str | None:
    """The dish a name or a request refers to, or ``None`` when unknown."""
    folded = f" {normalize(text or '')} "
    for alias, dish in _ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", folded):
            return dish.key
    return None


def dish_keys(text: str | None) -> list[str]:
    """Every dish a request names, in order: « poulet ou poisson » is two."""
    folded = f" {normalize(text or '')} "
    found = sorted(
        (match.start(), -len(alias), match.end(), dish.key)
        for alias, dish in _ALIASES
        for match in re.finditer(rf"\b{re.escape(alias)}\b", folded)
    )
    keys: list[str] = []
    covered = -1
    for start, _, end, key in found:
        # The longest name wins where two overlap: « poisson braisé » is the
        # braised fish, not every fish.
        if start < covered:
            continue
        covered = end
        if key not in keys:
            keys.append(key)
    return keys


def related_keys(key: str) -> frozenset[str]:
    """A general dish also finds its variants: "poulet" finds "poulet DG"."""
    if key in ("poulet", "poisson"):
        return frozenset(k for k in _BY_KEY if k == key or k.startswith(key + "_"))
    return frozenset({key})


def dish_name(key: str | None) -> str | None:
    dish = _BY_KEY.get(key or "")
    return dish.name if dish else None
