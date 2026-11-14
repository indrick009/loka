"""The words idia says about itself and about the user's data, never left to a model.

Three things are fixed here, because they must say exactly the same thing to
everyone: the first message a new user receives, the privacy summary, and the
erasure of the conversation history. A model rewording them could drop the
consent line or promise a deletion the platform does not do.

What idia promises, and nothing more: it connects people, discreetly; it sells
no one's data and promises nothing in the place of the people it connects; on
written request, the conversation history kept by idia is erased — not the
copy WhatsApp keeps on the user's own phone.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Request = Literal["policy", "erase", "confirm_erase"]

# The word that confirms an erasure: unusual enough that no answer to a form
# question ("oui", "ok") can erase anybody by accident.
CONFIRM_WORDS = frozenset({"supprimer", "delete", "effacer", "erase"})

_GREETINGS = frozenset(
    {
        "bonjour", "bonsoir", "salut", "hello", "hi", "hey", "allo", "coucou", "bjr",
        "slt", "bonjour monsieur", "bonjour madame", "good morning", "good evening",
        "bonjour idia", "salut idia", "hello idia", "bonsoir idia",
    }
)
_POLICY = re.compile(r"\b(confidentialite|vie privee|privacy|mes donnees|my data)\b")
_ERASE = re.compile(
    r"\b(supprim\w*|effac\w*|delete|erase|remove)\b.*\b(donnees|historique|conversation|"
    r"messages|data|history|chat)\b"
)


def fold(text: str) -> str:
    """Lower case, no accents, no punctuation or emoji: what a person typed, read plainly."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", plain)).strip()


def is_bare_greeting(text: str) -> bool:
    return fold(text) in _GREETINGS


def privacy_request(text: str, *, erase_pending: bool) -> Request | None:
    """What the user asks about their data, when that is what the message is about."""
    folded = fold(text)
    if erase_pending and folded in CONFIRM_WORDS:
        return "confirm_erase"
    if _ERASE.search(folded):
        return "erase"
    # Short only: « je cherche un studio, mes données c'est… » is not a question about privacy.
    if _POLICY.search(folded) and len(folded.split()) <= 6:
        return "policy"
    return None


def _english(language: str | None) -> bool:
    return (language or "").casefold().startswith("en")


def _policy_pointer(language: str | None, url: str) -> str:
    if url:
        return url
    if _english(language):
        return "write « privacy » to read it"
    return "écris « confidentialité » pour la lire"


def welcome(language: str | None, *, policy_url: str) -> str:
    """The first message a new user receives, with the consent line."""
    pointer = _policy_pointer(language, policy_url)
    if _english(language):
        return (
            "Hi 👋 I'm *idia*, your assistant on WhatsApp.\n"
            "🏠 Housing · 🍲 Food · 🚌 Bus · 💛 Dating · 🪪 Lost item\n"
            "Just tell me what you're looking for.\n"
            "A problem with someone (fake landlord, fake profile, scam)? Write « report ».\n\n"
            f"_By continuing, you accept idia's privacy policy: {pointer}_"
        )
    return (
        "Bonjour 👋 Moi c'est *idia*, ton assistant sur WhatsApp.\n"
        "🏠 Logement · 🍲 Repas · 🚌 Car · 💛 Rencontre · 🪪 Objet perdu\n"
        "Dis-moi simplement ce que tu cherches.\n"
        "Un souci avec quelqu'un (faux bailleur, faux profil, arnaque) ? Écris « réclamation ».\n\n"
        f"_En continuant, tu acceptes la politique de confidentialité d'idia : {pointer}_"
    )


def policy_summary(language: str | None, *, contact_email: str, policy_url: str) -> str:
    if _english(language):
        link = f"\nFull policy: {policy_url}" if policy_url else ""
        return (
            "🔒 *Privacy at idia*\n"
            "• idia only connects people, discreetly. idia sells nobody's data and "
            "promises nothing on behalf of the people it connects.\n"
            "• What you share is used only to answer you and to connect you.\n"
            "• Your conversation history kept by idia can be erased at any time: "
            "write « delete my data ».\n"
            f"• Questions: {contact_email}{link}"
        )
    link = f"\nPolitique complète : {policy_url}" if policy_url else ""
    return (
        "🔒 *Confidentialité chez idia*\n"
        "• idia met seulement en relation, avec discrétion. idia ne vend les données "
        "de personne et ne promet rien à la place des personnes mises en relation.\n"
        "• Ce que tu partages sert uniquement à te répondre et à te mettre en relation.\n"
        "• Ton historique de conversation gardé par idia peut être effacé à tout moment : "
        "écris « supprimer mes données ».\n"
        f"• Questions : {contact_email}{link}"
    )


def erase_question(language: str | None) -> str:
    if _english(language):
        return (
            "Do you want idia to erase your whole conversation history? This does not "
            "delete the messages in your own WhatsApp.\n"
            "Reply *DELETE* to confirm."
        )
    return (
        "Tu veux qu'idia efface tout ton historique de conversation ? Cela ne supprime "
        "pas les messages dans ton propre WhatsApp.\n"
        "Réponds *SUPPRIMER* pour confirmer."
    )


def erased(language: str | None, *, contact_email: str) -> str:
    if _english(language):
        return (
            "Done ✅ Your conversation history has been erased at idia. WhatsApp keeps "
            "its own copy on your phone.\n"
            "A listing, a profile or a payment is not affected: to remove one, "
            f"write to {contact_email}."
        )
    return (
        "C'est fait ✅ Ton historique de conversation a été effacé chez idia. WhatsApp "
        "garde sa propre copie sur ton téléphone.\n"
        "Une annonce, un profil ou un paiement ne sont pas concernés : pour les retirer, "
        f"écris à {contact_email}."
    )
