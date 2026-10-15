"""The summary a landlord confirms before his listing is published.

Platform text, never the model's. Asked to "confirm the summary", the model
wrote "votre propriété a-t-elle d'autres commodités ?" — no summary, no
question to confirm — and the fixed template it replaced said "voici ce que je
retiens" followed by nothing. A landlord cannot confirm values he is not shown,
and a "oui" to an invisible recap publishes whatever the session happens to
hold. Every value printed here is read from the session, as stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

_TYPES: dict[str, dict[str, str]] = {
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

_WORDS: dict[str, dict[str, str]] = {
    "fr": {
        "intro": "Voici votre annonce :",
        "modern": "moderne",
        "basic": "non moderne",
        "lodging": "logement",
        "lodgings": "logements",
        "rent": "Loyer : {amount} FCFA/mois",
        "included": "Eau et électricité : incluses dans le loyer",
        "extra_metered": "Eau et électricité : à part, selon la consommation",
        "extra_amount": "Eau et électricité : {amount} FCFA/mois en plus du loyer",
        "deposit": "Caution : {months} mois",
        "advance": "Avance : {months} mois",
        "guard": "Gardien : {value}",
        "parking": "Parking : {value}",
        "yes": "oui",
        "no": "non",
        "outro": (
            "Confirmez-vous la publication ? Répondez oui pour publier, ou "
            "dites-moi ce qu'il faut corriger."
        ),
    },
    "en": {
        "intro": "Here is your listing:",
        "modern": "modern",
        "basic": "basic",
        "lodging": "unit",
        "lodgings": "units",
        "rent": "Rent: {amount} FCFA/month",
        "included": "Water and electricity: included in the rent",
        "extra_metered": "Water and electricity: extra, by consumption",
        "extra_amount": "Water and electricity: {amount} FCFA/month on top of the rent",
        "deposit": "Deposit: {months} months",
        "advance": "Advance: {months} months",
        "guard": "Guard: {value}",
        "parking": "Parking: {value}",
        "yes": "yes",
        "no": "no",
        "outro": "Do you confirm publishing it? Answer yes to publish, or tell me what to correct.",
    },
}


_PHOTO_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "just_published": (
            "Photo bien reçue. Votre annonce est maintenant publiée : les "
            "locataires qui cherchent un logement comme le vôtre peuvent la "
            "trouver. Vous pouvez encore envoyer d'autres photos."
        ),
        "published": "Photo bien reçue, elle est ajoutée à votre annonce ({count}).",
        "draft": "Photo bien reçue ({count}).",
        "one": "1 photo",
        "many": "{photos} photos",
    },
    "en": {
        "just_published": (
            "Photo received. Your listing is now published: tenants looking for "
            "a place like yours can find it. You can still send more photos."
        ),
        "published": "Photo received, it is added to your listing ({count}).",
        "draft": "Photo received ({count}).",
        "one": "1 photo",
        "many": "{photos} photos",
    },
}


def photo_received_text(
    *, photos: int, published: bool, just_published: bool, language: str
) -> str:
    """Say what the photo did, and nothing it did not.

    The template it replaces asked for photos "indispensables pour publier"
    after every photo, the publishing one included.
    """
    words = _PHOTO_TEXT.get(language, _PHOTO_TEXT["fr"])
    if just_published:
        return words["just_published"]
    count = words["one"] if photos == 1 else words["many"].format(photos=photos)
    return words["published" if published else "draft"].format(count=count)


def property_type_label(property_type: str | None, language: str) -> str:
    """"Studio", "Chambre"... — empty when the type is unknown."""
    return _TYPES.get(language, _TYPES["fr"]).get(str(property_type or ""), "")


def _xaf(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


def listing_recap_text(facts: Mapping[str, Any], language: str) -> str:
    """The listing as stored, line by line, then the question to confirm it.

    A field the session does not hold is left out rather than guessed: an
    empty line would read as "none", which is a claim the landlord never made.
    """
    words = _WORDS.get(language, _WORDS["fr"])
    lines = [words["intro"]]

    kind = _TYPES.get(language, _TYPES["fr"]).get(str(facts.get("property_type") or ""), "")
    standing = {"MODERN": words["modern"], "NON_MODERN": words["basic"]}.get(
        str(facts.get("standing") or ""), ""
    )
    title = (
        f"{kind} {standing}" if language == "fr" else f"{standing} {kind.lower()}"
    ).strip()
    quantity = facts.get("quantity")
    if title:
        count = ""
        if isinstance(quantity, int):
            unit = words["lodging"] if quantity == 1 else words["lodgings"]
            count = f" ({quantity} {unit})"
        lines.append(f"- {title[0].upper()}{title[1:]}{count}")

    location = facts.get("location")
    if isinstance(location, Mapping) and location.get("city"):
        district = str(location.get("neighbourhood") or "").strip()
        lines.append(f"- {district + ', ' if district else ''}{location['city']}")

    rent = facts.get("rent")
    if isinstance(rent, int):
        lines.append("- " + words["rent"].format(amount=_xaf(rent)))

    policy, charges = facts.get("charging_policy"), facts.get("charges")
    if policy == "INCLUDED":
        lines.append("- " + words["included"])
    elif policy == "EXTRA":
        lines.append(
            "- " + words["extra_amount"].format(amount=_xaf(charges))
            if isinstance(charges, int) and charges > 0
            else "- " + words["extra_metered"]
        )

    for key, label in (("deposit_months", "deposit"), ("advance_months", "advance")):
        months = facts.get(key)
        if isinstance(months, int):
            lines.append("- " + words[label].format(months=months))

    amenities = facts.get("amenities")
    if isinstance(amenities, Mapping):
        for key, label in (("security", "guard"), ("parking", "parking")):
            if isinstance(amenities.get(key), bool):
                value = words["yes"] if amenities[key] else words["no"]
                lines.append("- " + words[label].format(value=value))

    lines.append(words["outro"])
    return "\n".join(lines)
