"""Every sentence the platform writes itself, in French and English.

Extracted from ``analyse_conversation_message`` along the same line as the
prompts: this module owns *the words*, the use case owns *the decision*.

These sentences are deliberately not handed to a model. They quote the
catalogue — a price, a neighbourhood, a listing that exists — and a rephrased
price is a listing that does not exist. The question keys the outbound layer
renders cover the fixed questions; what is here is the copy that depends on
data the outbound layer does not have, such as which fields a draft is still
missing or which properties a search returned.

Both languages are mandatory for each one: the catalogue must not answer in
French a thread the model read as English.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    ConversationPropertyHit,
    ConversationSearchQuery,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import MAX_RESULT_CHOICES


def reply_kind(*, reason: str | None, question_key: str | None) -> str:
    """What kind of answer this turn owes the user.

    Three cases, decided here so the outbound layer never has to re-derive
    whether an analysis succeeded. ``reason`` is what the pipeline refused:
    answering a question when the message was not understood would move a
    conversation the platform never actually followed.
    """
    if reason is not None:
        return "CLARIFY"
    return "QUESTION" if question_key else "ACK"


# The quality gate reports stable codes; the user gets words. Kept here rather
# than in the outbound layer because the list is dynamic and cannot be a fixed
# ``question_key``. Every platform-authored sentence exists in French (the
# default) and English: the catalogue must not speak French to a thread the
# model read as English, and it cannot hand the phrasing to the model either —
# a rephrased price would be a listing that does not exist.
_LANG_FR = "fr"


def _copy(table: Mapping[str, str], language: str) -> str:
    return table.get(language, table[_LANG_FR])


_MISSING_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "MISSING_PHOTOS": "les photos du logement (envoyez-en au moins une)",
        "MISSING_CHARGES": "les charges mensuelles",
        "MISSING_MINIMUM_DURATION": "la durée minimale de location",
        "MISSING_AVAILABILITY_DATE": "la date de disponibilité",
        "VAGUE_LOCATION": "le quartier précis",
        "MISSING_DEPOSIT": "la caution, en nombre de mois de loyer",
        "MISSING_ADVANCE": "l'avance, en nombre de mois de loyer",
        "MISSING_SECURITY": "le gardien et le parking",
        "MISSING_QUANTITY": "le nombre de logements identiques",
        "AMBIGUOUS_CONDITIONS": "les conditions de location",
        "SUSPICIOUS_PRICE": "le prix, qui semble anormal",
    },
    "en": {
        "MISSING_PHOTOS": "the property photos (send at least one)",
        "MISSING_CHARGES": "the monthly charges",
        "MISSING_MINIMUM_DURATION": "the minimum rental duration",
        "MISSING_AVAILABILITY_DATE": "the availability date",
        "VAGUE_LOCATION": "the exact neighbourhood",
        "MISSING_DEPOSIT": "the caution, in months of rent",
        "MISSING_ADVANCE": "the advance, in months of rent",
        "MISSING_SECURITY": "the guard and the parking",
        "MISSING_QUANTITY": "how many identical lodgings",
        "AMBIGUOUS_CONDITIONS": "the rental conditions",
        "SUSPICIOUS_PRICE": "the price, which looks unusual",
    },
}

_LISTING_UNSAVED_TEXT: dict[str, str] = {
    "fr": (
        "Je n'ai pas pu enregistrer l'annonce pour le moment. "
        "Réessayez dans un instant."
    ),
    "en": "I could not save the listing for now. Please try again in a moment.",
}

_MISSING_LISTING_INTRO: dict[str, str] = {
    "fr": (
        "C'est enregistré, votre annonce est créée. "
        "Pour pouvoir la publier, il manque encore : "
    ),
    "en": (
        "Saved, your listing is created. "
        "To publish it, the following is still missing: "
    ),
}

_JOIN_WORD: dict[str, str] = {"fr": " et ", "en": " and "}
_EMPTY_LIST: dict[str, str] = {"fr": "rien", "en": "nothing"}


def missing_listing_reply(missing: tuple[str, ...], language: str) -> str:
    labels_by_code = _MISSING_LABELS.get(language, _MISSING_LABELS[_LANG_FR])
    labels = [labels_by_code.get(code, code) for code in missing]
    return _copy(_MISSING_LISTING_INTRO, language) + _join(labels, language) + "."


def _join(items: list[str], language: str) -> str:
    if not items:
        return _copy(_EMPTY_LIST, language)
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + _copy(_JOIN_WORD, language) + items[-1]


# A WhatsApp thread is not a results page: five properties is already a long
# message, and the catalogue is read again on the next criteria anyway.
# One list, one bound: the number of lines printed is the number a tenant may
# pick, and the domain owns the bound because it is what validates the answer.
SEARCH_RESULT_LIMIT = MAX_RESULT_CHOICES

_SEARCH_UNAVAILABLE_TEXT: dict[str, str] = {
    "fr": (
        "Je n'ai pas pu lancer la recherche pour le moment. "
        "Réessayez dans un instant, ou reformulez votre demande."
    ),
    "en": (
        "I could not run the search for now. "
        "Please try again in a moment, or rephrase your request."
    ),
}

_NO_RESULTS_TEXT: dict[str, str] = {
    "fr": (
        "Je n'ai pas encore de logement qui corresponde à votre budget. "
        "Pour trouver plus vite, vous pouvez élargir la recherche : changez le "
        "quartier, la ville ou le budget, et je relance tout de suite."
    ),
    "en": (
        "I don't have a property matching your budget yet. To find one faster, "
        "you can widen the search: change the neighbourhood, the city or the "
        "budget, and I'll run it again right away."
    ),
}

_SEARCH_OUTRO: dict[str, str] = {
    "fr": "Répondez avec le numéro du logement qui vous intéresse.",
    "en": "Reply with the number of the property you are interested in.",
}

_PROPERTY_TYPE_LABELS: dict[str, dict[str, str]] = {
    "fr": {
        "APARTMENT": "Appartement",
        "COMMERCIAL": "Local commercial",
        "DUPLEX": "Duplex",
        "HOUSE": "Maison",
        "LAND": "Terrain",
        "LOFT": "Loft",
        "ROOM": "Chambre",
        "STUDIO": "Studio",
    },
    "en": {
        "APARTMENT": "Apartment",
        "COMMERCIAL": "Commercial space",
        "DUPLEX": "Duplex",
        "HOUSE": "House",
        "LAND": "Land",
        "LOFT": "Loft",
        "ROOM": "Room",
        "STUDIO": "Studio",
    },
}

_BEDROOM_LABELS: dict[str, tuple[str, str]] = {
    "fr": ("chambre", "chambres"),
    "en": ("bedroom", "bedrooms"),
}

_PRICE_SUFFIX: dict[str, str] = {"fr": "FCFA/mois", "en": "FCFA/month"}

_RESULTS_INTRO: dict[str, str] = {
    "fr": "J'ai trouvé {count} {word} :",
    "en": "I found {count} {word}:",
}

_RESULTS_WORD: dict[str, tuple[str, str]] = {
    "fr": ("logement", "logements"),
    "en": ("property", "properties"),
}


def _format_xaf(amount: int) -> str:
    """"150 000" — grouped the way it is read out loud here."""
    return f"{amount:,}".replace(",", " ")


def _hit_line(index: int, hit: ConversationPropertyHit, language: str) -> str:
    types = _PROPERTY_TYPE_LABELS.get(language, _PROPERTY_TYPE_LABELS[_LANG_FR])
    kind = types.get(hit.property_type, hit.property_type.title())
    details = [kind]
    if hit.bedrooms:
        singular, plural = _BEDROOM_LABELS.get(language, _BEDROOM_LABELS[_LANG_FR])
        details.append(f"{hit.bedrooms} {plural if hit.bedrooms > 1 else singular}")
    if hit.surface_m2:
        details.append(f"{hit.surface_m2} m2")
    place = ", ".join(part for part in (hit.neighbourhood, hit.city) if part)
    # The price is the public one: rent plus charges when they are billed on
    # top, which is the number a tenant actually compares.
    suffix = _copy(_PRICE_SUFFIX, language)
    return f"{index}. {' '.join(details)} - {place} - {_format_xaf(hit.price_xaf)} {suffix}"


def results_text(hits: Sequence[ConversationPropertyHit], language: str) -> str:
    count = len(hits)
    singular, plural = _RESULTS_WORD.get(language, _RESULTS_WORD[_LANG_FR])
    word = singular if count == 1 else plural
    intro = _copy(_RESULTS_INTRO, language).format(count=count, word=word)
    lines = [intro]
    lines.extend(_hit_line(index, hit, language) for index, hit in enumerate(hits, start=1))
    lines.append(_copy(_SEARCH_OUTRO, language))
    return "\n".join(lines)


def search_query(facts: Mapping[str, Any]) -> ConversationSearchQuery | None:
    """The catalogue query these facts describe, or ``None``.

    ``None`` means the facts cannot describe a search at all — no city, the one
    criterion the read model cannot do without. The caller reports that as a
    failure, because "nothing matches your budget" and "there was nothing to
    search with" are different answers.
    """
    location = facts.get("location")
    if not isinstance(location, dict):
        return None
    city = str(location.get("city") or "").strip()
    if not city:
        return None
    neighbourhood = str(location.get("neighbourhood") or "").strip()

    property_type = facts.get("property_type")
    rent = facts.get("rent")
    rooms = facts.get("rooms")
    bedrooms = rooms.get("bedrooms") if isinstance(rooms, dict) else None

    return ConversationSearchQuery(
        city=city,
        neighbourhoods=(neighbourhood,) if neighbourhood else (),
        property_types=(str(property_type),) if property_type else (),
        budget_xaf=_optional_amount(rent),
        min_bedrooms=_optional_amount(bedrooms, minimum=1),
    )


def _optional_amount(value: Any, *, minimum: int = 0) -> int | None:
    """An int that is not a bool and not below ``minimum``, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= minimum else None


def listing_unsaved_text(language: str) -> str:
    """The confirmation could not be acted on, and saying so beats a silence."""
    return _copy(_LISTING_UNSAVED_TEXT, language)


def search_unavailable_text(language: str) -> str:
    """The search could not run. Not the same sentence as "no match"."""
    return _copy(_SEARCH_UNAVAILABLE_TEXT, language)


def no_results_text(language: str) -> str:
    """Nothing matched, with the one move that can change that."""
    return _copy(_NO_RESULTS_TEXT, language)


# What the landlord reads once his answer has moved the listing. Three cases,
# because they are three different situations for him: the lodging is still on
# offer, some remain, or it has left the catalogue and no tenant will be sent.
_AVAILABILITY_ANSWER_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "delisted": (
            "C'est noté, le logement ne sera plus proposé aux locataires.\n"
            "Dites-moi quand il redevient libre et je le remets en ligne."
        ),
        "some_left": (
            "C'est noté. Il reste {lodgings} logement(s) dans cette annonce, "
            "elle reste proposée aux locataires."
        ),
        "available": (
            "Merci, c'est confirmé. Je transmets au locataire pour qu'il "
            "puisse vous contacter."
        ),
    },
    "en": {
        "delisted": (
            "Noted, the property will no longer be shown to tenants.\n"
            "Tell me when it is free again and I will put it back online."
        ),
        "some_left": (
            "Noted. {lodgings} lodging(s) remain in this listing, so it stays "
            "available to tenants."
        ),
        "available": (
            "Thank you, that is confirmed. I am letting the tenant know so "
            "they can contact you."
        ),
    },
}


def availability_answer_text(
    language: str, *, lodgings_left: int, delisted: bool
) -> str:
    """Acknowledge the landlord's answer, saying what it changed.

    Never a bare "c'est noté": a landlord who has just let one of two studios
    go needs to know the other is still on offer, and one who has let the last
    one go needs to know no tenant will be sent any more.
    """
    table = _AVAILABILITY_ANSWER_TEXT.get(language, _AVAILABILITY_ANSWER_TEXT[_LANG_FR])
    if delisted:
        return table["delisted"]
    if lodgings_left > 1:
        return table["some_left"].format(lodgings=lodgings_left)
    return table["available"]


# What the tenant reads once he has picked a lodging. Kora does not say
# "réservé" and does not promise a visit: it says the landlord is being asked,
# which is the only thing that is actually happening (housing spec §9).
_AVAILABILITY_PENDING_TEXT: dict[str, str] = {
    "fr": (
        "Je demande au bailleur si le logement est encore disponible.\n"
        "Je reviens vers vous dès qu'il répond. Si rien ne vient, dites-le moi "
        "et je vous propose d'autres logements."
    ),
    "en": (
        "I am asking the landlord whether the property is still available.\n"
        "I will come back to you as soon as he answers. If nothing comes, tell "
        "me and I will suggest other properties."
    ),
}

_CHOICE_OUT_OF_RANGE_TEXT: dict[str, str] = {
    "fr": (
        "Ce numéro ne correspond à aucun logement de la liste.\n"
        "Indiquez le numéro affiché à côté du logement qui vous intéresse."
    ),
    "en": (
        "That number does not match any property in the list.\n"
        "Give the number shown next to the property you are interested in."
    ),
}


def availability_pending_text(language: str) -> str:
    """The tenant has chosen; the landlord has not answered yet."""
    return _copy(_AVAILABILITY_PENDING_TEXT, language)


def choice_out_of_range_text(language: str) -> str:
    """His number names nothing. Re-asked rather than resolved to the nearest."""
    return _copy(_CHOICE_OUT_OF_RANGE_TEXT, language)
