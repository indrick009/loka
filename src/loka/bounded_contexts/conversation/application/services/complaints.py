"""A complaint, from anyone, about anything idia connected: read and answered the same way.

« faux bailleur », « faux profil », « arnaque », « je veux signaler… »: whatever
the service (housing, food, dating, found, booking), the words are fixed here,
never a model's. idia does not judge, promise or state a delay: it records the
account, gives a reference, says « je te reviens très vite », and a person
decides. A report is accepted even when idia did not introduce the two people:
it is marked unverified until someone checks it.

« Signaler » also means declaring a found document (« je veux signaler une CNI
trouvée »): a message about a lost or found item is left to the Found flow.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from loka.bounded_contexts.conversation.application.ports import Exchange
from loka.bounded_contexts.conversation.application.services.privacy import fold

_COMPLAINT = re.compile(
    r"\b(reclamation|reclamer|plainte|plaindre|plains|signaler|signalement|"
    r"faux (?:bailleur|proprietaire|profil|compte|numero|restaurant|agent|annonce)|"
    r"fausse (?:annonce|identite|photo)|arnaqu\w*|escroc\w*|escroquer\w*|fraud\w*|"
    r"complain\w*|report(?:ing)?|scam\w*|fake (?:profile|landlord|account|listing|owner))\b"
)
_FOUND = re.compile(
    r"\b(trouv\w*|perdu\w*|cni|carte|passeport|piece|document|objet|"
    r"found|lost|id card|passport)\b"
)
# Below this, « je veux faire une réclamation » says nothing of what happened.
_DETAILED_WORDS = 10
_ALERT_LINES = 6


def report_hint(language: str | None) -> str:
    """Said once a request is under way — a lodging, a meal, a ticket, a person.

    Never in the welcome: nobody comes to idia to complain.
    """
    if _english(language):
        return "If anything goes wrong or someone behaves badly, please tell me (write « report »)."
    return "Au moindre problème ou abus, merci de nous le signaler (écris « réclamation »)."


def is_complaint(text: str) -> bool:
    folded = fold(text)
    return bool(_COMPLAINT.search(folded)) and not _FOUND.search(folded)


def is_detailed(text: str) -> bool:
    return len(fold(text).split()) >= _DETAILED_WORDS


def _english(language: str | None) -> bool:
    return (language or "").casefold().startswith("en")


def ask_details(language: str | None) -> str:
    if _english(language):
        return (
            "I'm sorry this happened 🙏 Tell me what happened: who (name or number), "
            "when, and send screenshots if you have any. I'll pass it on to the team."
        )
    return (
        "Désolé que ça te soit arrivé 🙏 Raconte-moi ce qui s'est passé : qui (nom ou "
        "numéro), quand, et envoie des captures d'écran si tu en as. Je transmets à l'équipe."
    )


def recorded(language: str | None, *, reference: str) -> str:
    if _english(language):
        return (
            f"Thank you. Your report {reference} is recorded and passed on to the team. "
            "I'll get back to you very soon. You can still send screenshots here."
        )
    return (
        f"Merci. Ta réclamation {reference} est enregistrée et transmise à l'équipe. "
        "Je te reviens très vite. Tu peux encore envoyer des captures ici."
    )


def screenshot_added(language: str | None, *, reference: str) -> str:
    if _english(language):
        return f"Added to your report {reference} ✅"
    return f"Ajoutée à ta réclamation {reference} ✅"


def alert(
    *, reference: str, phone: str, account: str, thread: Sequence[Exchange], screenshots: int
) -> str:
    """What the team reads on WhatsApp: who, what, and the last lines before it."""
    lines = "\n".join(
        f"{'👤' if line.speaker == 'user' else '🤖'} {line.text[:200]}"
        for line in list(thread)[-_ALERT_LINES:]
    )
    shots = f"\nCaptures : {screenshots}" if screenshots else ""
    return (
        f"🚨 Réclamation {reference} (non vérifiée)\n"
        f"De : {phone}{shots}\n\n"
        f"« {account[:1200]} »\n\n"
        f"Derniers échanges :\n{lines or '—'}"
    )


def screenshot_alert(*, reference: str, phone: str) -> str:
    return f"📎 Réclamation {reference} : nouvelle capture de {phone}"
