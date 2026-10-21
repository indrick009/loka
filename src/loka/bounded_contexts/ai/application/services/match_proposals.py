"""The message that keeps "si un logement correspond, je reviens vers vous".

Platform text, never the model's: every line is a lodging that exists, printed
from the match as it was made, and the numbers are the ones his reply is read
against. A model asked to "present the results" would be free to round a rent
or drop a line, and his "2" would then name a lodging he did not read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from loka.bounded_contexts.ai.application.services.listing_recap import (
    property_type_label,
)

# The listing ids last printed to this tenant, in the order he saw them: his
# "2" is read against this. The leading underscore keeps it out of every
# prompt — ``_public_known_facts`` strips it — so it costs no tokens and cannot
# be read back as something the tenant said.
SHOWN_LISTINGS_KEY = "_shown_listings"

_WORDS: dict[str, dict[str, str]] = {
    "fr": {
        "one": "Bonne nouvelle : j'ai trouvé un logement qui correspond à votre recherche.",
        "many": (
            "Bonne nouvelle : j'ai trouvé {count} logements qui correspondent à "
            "votre recherche."
        ),
        "lodging": "Logement",
        "rent": "{amount} FCFA/mois",
        "photo": "(photo {index}/{total})",
        "outro": (
            "Répondez avec le numéro du logement qui vous intéresse : je demande "
            "au propriétaire s'il est toujours disponible."
        ),
    },
    "en": {
        "one": "Good news: I found a place that matches your search.",
        "many": "Good news: I found {count} places that match your search.",
        "lodging": "Place",
        "rent": "{amount} FCFA/month",
        "photo": "(photo {index}/{total})",
        "outro": (
            "Reply with the number of the place you are interested in: I will ask "
            "the landlord whether it is still available."
        ),
    },
}


def match_proposals_text(proposals: Sequence[Mapping[str, Any]], language: str) -> str:
    """One numbered line per lodging, in the order the choice is read against."""
    words = _WORDS.get(language, _WORDS["fr"])
    count = len(proposals)
    lines = [words["one"] if count == 1 else words["many"].format(count=count)]
    for number, proposal in enumerate(proposals, start=1):
        lines.append(match_proposal_line(proposal, number, language))
    lines.append(words["outro"])
    return "\n".join(lines)


def match_proposal_line(
    proposal: Mapping[str, Any], number: int, language: str
) -> str:
    """The numbered line for one lodging — the one his reply names.

    Doubles as the caption of that lodging's photos: the number a tenant sees
    on the picture is the number he answers with.
    """
    words = _WORDS.get(language, _WORDS["fr"])
    return f"{number}. {_line(proposal, language, words)}"


def match_proposal_caption(
    line: str, *, index: int, total: int, language: str
) -> str:
    """The line, plus ``(2/3)`` when a lodging carries more than one photo.

    Without it a tenant receiving three pictures captioned ``1. Studio …``
    cannot tell whether he is looking at one lodging three times or three
    lodgings, and the number he replies with stops being obvious.
    """
    if total <= 1:
        return line
    words = _WORDS.get(language, _WORDS["fr"])
    return f"{line} {words['photo'].format(index=index, total=total)}"


def _line(proposal: Mapping[str, Any], language: str, words: Mapping[str, str]) -> str:
    kind = property_type_label(proposal.get("property_type"), language) or words["lodging"]
    place = ", ".join(
        str(part) for part in (proposal.get("neighbourhood"), proposal.get("city")) if part
    )
    rent = proposal.get("rent_xaf")
    parts = [kind, place]
    if isinstance(rent, int) and not isinstance(rent, bool):
        parts.append(words["rent"].format(amount=f"{rent:,}".replace(",", " ")))
    return " - ".join(part for part in parts if part)
