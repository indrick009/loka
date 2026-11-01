"""What Dating tells the conversation engine about itself."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.plugs.forms import common_facts, text_of
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec, FormField
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.domain.services.extracted_facts import ExtractedFacts
from loka.bounded_contexts.dating.domain.profile import LOOKING, SEXES
from loka.shared.domain.knowledge import market

PHOTO_RULE = {
    "fr": "Envoie maintenant une photo de toi, bien nette, où l'on voit ton visage.\n"
    "Important : la photo ne doit pas être générée par l'IA. Toute photo avec "
    "filtre, générée ou modifiée par l'IA entraîne la suppression du compte.",
    "en": "Now send a clear photo of yourself, showing your face.\n"
    "Important: the photo must not be AI-generated. Any photo with a filter, "
    "generated or modified by AI leads to the account being deleted.",
}

PROFILE_FORM = (
    FormField(
        "first_name",
        "the member's first name",
        {
            "fr": "Bienvenue sur Kora Rencontres ! Quel est ton prénom ?",
            "en": "Welcome to Kora Dating! What is your first name?",
        },
    ),
    FormField("age", "the member's age", {"fr": "Quel âge as-tu ?", "en": "How old are you?"}),
    FormField(
        "sex",
        "the member's sex (homme or femme)",
        {
            "fr": "Tu es un homme ou une femme ?",
            "en": "Are you a man or a woman?",
        },
    ),
    FormField(
        "profession",
        "the member's profession",
        {
            "fr": "Que fais-tu dans la vie ?",
            "en": "What do you do for a living?",
        },
    ),
    FormField(
        "country",
        "the country the member lives in",
        {
            "fr": "Dans quel pays vis-tu ?",
            "en": "Which country do you live in?",
        },
    ),
    FormField(
        "city",
        "the city the member lives in",
        {
            "fr": "Et dans quelle ville ?",
            "en": "And which city?",
        },
    ),
    FormField("photo", "a photo of the member (sent as an image)", PHOTO_RULE),
    FormField(
        "description",
        "a description of themselves and of the love they look for",
        {
            "fr": "Maintenant le plus important : ta description. Présente-toi et dis "
            "clairement quel type d'amour tu recherches, ce que tu aimes, ce que tu "
            "attends de l'autre. Elle n'est lue que par Kora pour te trouver la bonne "
            "personne : elle ne sera jamais montrée à personne. Alors dis tout, plus "
            "tu es précis, meilleur sera le match.",
            "en": "Now the most important part: your description. Tell me about yourself "
            "and say clearly what kind of love you are looking for, what you like and "
            "what you expect from the other person. Only Kora reads it, to find you "
            "the right person: it is never shown to anyone. So say everything, the "
            "more precise you are, the better the match.",
        },
    ),
    FormField(
        "looking",
        "where they look for love: quartier, ville, pays, etranger or peu importe",
        {
            "fr": "Où cherches-tu l'amour ? Dans ton quartier, dans ta ville, dans ton "
            "pays, à l'extérieur du pays (tu peux en choisir plusieurs), ou peu importe ?",
            "en": "Where are you looking for love? Your neighbourhood, your city, your "
            "country, abroad (several are fine), or anywhere?",
        },
    ),
)

RULES = (
    " Dating: someone looking for love, a partner or a relationship is "
    "DATING_PROFILE. Fill first_name, age (integer), sex (HOMME or FEMME), "
    "profession, country, city, description (their own words, unchanged) and "
    "looking (any of QUARTIER, VILLE, PAYS, ETRANGER, or PEU_IMPORTE). Set "
    "'seeking' (HOMME or FEMME) only when the description says whom they want "
    "to meet. Set 'inappropriate' to true when the description is sexual, "
    "offensive or commercial. A description is private: never quote or sum it up "
    "to another member. To a profile Kora proposed, 'oui' is AFFIRMATIVE "
    "and 'non' is NEGATIVE."
)

ENTITIES: dict[str, Any] = {
    "first_name": {"type": "string"},
    "age": {"type": "integer"},
    "sex": {"type": "string", "enum": list(SEXES)},
    "profession": {"type": "string"},
    "country": {"type": "string"},
    "description": {"type": "string"},
    "looking": {"type": "array", "items": {"type": "string", "enum": list(LOOKING)}},
    "seeking": {"type": "string", "enum": list(SEXES)},
    "inappropriate": {"type": "boolean"},
}


def validate(entities: dict[str, Any], flow: FlowName) -> ExtractedFacts:
    common, rejected = common_facts({k: v for k, v in entities.items() if k != "city"})
    facts: dict[str, Any] = {k: v for k, v in common.items() if k != "place"}
    rejected.pop("place", None)
    for name, longest in (("first_name", 40), ("profession", 80), ("country", 60)):
        value = text_of(entities.get(name), longest=longest)
        if value and len(value) >= 2:
            facts[name] = value[0].upper() + value[1:]
    city = text_of(entities.get("city"), longest=60)
    if city:
        facts["city"] = market.resolve_city(city) or city.title()
        if "country" not in facts and market.resolve_city(city):
            facts["country"] = "Cameroun"
    age = entities.get("age")
    if isinstance(age, int) and not isinstance(age, bool) and 10 <= age <= 99:
        facts["age"] = age
    if entities.get("sex") in SEXES:
        facts["sex"] = entities["sex"]
    if entities.get("seeking") in SEXES:
        facts["seeking"] = entities["seeking"]
    looking = entities.get("looking")
    if isinstance(looking, list):
        picked = [item for item in looking if item in LOOKING]
        if picked:
            facts["looking"] = ["PEU_IMPORTE"] if "PEU_IMPORTE" in picked else picked
    description = text_of(entities.get("description"))
    if description:
        if entities.get("inappropriate") is True:
            rejected["description"] = "inappropriate"
        elif len(description) >= 20:
            facts["description"] = description
        else:
            rejected["description"] = "too short"
    if common.get("media_kind") == "IMAGE" and common.get("media_key"):
        facts["photo"] = common["media_key"]
    return ExtractedFacts(facts=facts, rejected=rejected)


DATING = DomainSpec(
    name="dating",
    roles={
        FlowName.DATING_PROFILE: "a member creating a dating profile, then "
        "answering the profiles Kora proposes"
    },
    forms={FlowName.DATING_PROFILE: PROFILE_FORM},
    opening_intents={"DATING_PROFILE": FlowName.DATING_PROFILE},
    intents=frozenset({"AFFIRMATIVE", "NEGATIVE", "COLLECT_MEDIA"}),
    rules=RULES,
    entities=ENTITIES,
    validate=validate,
    cues={
        "DATING_PROFILE": (
            r"\b(quelqu un de serieux|rencontres?|ame s(oe)?ur|l amour|celibataire|"
            r"relation serieuse|"
            r"me caser|ma moitie|go serieuse|dating|marry|serious (man|woman|"
            r"relationship))\b"
        ),
    },
)
