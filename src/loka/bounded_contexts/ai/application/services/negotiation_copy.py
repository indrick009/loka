"""§8 in words: the offer put to a landlord, his reply, and what the tenant hears.

The landlord's reply is read by a rule, not the model: "oui" commits him to a
price, and a number is a price a tenant will be told. Only what the rule can
settle is settled here — "c'est déjà pris" goes on to the ordinary availability
reading, which already knows it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

OFFER_QUESTION_KEY = "ask.availability_offer"

_YES = frozenset({"oui", "ok", "okay", "d'accord", "dac", "yes", "accepte", "j'accepte"})
_NO = frozenset({"non", "no", "nope", "jamais"})
# Words that mean the lodging is gone: left to the availability reading.
_GONE = ("pris", "loué", "loue", "occupé", "occupe", "plus disponible", "taken", "rented")
_MONTHS = re.compile(r"(?<!\d)(\d{1,2})\s*(?:mois|months?)\b")
_AMOUNT = re.compile(r"(?<![\d.])(\d[\d\s.\u202f]*\d|\d)\s*(mil|mille|k)?\b")

_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "intro": "Le logement est encore disponible ? Un locataire aimerait visiter.",
        "rent": "{rent} FCFA par mois au lieu de {asked}",
        "advance": "{months} mois d'avance au lieu de {asked}",
        "ask": "Il propose {terms}. Pouvez-vous l'envisager ?",
        "how": (
            "Répondez OUI si vous acceptez, NON si vous gardez vos conditions, ou "
            "indiquez votre prix. Si le logement n'est plus libre, dites-le."
        ),
        "and": " et ",
        "accepted": "Bonne nouvelle : le propriétaire accepte {terms}.",
        "refused": (
            "Le propriétaire garde ses conditions ({terms}), mais le logement est "
            "toujours disponible."
        ),
        "countered": "Le propriétaire propose {terms}. C'est sa dernière proposition.",
        "rent_terms": "{rent} FCFA par mois",
        "advance_terms": "{months} mois d'avance",
    },
    "en": {
        "intro": "Is the place still available? A tenant would like to visit.",
        "rent": "{rent} FCFA a month instead of {asked}",
        "advance": "{months} months of advance instead of {asked}",
        "ask": "They offer {terms}. Could you consider it?",
        "how": (
            "Reply YES if you accept, NO to keep your terms, or give your price. "
            "If the place is no longer free, say so."
        ),
        "and": " and ",
        "accepted": "Good news: the landlord accepts {terms}.",
        "refused": "The landlord keeps their terms ({terms}), but the place is still available.",
        "countered": "The landlord offers {terms}. That is their final word.",
        "rent_terms": "{rent} FCFA a month",
        "advance_terms": "{months} months of advance",
    },
}


def _words(language: str) -> dict[str, str]:
    return _TEXT.get(language, _TEXT["fr"])


def _xaf(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def landlord_offer_text(offer: Mapping[str, Any], language: str) -> str:
    """The availability question with the tenant's terms in it."""
    words = _words(language)
    terms = []
    if isinstance(offer.get("rent_xaf"), int) and isinstance(offer.get("asked_rent_xaf"), int):
        terms.append(
            words["rent"].format(rent=_xaf(offer["rent_xaf"]), asked=_xaf(offer["asked_rent_xaf"]))
        )
    if isinstance(offer.get("advance_months"), int):
        terms.append(
            words["advance"].format(
                months=offer["advance_months"], asked=offer.get("asked_advance_months")
            )
        )
    ask = words["ask"].format(terms=words["and"].join(terms))
    return "\n".join((words["intro"], ask, words["how"]))


def read_offer_reply(text: str | None) -> dict[str, Any] | None:
    """``{"outcome", "rent_xaf", "advance_months"}``, or ``None`` to read it otherwise."""
    folded = (text or "").strip().casefold().replace("\u2019", "'")
    if not folded or any(word in folded for word in _GONE):
        return None
    months = _MONTHS.search(folded)
    without_months = _MONTHS.sub(" ", folded)
    rent = None
    for match in _AMOUNT.finditer(without_months):
        value = int(re.sub(r"\D", "", match.group(1)))
        if match.group(2):
            value *= 1000
        if value >= 1000:
            rent = value
            break
    if rent is not None or months is not None:
        return {
            "outcome": "COUNTERED",
            "rent_xaf": rent,
            "advance_months": int(months.group(1)) if months else None,
        }
    first = re.findall(r"[\w']+", folded)[:1]
    if first and first[0] in _NO:
        return {"outcome": "REFUSED", "rent_xaf": None, "advance_months": None}
    if first and first[0] in _YES:
        return {"outcome": "ACCEPTED", "rent_xaf": None, "advance_months": None}
    return None


def tenant_offer_text(
    offer: Mapping[str, Any] | None, reply: Mapping[str, Any] | None, language: str
) -> str | None:
    """What the tenant hears of the negotiation, before the payment offer."""
    if not offer or not reply:
        return None
    words = _words(language)
    outcome = reply.get("outcome")
    if outcome == "ACCEPTED":
        source: Mapping[str, Any] = offer
    elif outcome == "REFUSED":
        source = {
            "rent_xaf": offer.get("asked_rent_xaf"),
            "advance_months": offer.get("asked_advance_months"),
        }
    elif outcome == "COUNTERED":
        source = reply
    else:
        return None
    terms = []
    if isinstance(source.get("rent_xaf"), int):
        terms.append(words["rent_terms"].format(rent=_xaf(source["rent_xaf"])))
    if isinstance(source.get("advance_months"), int):
        terms.append(words["advance_terms"].format(months=source["advance_months"]))
    if not terms:
        return None
    return words[str(outcome).casefold()].format(terms=words["and"].join(terms))
