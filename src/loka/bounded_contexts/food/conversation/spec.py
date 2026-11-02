"""What Food tells the conversation engine about itself."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import common_facts, text_of
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec, FormField
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.domain.services.extracted_facts import ExtractedFacts
from loka.bounded_contexts.food.domain.menu import MenuDish
from loka.shared.domain.errors import DomainError
from loka.shared.domain.knowledge.dishes import dish_key

_SHARE = (
    "Le plus simple : partage ta position WhatsApp. Sinon, donne la ville et le "
    "quartier, par exemple : Douala, Bonapriso."
)

RESTAURANT_FORM = (
    FormField(
        "restaurant_name",
        "the restaurant's name",
        {"fr": "Comment s'appelle ton restaurant ?", "en": "What is your restaurant called?"},
    ),
    FormField(
        "place",
        "where the restaurant is (share the WhatsApp location, or city and district)",
        {
            "fr": f"Où se trouve le restaurant ? {_SHARE}",
            "en": "Where is the restaurant? Share your WhatsApp location, or give the "
            "city and district.",
        },
    ),
    FormField(
        "dishes",
        "today's dishes, each with its price in FCFA",
        {
            "fr": "Quels plats as-tu aujourd'hui, avec leur prix ?\n"
            "Par exemple : ndolè 2 000, poulet DG 3 500, soya 1 000.\n"
            "Tu peux aussi m'envoyer une photo de ton menu écrit : je lis les plats et "
            "les prix pour toi.",
            "en": "Which dishes do you have today, with their prices?\n"
            "For example: ndole 2000, chicken DG 3500.\n"
            "You can also send me a photo of your written menu: I will read it.",
        },
    ),
)

SEARCH_FORM = (
    FormField(
        "place",
        "where the client is, or wants to eat (WhatsApp location, or city and district)",
        {
            "fr": f"Tu es où ? Je cherche ce qui se mange près de toi aujourd'hui. {_SHARE}",
            "en": "Where are you? I will look for what is cooking near you today. Share "
            "your WhatsApp location, or give the city and district.",
        },
    ),
)

RULES = (
    " Food: a restaurateur who registers his restaurant or gives today's menu is "
    "PUBLISH_MENU (CREATE_RESTAURANT when he only wants to register); put every "
    "dish of the menu in 'dishes' with its price in FCFA ('2 mil' or '2k' = 2000, "
    "a bare '2' for a dish means 2000), the restaurant's name in "
    "'restaurant_name'. 'Même menu qu'hier' or 'comme d'habitude' sets "
    "'same_menu'. 'Le ndolè est fini' is CLOSE_DISHES with 'closed_dishes'; "
    "'c'est fini pour aujourd'hui' is CLOSE_MENU. Someone who wants to eat or "
    "asks where to find a dish is FOOD_SEARCH, with 'dish' when he names one. A "
    "line number picked from a list Kora sent goes in 'choice'."
)

ENTITIES: dict[str, Any] = {
    "restaurant_name": {"type": "string"},
    "dishes": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "price": {"type": "integer"}},
        },
    },
    "same_menu": {"type": "boolean"},
    "closed_dishes": {"type": "array", "items": {"type": "string"}},
    "dish": {"type": "string"},
    "choice": {"type": "integer"},
}


def validate(entities: dict[str, Any], flow: FlowName) -> ExtractedFacts:
    facts, rejected = common_facts(entities)
    name = text_of(entities.get("restaurant_name"), longest=80)
    if name and len(name) >= 2:
        facts["restaurant_name"] = name
    raw = entities.get("dishes")
    if isinstance(raw, list) and raw:
        dishes = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                dishes.append(MenuDish.of(item.get("name"), item.get("price")).as_dict())
            except DomainError as exc:
                rejected[f"dish:{item.get('name')}"] = exc.message
        if dishes:
            facts["dishes"] = dishes
    if entities.get("same_menu") is True:
        facts["same_menu"] = True
    closed = entities.get("closed_dishes")
    if isinstance(closed, list):
        names = [text for text in (text_of(item, longest=60) for item in closed) if text]
        if names:
            facts["closed_dishes"] = names
    dish = text_of(entities.get("dish"), longest=60)
    if dish:
        facts["dish"] = dish
        facts["dish_key"] = dish_key(dish)
    return ExtractedFacts(facts=facts, rejected=rejected)


FOOD = DomainSpec(
    name="food",
    roles={
        FlowName.FOOD_RESTAURANT: "a restaurateur registering his restaurant and "
        "publishing or closing today's menu",
        FlowName.FOOD_SEARCH: "a client looking for a dish cooking today, near him",
    },
    forms={FlowName.FOOD_RESTAURANT: RESTAURANT_FORM, FlowName.FOOD_SEARCH: SEARCH_FORM},
    opening_intents={
        "CREATE_RESTAURANT": FlowName.FOOD_RESTAURANT,
        "PUBLISH_MENU": FlowName.FOOD_RESTAURANT,
        "CLOSE_DISHES": FlowName.FOOD_RESTAURANT,
        "CLOSE_MENU": FlowName.FOOD_RESTAURANT,
        "FOOD_SEARCH": FlowName.FOOD_SEARCH,
    },
    intents=frozenset({"COLLECT_CHOICE", "COLLECT_LOCATION", "COLLECT_MEDIA"}),
    rules=RULES,
    entities=ENTITIES,
    validate=validate,
    cues={
        "CREATE_RESTAURANT": (
            r"\b(mon (restaurant|resto|tourne dos|chantier|maquis|snack|boukarou)|"
            r"je suis (une )?restaurat(eur|rice)|je vends (a manger|des plats)|"
            r"inscrire mon (restaurant|resto)|chop house|my (restaurant|eatery))\b"
        ),
        "FOOD_SEARCH": (
            r"\b(manger|faim|ndole|eru|okok|koki|achu|mbongo|poulet dg|"
            r"poisson braise|soya|beignets?|chop|djaff\w*|tchop|tourne dos|"
            r"restaurant|resto|maquis|plat du jour)\b"
        ),
    },
)
