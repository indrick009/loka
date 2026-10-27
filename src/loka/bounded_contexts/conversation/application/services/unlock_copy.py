"""What Kora says, and reads, around the 1 000 FCFA unlock.

Platform text and rules, never the model's. This is the one exchange where a
misread costs money: "oui" must mean pay with the number shown, a number must
mean that number, and nothing else may start a withdrawal. The amount is
always stated in words before anything is requested, and the payer validates
on his own phone — Kora never asks for a PIN.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

# Kept on the tenant's session while the unlock is under way: which listing,
# whose contact, and the number the withdrawal goes to. The underscore keeps it
# out of every prompt.
UNLOCK_KEY = "_unlock"
PAYMENT_QUESTION_KEY = "ask.payment_number"

_MOBILE = re.compile(r"(?:\+|00)?(?:237)?[\s.-]*6(?:[\s.-]*\d){8}")
_YES = re.compile(
    r"^(?:oui|ouais|ok|okay|d'?accord|yes|yep|vas[- ]y|go|je confirme|confirm[eé]?|"
    r"ce num[eé]ro|celui[- ]ci|le m[eê]me|avec ce num[eé]ro|payer)\b",
    re.I,
)
_NO = re.compile(r"^(?:non|no|annule[rz]?|pas maintenant|plus tard|laisse[rz]?)\b", re.I)


class PaymentAnswer(StrEnum):
    THIS_NUMBER = "THIS_NUMBER"
    OTHER_NUMBER = "OTHER_NUMBER"
    CANCEL = "CANCEL"
    UNCLEAR = "UNCLEAR"


def read_payment_answer(text: str) -> tuple[PaymentAnswer, str | None]:
    """His answer to "ce numéro ou un autre ?", and the number it names."""
    found = _MOBILE.search(text)
    if found is not None:
        return PaymentAnswer.OTHER_NUMBER, e164(found.group(0))
    cleaned = text.strip().lower()
    if _NO.match(cleaned):
        return PaymentAnswer.CANCEL, None
    if _YES.match(cleaned):
        return PaymentAnswer.THIS_NUMBER, None
    return PaymentAnswer.UNCLEAR, None


def e164(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    return "+237" + digits[-9:]


def display(phone: str) -> str:
    """"+237658528664" → "6 58 52 86 64", the way it is read aloud."""
    local = "".join(ch for ch in phone if ch.isdigit())[-9:]
    return f"{local[0]} {local[1:3]} {local[3:5]} {local[5:7]} {local[7:9]}"


_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "offer": (
            "Bonne nouvelle : le propriétaire confirme que le logement est toujours "
            "disponible.\nPour recevoir son numéro et la localisation du logement, "
            "les frais Kora sont de 1 000 FCFA, payés une seule fois.\n"
            "Voulez-vous payer avec ce numéro ({phone}) ? Répondez OUI, ou envoyez le "
            "numéro Mobile Money ou Orange Money à utiliser. Répondez NON pour annuler."
        ),
        "requested": (
            "J'ai envoyé une demande de paiement de 1 000 FCFA au {phone}. Validez-la "
            "sur votre téléphone avec votre code secret Mobile Money ou Orange Money. "
            "Je vous envoie le contact du propriétaire dès que le paiement est confirmé."
        ),
        "waiting": (
            "Votre paiement de 1 000 FCFA est en attente de validation sur le {phone}. "
            "Dès qu'il est confirmé, je vous envoie le contact du propriétaire."
        ),
        "unclear": (
            "Répondez OUI pour payer 1 000 FCFA avec ce numéro ({phone}), envoyez un "
            "autre numéro Mobile Money ou Orange Money, ou répondez NON pour annuler."
        ),
        "cancelled": (
            "D'accord, aucun paiement n'est demandé. Si vous changez d'avis, répondez "
            "avec le numéro du logement qui vous intéresse."
        ),
        "already": "Vous avez déjà débloqué ce logement : aucun nouveau paiement n'est demandé.",
        "not_confirmed": (
            "Le propriétaire n'a pas encore confirmé que le logement est disponible : "
            "aucun paiement ne vous est demandé pour le moment."
        ),
        "not_started": (
            "Je n'ai pas pu lancer le paiement pour le moment. Répondez OUI pour "
            "réessayer, ou envoyez un autre numéro Mobile Money ou Orange Money."
        ),
        "failed": (
            "Le paiement de 1 000 FCFA n'a pas abouti, rien n'a été débité. Répondez OUI "
            "pour réessayer avec le {phone}, ou envoyez un autre numéro."
        ),
        "unavailable": (
            "Le propriétaire indique que ce logement n'est plus disponible. Aucun "
            "paiement ne vous est demandé. Je continue à chercher : si un autre "
            "logement correspond, je vous l'envoie ici. Vous pouvez aussi répondre "
            "avec le numéro d'un autre logement de la liste."
        ),
        "contact": (
            "Paiement reçu, merci !\nContact du propriétaire : {phone}\n"
            "Logement : {lodging}"
        ),
        "map": "Position : https://maps.google.com/?q={lat},{lon}",
        "contact_end": "Vous pouvez maintenant l'appeler pour organiser la visite.",
    },
    "en": {
        "offer": (
            "Good news: the landlord confirms the place is still available.\nTo get "
            "their number and the location of the place, Kora's fee is 1,000 FCFA, "
            "paid once.\nDo you want to pay with this number ({phone})? Reply YES, or "
            "send the Mobile Money or Orange Money number to use. Reply NO to cancel."
        ),
        "requested": (
            "I sent a 1,000 FCFA payment request to {phone}. Approve it on your phone "
            "with your Mobile Money or Orange Money PIN. I will send the landlord's "
            "contact as soon as the payment is confirmed."
        ),
        "waiting": (
            "Your 1,000 FCFA payment is waiting for approval on {phone}. As soon as it "
            "is confirmed, I will send the landlord's contact."
        ),
        "unclear": (
            "Reply YES to pay 1,000 FCFA with this number ({phone}), send another "
            "Mobile Money or Orange Money number, or reply NO to cancel."
        ),
        "cancelled": (
            "Alright, no payment is requested. If you change your mind, reply with the "
            "number of the place you are interested in."
        ),
        "already": "You have already unlocked this place: no new payment is requested.",
        "not_confirmed": (
            "The landlord has not confirmed the place is available yet: no payment is "
            "requested for now."
        ),
        "not_started": (
            "I could not start the payment right now. Reply YES to try again, or send "
            "another Mobile Money or Orange Money number."
        ),
        "failed": (
            "The 1,000 FCFA payment did not go through and nothing was charged. Reply "
            "YES to try again with {phone}, or send another number."
        ),
        "unavailable": (
            "The landlord says this place is no longer available. No payment is "
            "requested. I keep looking: if another place matches, I will send it "
            "to you here. You can also reply with the number of another place on "
            "the list."
        ),
        "contact": "Payment received, thank you!\nLandlord's contact: {phone}\nPlace: {lodging}",
        "map": "Location: https://maps.google.com/?q={lat},{lon}",
        "contact_end": "You can now call them to arrange a visit.",
    },
}


def unlock_text(key: str, language: str, **values: Any) -> str:
    words = _TEXT.get(language, _TEXT["fr"])
    if values.get("phone"):
        values = {**values, "phone": display(str(values["phone"]))}
    return words[key].format(**values)


def contact_text(
    *,
    landlord_phone: str,
    lodging: str,
    point: Mapping[str, float] | None,
    language: str,
) -> str:
    """The landlord's number and where the lodging is: what the 1 000 FCFA bought."""
    words = _TEXT.get(language, _TEXT["fr"])
    lines = [
        words["contact"].format(phone="+237 " + display(landlord_phone), lodging=lodging)
    ]
    if point is not None:
        lines.append(words["map"].format(lat=point["latitude"], lon=point["longitude"]))
    lines.append(words["contact_end"])
    return "\n".join(lines)
