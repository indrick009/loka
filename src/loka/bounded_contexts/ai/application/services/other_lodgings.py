"""A landlord with several different lodgings publishes them one at a time (§5).

"Deux studios à 65 000 et un appartement à 90 000" is two listings: identical
lodgings are one listing with a quantity, a different type or a different rent
is another listing. The first fills the form under way; the others wait in the
session, and the moment the first is published the next form opens with what
he already said — type, rent, how many — so he is only asked the rest.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loka.bounded_contexts.ai.application.services.listing_recap import property_type_label
from loka.bounded_contexts.ai.domain.services.fact_extraction import NEXT_LODGINGS_KEY

__all__ = ["NEXT_LODGINGS_KEY", "NEXT_LODGING_QUESTION", "next_lodging_facts", "next_lodging_text"]

# The first question a form opened from the list asks: the type, the rent and
# the count are known, and the standing comes next in the §4 order.
NEXT_LODGING_QUESTION = "ask.standing"

_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "next": "Passons maintenant à ton {lodging} : il est moderne ou non ?",
        "priced": "{kind} à {rent} FCFA/mois",
        "lodging": "logement",
    },
    "en": {
        "next": "Now on to your {lodging}: is it modern or not?",
        "priced": "{kind} at {rent} FCFA/month",
        "lodging": "lodging",
    },
}


def next_lodging_facts(lodging: Mapping[str, Any]) -> dict[str, Any]:
    """The facts of the form that opens for this lodging."""
    return {
        name: lodging[name]
        for name in ("property_type", "rent", "quantity")
        if lodging.get(name) is not None
    }


def next_lodging_text(lodging: Mapping[str, Any], language: str) -> str:
    """ "Passons maintenant à ton appartement à 90 000 FCFA/mois : il est moderne ou non ?" """
    words = _TEXT.get(language, _TEXT["fr"])
    label = property_type_label(lodging.get("property_type"), language)
    kind = (label or words["lodging"]).casefold()
    rent = lodging.get("rent")
    described = (
        words["priced"].format(kind=kind, rent=f"{rent:,}".replace(",", " "))
        if isinstance(rent, int)
        else kind
    )
    return words["next"].format(lodging=described)
