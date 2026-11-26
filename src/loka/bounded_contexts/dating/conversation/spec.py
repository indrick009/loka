"""What Dating tells the conversation engine about itself."""

from __future__ import annotations

from typing import Any

import phonenumbers
from phonenumbers import geocoder

from loka.bounded_contexts.conversation.application.plugs.forms import common_facts, text_of
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec, FormField
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.domain.services.extracted_facts import ExtractedFacts
from loka.bounded_contexts.dating.domain.profile import LOOKING, SEXES
from loka.shared.domain.knowledge import market
from loka.shared.domain.knowledge.jobs import jobs_glossary

# Said word for word before the photo is sent, whoever writes the question.
PHOTO_RULE = {
    "fr": "Important : la photo ne doit pas être générée par l'IA. Toute photo avec "
    "filtre, générée ou modifiée par l'IA entraîne la suppression du compte.",
    "en": "Important: the photo must not be AI-generated. Any photo with a filter, "
    "generated or modified by AI leads to the account being deleted.",
}
# The privacy promise that makes people say everything in their description.
DESCRIPTION_PROMISE = {
    "fr": "Ta description n'est lue que par idia pour te trouver la bonne personne : "
    "elle ne sera jamais montrée à personne.",
    "en": "Only idia reads your description, to find you the right person: it is "
    "never shown to anyone.",
}

PROFILE_FORM = (
    FormField(
        "first_name",
        "the member's first name",
        {
            "fr": "Bienvenue sur idia Rencontres ! Quel est ton prénom ?",
            "en": "Welcome to idia Dating! What is your first name?",
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
        "the member's profession, in their own word; Cameroonian jobs: " + jobs_glossary(),
        {
            "fr": "Que fais-tu dans la vie ?",
            "en": "What do you do for a living?",
        },
    ),
    # No country question: the phone number gives it, and a city abroad
    # (« Lyon ») corrects it. See ``country_of``.
    FormField(
        "city",
        "the city the member lives in; when it is outside Cameroon, its country too",
        {
            "fr": "Dans quelle ville vis-tu ?",
            "en": "Which city do you live in?",
        },
    ),
    FormField(
        "photo",
        "a clear photo of the member's face, sent as an image",
        {
            "fr": "Envoie maintenant une photo de toi, bien nette, où l'on voit ton visage.",
            "en": "Now send a clear photo of yourself, showing your face.",
        },
        notice=PHOTO_RULE,
    ),
    FormField(
        "description",
        "the most important answer, the one matches are made on: who the member "
        "is and the love they look for, what they like and expect from the other, "
        "and their red flags (what they will not accept in a partner); a few words "
        "are not enough, invite them to say more, red flags included; whenever red "
        "flags are asked about, they are written « 🚩 *red flags* »",
        {
            "fr": "Maintenant le plus important : ta description. Présente-toi et dis "
            "clairement quel type d'amour tu recherches, ce que tu aimes, ce que tu "
            "attends de l'autre.\n\n"
            "🚩 *Tes red flags* : ce que tu n'acceptes pas chez l'autre (le mensonge, "
            "la cigarette, l'alcool…). C'est très important pour le match.\n\n"
            "Plus tu es précis, meilleur sera le match.",
            "en": "Now the most important part: your description. Tell me about yourself "
            "and say clearly what kind of love you are looking for, what you like and "
            "what you expect from the other person.\n\n"
            "🚩 *Your red flags*: what you won't accept in a partner (lying, smoking, "
            "drinking…). This matters a lot for the match.\n\n"
            "The more precise, the better the match.",
        },
        notice=DESCRIPTION_PROMISE,
    ),
    FormField(
        "ages",
        "the age range of the person sought: age_min and/or age_max, or ages_any "
        "when any adult age is fine; taken from the description when it says it "
        "(« plus de 26 ans » is age_min 26)",
        {
            "fr": "Quel âge doit avoir la personne que tu cherches ? Par exemple "
            "« 25-35 ans », « plus de 26 ans », ou « peu importe ».",
            "en": "How old should the person you are looking for be? For example "
            "« 25-35 », « over 26 », or « any age ».",
        },
    ),
    FormField(
        "looking",
        "where they look for love: quartier, ville, pays, etranger or peu importe; a "
        "preference (« de préférence », « plutôt », « idéalement », « X mais… ») is "
        "not an exclusion: add peu importe",
        {
            "fr": "Où cherches-tu l'amour ? Dans ton quartier, dans ta ville, dans ton "
            "pays, à l'extérieur du pays (tu peux en choisir plusieurs), ou peu importe ?",
            "en": "Where are you looking for love? Your neighbourhood, your city, your "
            "country, abroad (several are fine), or anywhere?",
        },
    ),
)

# Who a message written in a talk is for, or what it asks of idia.
ADDRESSEES = ("MATCH", "IDIA", "UNCLEAR", "END", "CONFIRM", "SEND", "KEEP")

RULES = (
    " Dating: someone looking for love, a partner or a relationship is "
    "DATING_PROFILE (in Cameroon « le gar », « un gars » is a boyfriend, « une go » "
    "a girlfriend). 'sex' is the member's own: someone looking for a man (« je "
    "cherche l'homme », « le gar ») is FEMME, for a woman is HOMME; never guess it "
    "from a first name. Fill first_name, age (integer), sex (HOMME or FEMME), "
    "profession, city (and country only when said, or plain from a city "
    "abroad: Lyon is France), description (their own words, unchanged, red "
    "flags included) and "
    "looking (any of QUARTIER, VILLE, PAYS, ETRANGER, or PEU_IMPORTE; a mere "
    "preference such as « de préférence à l'étranger mais… » adds PEU_IMPORTE, "
    "and it may be changed later, once the profile exists), and the age range "
    "sought: age_min / age_max (« plus de 26 ans », « entre 25 et 35 », « pas plus "
    "de 40 ans »), read from the description too, or ages_any for « peu importe » "
    "when the open question is the age; it may be changed later too. "
    "Set 'inappropriate' to true when the description is sexual, "
    "offensive or commercial. A description is private: never quote or sum it up "
    "to another member, and never say what someone looks like or is from it. "
    "Once the profile exists (step PUBLISHED), never ask a "
    "profile question again; to change the photo, the member simply sends a new "
    "photo here; during a talk, with the caption « profil » (any other photo "
    "goes to the other member). 'rencontres' in the facts is where the member's yeses stand: "
    "answer from it only, never invent an answer, and never say someone refused "
    "(say they did not follow up). To a profile idia proposed, 'oui' is AFFIRMATIVE "
    "and 'non' is NEGATIVE. While the open question is ask.dating.talk or "
    "ask.dating.relay, the member is talking with 'discussion_avec' through idia: "
    "idia passes on what is written to that person. Read the message with the "
    "recent messages and decide, as a person would, in 'addressee': MATCH when "
    "it is for that person (« tu », « toi » there are that person; a question "
    "about their life, even short, is for them: idia knows nothing of them "
    "beyond the profile and never answers in their place), with 'relay_text' "
    "the exact words for them, copied without the instruction to idia around "
    "them (« demande-lui « tu habites où ? » » gives « tu habites où ? »). IDIA "
    "when it is for idia (how the talk or the app works, changing one's own "
    "photo, a remark about idia's last message), with 'reply' answering it. "
    "END when the member no longer wants to go on with that person or wants "
    "other profiles instead. CONFIRM when they decide to go ahead with that "
    "person. SEND when they ask idia to pass on 'message_garde' (« envoie-lui », "
    "« transmets », « send to him », or yes after idia asked whether to send "
    "it); KEEP when they say not to send it. UNCLEAR only when you really "
    "cannot tell. "
    "Set 'inappropriate' to true only when it is sexual, insulting "
    "or asks for money."
)

ENTITIES: dict[str, Any] = {
    "first_name": {"type": "string"},
    "age": {"type": "integer"},
    "sex": {"type": "string", "enum": list(SEXES)},
    "profession": {"type": "string"},
    "country": {"type": "string"},
    "description": {"type": "string"},
    "looking": {"type": "array", "items": {"type": "string", "enum": list(LOOKING)}},
    "age_min": {"type": "integer"},
    "age_max": {"type": "integer"},
    "ages_any": {"type": "boolean"},
    "inappropriate": {"type": "boolean"},
    "addressee": {"type": "string", "enum": list(ADDRESSEES)},
    "relay_text": {"type": "string"},
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
    pinned = common.get("place") if "latitude" in entities else None
    if not city and pinned:
        # A shared position answers « where do you live » (journal of 5 Nov
        # 2026: a pin in Obobogo, then « Dans quel pays vis-tu ? » three times).
        city = str(pinned["city"])
    if city:
        facts["city"] = market.resolve_city(city) or city.title()
        if "country" not in facts and market.resolve_city(city):
            facts["country"] = "Cameroun"
    age = entities.get("age")
    if isinstance(age, int) and not isinstance(age, bool) and 10 <= age <= 99:
        facts["age"] = age
    if entities.get("sex") in SEXES:
        facts["sex"] = entities["sex"]
    looking = entities.get("looking")
    if isinstance(looking, list):
        picked = [item for item in looking if item in LOOKING]
        if picked:
            facts["looking"] = ["PEU_IMPORTE"] if "PEU_IMPORTE" in picked else picked
    ages = _ages(entities)
    if ages is not None:
        facts["ages"] = ages
    description = text_of(entities.get("description"))
    if description:
        if entities.get("inappropriate") is True:
            rejected["description"] = "inappropriate"
        elif len(description) >= 20:
            facts["description"] = description
        else:
            rejected["description"] = "too short"
    if entities.get("addressee") in ADDRESSEES:
        facts["addressee"] = entities["addressee"]
    relay_text = text_of(entities.get("relay_text"), longest=600)
    if relay_text:
        facts["relay_text"] = relay_text
    if entities.get("inappropriate") is True:
        # Read by a talk: a message to a match is refused, not relayed.
        rejected["message"] = "inappropriate"
    if common.get("media_kind") == "IMAGE" and common.get("media_key"):
        facts["photo"] = common["media_key"]
    return ExtractedFacts(facts=facts, rejected=rejected)


def _ages(entities: dict[str, Any]) -> dict[str, int | None] | None:
    """The age range sought, ``{"min": 26, "max": None}``; any age is two ``None``."""
    bounds = {
        end: value
        for end, value in (("min", entities.get("age_min")), ("max", entities.get("age_max")))
        if isinstance(value, int) and not isinstance(value, bool) and 18 <= value <= 99
    }
    if bounds:
        low, high = bounds.get("min"), bounds.get("max")
        if low is not None and high is not None and low > high:
            low, high = high, low
        return {"min": low, "max": high}
    if entities.get("ages_any") is True:
        return {"min": None, "max": None}
    return None


DATING = DomainSpec(
    name="dating",
    roles={
        FlowName.DATING_PROFILE: "a member creating a dating profile, then "
        "answering the profiles idia proposes, then asking questions to a match "
        "through idia"
    },
    forms={FlowName.DATING_PROFILE: PROFILE_FORM},
    opening_intents={"DATING_PROFILE": FlowName.DATING_PROFILE},
    intents=frozenset({"AFFIRMATIVE", "NEGATIVE", "COLLECT_MEDIA"}),
    rules=RULES,
    entities=ENTITIES,
    validate=validate,
    holding=frozenset({"ask.dating.talk", "ask.dating.plus", "ask.dating.relay"}),
    cues={
        "DATING_PROFILE": (
            # « Trouve-moi le gar », « une go »: in Cameroon a gar is a
            # boyfriend (journal of 25 Nov 2026, 16:38, handed to the team).
            r"\b(quelqu un de serieux|rencontres?|ame s(oe)?ur|l amour|celibataire|"
            r"(le|un|mon|ma|une) (gar|go|copain|copine|cherie?|petite? ami\w*)|(un|mon) gars|"
            r"relation serieuse|"
            r"me caser|ma moitie|go serieuse|dating|marry|serious (man|woman|"
            r"relationship))\b"
        ),
    },
)


def country_of(e164: str) -> str:
    """The country of a phone number, in French: where a member lives unless told otherwise."""
    try:
        number = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return "Cameroun"
    return geocoder.country_name_for_number(number, "fr") or "Cameroun"
