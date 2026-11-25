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
        "pinned": " (position WhatsApp enregistrée)",
        "modern": "moderne",
        "basic": "non moderne",
        "lodging": "logement",
        "lodgings": "logements",
        "rent": "Loyer : {amount} FCFA/mois",
        "included": "Eau et électricité : incluses dans le loyer",
        "extra_metered": "Eau et électricité : à part, selon la consommation",
        "extra_amount": "Eau et électricité : {amount} FCFA/mois en plus du loyer",
        "deposit": "Caution : {months} mois",
        "no_deposit": "Caution : aucune",
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
        "pinned": " (WhatsApp location saved)",
        "modern": "modern",
        "basic": "basic",
        "lodging": "unit",
        "lodgings": "units",
        "rent": "Rent: {amount} FCFA/month",
        "included": "Water and electricity: included in the rent",
        "extra_metered": "Water and electricity: extra, by consumption",
        "extra_amount": "Water and electricity: {amount} FCFA/month on top of the rent",
        "deposit": "Deposit: {months} months",
        "no_deposit": "Deposit: none",
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
        # Also the answer to the last photo of an album, whose first photo may
        # be the one that published the listing: so it says the listing is
        # online, not only that a photo was added.
        "published": (
            "Photo bien reçue ({count}) : votre annonce est en ligne, les "
            "locataires qui cherchent un logement comme le vôtre peuvent la trouver."
        ),
        "draft": "Photo bien reçue ({count}).",
        "one": "1 photo",
        "many": "{photos} photos",
    },
    "en": {
        "just_published": (
            "Photo received. Your listing is now published: tenants looking for "
            "a place like yours can find it. You can still send more photos."
        ),
        "published": (
            "Photo received ({count}): your listing is online, tenants looking "
            "for a place like yours can find it."
        ),
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


_MEDIA_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "video": "Vidéo bien reçue, elle est ajoutée à ton annonce.",
        "video_needs_photo": (
            "Vidéo bien reçue. Pour publier l'annonce, il faut aussi au moins une photo."
        ),
        "document": "Document bien reçu.",
        "kept": (
            "Bien reçu ({count}), je le garde pour ton annonce : il y sera ajouté "
            "dès qu'elle sera enregistrée."
        ),
        "kept_first": (
            # Not housing by default: Nsai's two photos were dishes, and were
            # taken for a lodging to rent (journal of 25 Nov 2026, 09:44).
            "Bien reçu ({count}) 👍 Peux-tu m'en dire un peu plus : c'est pour quoi ?"
        ),
        "AUDIO": (
            "Je ne peux pas encore écouter les notes vocales. Peux-tu me "
            "l'écrire en quelques mots ?"
        ),
        "too_large": (
            "Cette vidéo est trop lourde pour moi : au-delà de 16 Mo, je ne peux "
            "pas la recevoir. Envoie une vidéo plus courte (moins d'une minute) "
            "ou des photos."
        ),
        "other": (
            "Je ne peux pas utiliser ce type de fichier. Envoie plutôt une photo "
            "ou un texte."
        ),
        "kept_published": (
            "Annonce enregistrée avec tes {count} : elle est maintenant publiée, les "
            "locataires qui cherchent un logement comme le tien peuvent la trouver."
        ),
        "one": "1 fichier",
        "many": "{count} fichiers",
        "one_photo": "1 photo",
        "many_photos": "{count} photos",
    },
    "en": {
        "video": "Video received, it is added to your listing.",
        "video_needs_photo": (
            "Video received. To publish the listing, at least one photo is needed too."
        ),
        "document": "Document received.",
        "kept": (
            "Received ({count}), I am keeping it for your listing: it will be added "
            "as soon as the listing is saved."
        ),
        "kept_first": (
            "Received ({count}) 👍 Could you tell me a bit more: what is it for?"
        ),
        "AUDIO": "I cannot listen to voice notes yet. Could you write it in a few words?",
        "too_large": (
            "This video is too heavy for me: above 16 MB I cannot receive it. "
            "Please send a shorter video (under a minute) or photos."
        ),
        "other": "I cannot use this kind of file. Please send a photo or a text instead.",
        "kept_published": (
            "Listing saved with your {count}: it is now published, tenants looking "
            "for a place like yours can find it."
        ),
        "one": "1 file",
        "many": "{count} files",
        "one_photo": "1 photo",
        "many_photos": "{count} photos",
    },
}


def media_received_text(
    *, kind: str, photos: int, published: bool, just_published: bool, language: str
) -> str:
    """What one stored photo, video or document did to the listing."""
    if kind == "PHOTO":
        return photo_received_text(
            photos=photos, published=published, just_published=just_published, language=language
        )
    words = _MEDIA_TEXT.get(language, _MEDIA_TEXT["fr"])
    if kind == "VIDEO":
        # A video is shown to tenants but never stands in for a photo.
        return words["video" if published or photos else "video_needs_photo"]
    return words["document"]


# The question asked about photos sent with no task, by what they show.
_SUBJECT_QUESTION: dict[str, dict[str, str]] = {
    "fr": {
        "FOOD": "C'est un plat : tu veux publier le menu de ton restaurant, ou tu cherches "
        "à manger ?",
        "MENU": "C'est un menu : tu veux publier le menu du jour de ton restaurant ?",
        "LODGING": "C'est un logement : tu veux le mettre en location ? Dis-moi de quel "
        "logement il s'agit.",
        "DOCUMENT": "C'est une pièce d'identité : tu l'as trouvée, ou tu as perdu la tienne ?",
        "PORTRAIT": "C'est pour créer ton profil Rencontre ?",
    },
    "en": {
        "FOOD": "It is a dish: do you want to publish your restaurant's menu, or are you "
        "looking for food?",
        "MENU": "It is a menu: do you want to publish your restaurant's menu of the day?",
        "LODGING": "It is a lodging: do you want to rent it out? Tell me which place it is.",
        "DOCUMENT": "It is an ID document: did you find it, or lose yours?",
        "PORTRAIT": "Is it for your dating profile?",
    },
}


def media_kept_text(
    *,
    count: int,
    photos_only: bool,
    listing_started: bool,
    language: str,
    subject: str | None = None,
) -> str:
    """Sent before any listing exists: kept, and said to be kept."""
    words = _MEDIA_TEXT.get(language, _MEDIA_TEXT["fr"])
    amount = _amount(words, count=count, photos_only=photos_only)
    if listing_started:
        return words["kept"].format(count=amount)
    question = _SUBJECT_QUESTION.get(language, _SUBJECT_QUESTION["fr"]).get(subject or "")
    if question is None:
        return words["kept_first"].format(count=amount)
    received = "Bien reçu" if language != "en" else "Received"
    return f"{received} ({amount}) 👍 {question}"


def kept_media_published_text(*, count: int, photos_only: bool, language: str) -> str:
    """The photos sent early were all the listing was waiting for."""
    words = _MEDIA_TEXT.get(language, _MEDIA_TEXT["fr"])
    return words["kept_published"].format(
        count=_amount(words, count=count, photos_only=photos_only)
    )


def _amount(words: Mapping[str, str], *, count: int, photos_only: bool) -> str:
    one, many = ("one_photo", "many_photos") if photos_only else ("one", "many")
    return words[one] if count == 1 else words[many].format(count=count)


def media_refused_text(reason: str, language: str) -> str:
    """The transport could not store what was sent, and here is why."""
    words = _MEDIA_TEXT.get(language, _MEDIA_TEXT["fr"])
    return words.get(reason, words["other"])


def unsupported_media_text(kind: str, language: str) -> str:
    """A voice note or a sticker: never filed as a photo of a lodging."""
    words = _MEDIA_TEXT.get(language, _MEDIA_TEXT["fr"])
    return words.get(kind, words["other"])


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
        # Observed live: "tu as retenu la localisation ?" — the recap read
        # "Yaoundé" and nothing said the exact spot he shared was kept.
        pinned = location.get("latitude") is not None and not location.get("approximate")
        lines.append(
            f"- {district + ', ' if district else ''}{location['city']}"
            + (words["pinned"] if pinned else "")
        )

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

    # The order the landlord was asked them in: the advance, then the caution.
    for key, label in (("advance_months", "advance"), ("deposit_months", "deposit")):
        months = facts.get(key)
        if isinstance(months, int):
            shown = words["no_deposit"] if label == "deposit" and months == 0 else None
            lines.append("- " + (shown or words[label].format(months=months)))

    amenities = facts.get("amenities")
    if isinstance(amenities, Mapping):
        for key, label in (("security", "guard"), ("parking", "parking")):
            if isinstance(amenities.get(key), bool):
                value = words["yes"] if amenities[key] else words["no"]
                lines.append("- " + words[label].format(value=value))

    lines.append(words["outro"])
    return "\n".join(lines)
