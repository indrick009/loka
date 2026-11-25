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
ADDRESSEES = ("MATCH", "IDIA", "UNCLEAR", "END", "CONFIRM", "SEND")

RULES = (
    " Dating: someone looking for love, a partner or a relationship is "
    "DATING_PROFILE. Fill first_name, age (integer), sex (HOMME or FEMME), "
    "profession, city (and country only when said, or plain from a city "
    "abroad: Lyon is France), description (their own words, unchanged, red "
    "flags included) and "
    "looking (any of QUARTIER, VILLE, PAYS, ETRANGER, or PEU_IMPORTE; a mere "
    "preference such as « de préférence à l'étranger mais… » adds PEU_IMPORTE, "
    "and it may be changed later, once the profile exists). "
    "Set 'inappropriate' to true when the description is sexual, "
    "offensive or commercial. A description is private: never quote or sum it up "
    "to another member. Once the profile exists (step PUBLISHED), never ask a "
    "profile question again; to change the photo, the member simply sends a new "
    "photo here. 'rencontres' in the facts is where the member's yeses stand: "
    "answer from it only, never invent an answer, and never say someone refused "
    "(say they did not follow up). To a profile idia proposed, 'oui' is AFFIRMATIVE "
    "and 'non' is NEGATIVE. While the open question is ask.dating.talk, the "
    "message may be written to the other member, whom idia relays it to: "
    "extract nothing but 'addressee' and 'relay_text'. 'addressee' is MATCH when it "
    "is for the other member: there « tu » and « toi » are the other member "
    "(« je veux une photo de toi »), and so is any question about their life, "
    "even short (« quel quartier ? », « pourquoi il y a un souci ? »); idia "
    "knows nothing of them beyond the profile and never answers such a question "
    "itself. IDIA when it is plainly for idia about how the talk or the app "
    "works (« on peut envoyer des vidéos ? »), with 'reply' answering it. END "
    "when the member no longer wants to go on with this person or asks for other "
    "profiles instead (« je ne veux plus », « dis-lui que je ne veux plus », "
    "« montre-moi d'autres profils »). CONFIRM when they decide to go ahead with "
    "this person (« CONFIRMER », « c'est bon pour moi, je la veux »). SEND when "
    "they ask idia to pass on what they wrote just before (« transmets-lui », "
    "« send to him »). UNCLEAR otherwise, above all a question about the other "
    "member in the third person (« qu'est-ce qu'elle attend d'un homme ? »). "
    "'relay_text', for MATCH: the words to deliver, copied from the message "
    "without the instruction to idia around them (« demande-lui « tu habites "
    "où ? » » gives « tu habites où ? »), never rewritten. "
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
            r"\b(quelqu un de serieux|rencontres?|ame s(oe)?ur|l amour|celibataire|"
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
