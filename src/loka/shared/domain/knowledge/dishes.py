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
    """``sides``: what it is usually eaten with, when the plate needs one.

    Empty for a plate complete in itself (Poulet DG, achu, soya) and for an
    accompaniment sold alone (riz, plantains, couscous). A restaurateur who
    names a sauce without its accompaniment is asked which one: a client must
    know whether his ndolè comes with plantain or with bâton.
    """

    key: str
    name: str
    aliases: tuple[str, ...]
    sides: tuple[str, ...] = ()


_BRAISE = ("bâton de manioc", "miondo", "plantain frit", "frites")
_SAUCE = ("plantain", "bâton de manioc", "riz")

DISHES: tuple[Dish, ...] = (
    Dish("ndole", "Ndolè", ("ndole",), ("plantain mûr", "bâton de manioc", "miondo", "riz")),
    Dish("poulet_dg", "Poulet DG", ("poulet dg", "dg")),
    Dish(
        "poulet_braise",
        "Poulet braisé",
        ("poulet braise", "poulet grille", "poulet a la braise"),
        _BRAISE,
    ),
    Dish("poisson_braise", "Poisson braisé", (
        "poisson braise", "poisson grille", "bar braise", "maquereau braise", "carpe braisee",
        "poisson a la braise", "tilapia braise", "machoiron braise",
    ), _BRAISE),
    Dish("porc_braise", "Porc braisé", ("porc braise", "porc grille", "porc"), _BRAISE),
    Dish("soya", "Soya", ("soya", "suya", "brochette", "brochettes")),
    # Escargots, braisés or in sauce: a plate in Cameroon (journal of 25 Nov
    # 2026, 14:24: a seller of escargots was handed to the team).
    Dish(
        "escargots",
        "Escargots",
        ("escargot", "escargots", "nyamangoro"),
        ("plantain", "bâton de manioc", "miondo"),
    ),
    Dish("eru", "Eru", ("eru",), ("water fufu", "garri")),
    Dish("okok", "Okok", ("okok",), ("bâton de manioc", "manioc")),
    Dish("koki", "Koki", ("koki",), ("plantain", "manioc")),
    Dish("achu", "Achu", ("achu", "sauce jaune", "taro sauce jaune")),
    Dish("mbongo", "Mbongo tchobi", ("mbongo", "mbongo tchobi"), _SAUCE),
    Dish("nkui", "Nkui", ("nkui",), ("couscous maïs",)),
    Dish("njama_njama", "Njama njama", ("njama njama", "jama jama"), ("couscous maïs",)),
    Dish("gombo", "Sauce gombo", ("gombo", "sauce gombo", "okra"), (
        "couscous maïs", "water fufu", "garri",
    )),
    Dish("arachide", "Sauce arachide", ("sauce arachide", "sauce d arachide"), ("riz", "plantain")),
    Dish("nnam", "Mets d'arachide", ("nnam owondo", "mets d arachide", "mets de pistache",
                                     "nnam ngon"), ("plantain", "manioc")),
    Dish("kpem", "Kpem", ("kpem", "feuilles de manioc"), ("plantain", "bâton de manioc")),
    Dish("folong", "Folong", ("folong",), ("plantain", "manioc")),
    Dish("ndomba", "Ndomba", ("ndomba", "papillote"), ("plantain", "bâton de manioc")),
    Dish("sanga", "Sanga", ("sanga",)),
    Dish("kondre", "Kondrè", ("kondre",)),
    Dish("ekwang", "Ekwang", ("ekwang", "ekwan")),
    Dish("folere", "Folléré", ("folere", "follere"), ("couscous", "riz")),
    Dish("pile", "Pilé", ("pile", "pommes pilees")),
    Dish("couscous", "Couscous", ("couscous", "couscous mais", "couscous manioc")),
    Dish("bobolo", "Bâton de manioc", ("bobolo", "baton de manioc", "miondo")),
    Dish("water_fufu", "Water fufu", ("water fufu", "waterfufu", "fufu")),
    Dish("garri", "Garri", ("garri", "tapioca")),
    Dish("bhb", "Beignets haricots bouillie", ("bhb", "beignet haricot", "beignets haricots")),
    Dish("riz", "Riz", ("riz", "riz sauce", "riz gras", "riz jollof", "jollof")),
    Dish("plantain", "Plantains", ("plantain", "plantains", "alloco", "frites de plantain")),
    Dish("spaghetti", "Omelette spaghetti", ("omelette spaghetti", "spaghetti", "spag")),
    Dish("haricot", "Haricots", ("haricot", "haricots"), ("riz", "plantain", "beignets")),
    Dish("pepper_soup", "Pepper soup", ("pepper soup", "bouillon", "soupe")),
    Dish("chawarma", "Chawarma", ("chawarma", "shawarma")),
    Dish("poulet", "Poulet", ("poulet",), _BRAISE),
    Dish("poisson", "Poisson", ("poisson",), _BRAISE),
)

# What a plate is eaten with: named, the plate needs nothing more.
SIDE_WORDS = (
    "plantain", "plantains", "alloco", "baton", "batons", "bobolo", "miondo", "manioc",
    "riz", "couscous", "fufu", "garri", "tapioca", "macabo", "taro", "igname", "patate",
    "patates", "frites", "pain", "pomme", "pommes", "beignet", "beignets", "complement",
    "complements", "accompagnement",
)
_SIDES = re.compile(rf"\b({'|'.join(SIDE_WORDS)})\b")


def needs_side(name: str) -> Dish | None:
    """The dish a menu line names when it lacks the accompaniment it is eaten with."""
    dish = _BY_KEY.get(dish_key(name) or "")
    if dish is None or not dish.sides:
        return None
    return None if _SIDES.search(normalize(name)) else dish


def is_side(name: str) -> bool:
    """A menu line that is an accompaniment, or names one."""
    return _SIDES.search(normalize(name)) is not None


_ALIASES = sorted(
    ((normalize(alias), dish) for dish in DISHES for alias in dish.aliases),
    key=lambda item: -len(item[0]),
)
_BY_KEY = {dish.key: dish for dish in DISHES}
# Accompaniments sold as such: never the plate when a line names both.
_SIDE_KEYS = frozenset({"couscous", "bobolo", "water_fufu", "garri", "riz", "plantain"})


def dish_key(text: str | None) -> str | None:
    """The dish a name or a request refers to, or ``None`` when unknown.

    The plate before its accompaniment: « ndolè plantain » is the ndolè.
    """
    keys = dish_keys(text)
    plates = [key for key in keys if key not in _SIDE_KEYS]
    chosen = plates or keys
    return chosen[0] if chosen else None


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


# What kind of thing a menu line is. « J'ai faim » is answered with meals
# only, never a juice or a cream (decision of 25 Nov 2026, a dessert seller).
MEAL, DRINK, DESSERT = "MEAL", "DRINK", "DESSERT"
_DRINK = re.compile(
    r"\b(jus|bissap|gingembre|citronnelle|boissons?|sodas?|coca|fanta|top|djino|eau|"
    r"bieres?|vin|cafe|lait|smoothies?|cocktails?|milkshakes?|kossam)\b"
)
_DESSERT = re.compile(
    r"\b(desserts?|cremes?|glaces?|gateaux?|cakes?|tartes?|patisseries?|yaourts?|"
    r"yogourts?|salade de fruits|crepes?|chocolat|flans?|mousses?|cookies?|biscuits?|"
    r"donuts?|muffins?|tiramisu|fondants?|puddings?|sorbets?|cupcakes?|brownies?)\b"
)
_GENERIC = {
    "dessert": DESSERT, "desserts": DESSERT, "boisson": DRINK, "boissons": DRINK,
    "a boire": DRINK, "quelque chose a boire": DRINK, "drink": DRINK, "drinks": DRINK,
}


def category_of(name: str) -> str:
    """MEAL, DRINK or DESSERT: a plate idia knows is a meal, whatever it holds."""
    key = dish_key(name)
    if key is not None:
        return MEAL
    folded = normalize(name)
    if _DESSERT.search(folded):
        return DESSERT
    if _DRINK.search(folded):
        return DRINK
    return MEAL


def asked_category(request: str) -> str | None:
    """« Un dessert », « quelque chose à boire »: a kind asked for, not a dish."""
    return _GENERIC.get(normalize(request).removeprefix("un ").removeprefix("une ").strip())
