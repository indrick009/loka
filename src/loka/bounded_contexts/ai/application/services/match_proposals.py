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
from loka.bounded_contexts.ai.domain.knowledge.places import nearest_district

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
        "gap_one": (
            "Je n'ai rien trouvé qui corresponde exactement à votre recherche, mais "
            "ce logement s'en approche :"
        ),
        "gap_many": (
            "Je n'ai rien trouvé qui corresponde exactement à votre recherche, mais "
            "ces {count} logements s'en approchent :"
        ),
        "gap_outro": (
            "Pouvez-vous l'envisager ? Répondez avec le numéro du logement : je "
            "demande au propriétaire s'il est toujours disponible."
        ),
        "over_budget": "{amount} FCFA au-dessus de votre budget",
        "over_advance": "{months} mois d'avance de plus que prévu",
        "same_district": "dans le même quartier",
        "district_away": "à {km} km {origin}",
        "km_away": "à {km} km de l'endroit choisi",
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
        "gap_one": "Nothing matches your search exactly, but this place comes close:",
        "gap_many": "Nothing matches your search exactly, but these {count} places come close:",
        "gap_outro": (
            "Could you consider it? Reply with the number of the place: I will ask "
            "the landlord whether it is still available."
        ),
        "over_budget": "{amount} FCFA above your budget",
        "over_advance": "{months} more months of advance than you planned",
        "same_district": "in the same neighbourhood",
        "district_away": "{km} km {origin}",
        "km_away": "{km} km from the place you chose",
    },
}


def match_proposals_text(
    proposals: Sequence[Mapping[str, Any]],
    language: str,
    *,
    tenant_district: str | None = None,
) -> str:
    """One numbered line per lodging, in the order the choice is read against.

    A gap (§8) is never passed off as a match: the list says nothing matched
    exactly, each line says what differs — the budget, the district — and the
    question is whether he can consider it. ``tenant_district`` is where he
    wants to live, so "dans le même quartier" can be said when it is true.
    """
    words = _WORDS.get(language, _WORDS["fr"])
    count = len(proposals)
    gaps = all(isinstance(proposal.get("gap"), Mapping) for proposal in proposals)
    if gaps:
        head = words["gap_one"] if count == 1 else words["gap_many"].format(count=count)
    else:
        head = words["one"] if count == 1 else words["many"].format(count=count)
    lines = [head]
    for number, proposal in enumerate(proposals, start=1):
        line = match_proposal_line(proposal, number, language)
        if gaps:
            differs = _what_differs(proposal, tenant_district, words)
            line = f"{line} ({differs})" if differs else line
        lines.append(line)
    lines.append(words["gap_outro" if gaps else "outro"])
    return "\n".join(lines)


def _what_differs(
    proposal: Mapping[str, Any], tenant_district: str | None, words: Mapping[str, str]
) -> str:
    gap = proposal.get("gap") or {}
    parts: list[str] = []
    over = gap.get("over_budget_xaf")
    if isinstance(over, int) and over > 0:
        parts.append(words["over_budget"].format(amount=f"{over:,}".replace(",", " ")))
    advance = gap.get("over_advance_months")
    if isinstance(advance, int) and advance > 0:
        parts.append(words["over_advance"].format(months=advance))
    district = _district_of(proposal)
    km = gap.get("distance_km")
    if district and tenant_district and district.casefold() == tenant_district.casefold():
        parts.append(words["same_district"])
    elif isinstance(km, int | float) and km >= 0.5:
        shown = f"{km:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        if tenant_district:
            # The lodging's own district is already on the line: only how far
            # it is from his is added.
            parts.append(
                words["district_away"].format(km=shown, origin=_from(tenant_district, words))
            )
        else:
            parts.append(words["km_away"].format(km=shown))
    return ", ".join(parts)


def _from(place: str, words: Mapping[str, str]) -> str:
    """ "d'Odza", "de Bastos", "from Odza" — French elides before a vowel."""
    if words is _WORDS["en"]:
        return f"from {place}"
    return f"d'{place}" if place[:1].casefold() in "aeiouyhéèêâîôû" else f"de {place}"


def _district_of(proposal: Mapping[str, Any]) -> str | None:
    """The lodging's district as published, else the one its pin falls in."""
    named = proposal.get("neighbourhood")
    if isinstance(named, str) and named.strip():
        return named.strip()
    latitude, longitude = proposal.get("latitude"), proposal.get("longitude")
    if isinstance(latitude, int | float) and isinstance(longitude, int | float):
        return nearest_district(latitude, longitude)
    return None


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
    place = ", ".join(str(part) for part in (_district_of(proposal), proposal.get("city")) if part)
    rent = proposal.get("rent_xaf")
    parts = [kind, place]
    if isinstance(rent, int) and not isinstance(rent, bool):
        parts.append(words["rent"].format(amount=f"{rent:,}".replace(",", " ")))
    return " - ".join(part for part in parts if part)
