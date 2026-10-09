"""Fallback copy for outbound WhatsApp replies.

When a response model is configured the analysis writes the sentence itself and
this module is only the safety net; when it is not, this is the whole voice of
the assistant. Either way the analysis hands over a stable ``question_key`` and
this module owns *how it is phrased* without a model. Keeping the sentences here
rather than next to the question keys means a rewording, a translation or a tone
change never re-runs the model and never touches conversation state.

A key with no sentence must not be sent: an untranslated key leaking into a
thread is worse than a fallback, because the landlord cannot answer a variable
name. Unknown keys therefore degrade to the generic clarification.
"""

from __future__ import annotations

# The order of the property flow is decided upstream (see PROPERTY_REQUIREMENTS
# in the AI context); these are only the sentences that ask for each item.
QUESTION_TEXT: dict[str, str] = {
    "ask.property_type": (
        "Quel type de logement souhaitez-vous publier ?\n"
        "Exemple : appartement, studio, maison, duplex, chambre, local."
    ),
    "ask.location": (
        "Dans quelle ville et dans quel quartier se trouve le logement ?\n"
        "Exemple : Douala, Bastos."
    ),
    "ask.rent": "Quel est le loyer mensuel, en FCFA ?",
    "ask.features": (
        "Combien de chambres et de salles de bain ?\n"
        "Vous pouvez aussi indiquer la superficie en m2."
    ),
    "ask.charges": (
        "Les charges mensuelles sont-elles incluses dans le loyer ?\n"
        "Si non, quel est leur montant en FCFA ?"
    ),
    "ask.minimum_duration": "Quelle est la durée minimale de location souhaitée ?",
    "ask.availability": (
        "Le logement est-il toujours disponible ?\n"
        "Répondez : oui, loue, ou indisponible."
    ),
    "ask.conditions": (
        "Quelles conditions le locataire doit-il respecter ? "
        "(caution, durée, type de locataire...)"
    ),
    "ask.confirm_property": (
        "Voici ce que je retiens. Confirmez-vous pour publier l'annonce ?\n"
        "Répondez oui pour publier, ou indiquez ce qu'il faut corriger."
    ),
    "confirm.published": (
        "Votre bien est publié. Il apparaîtra dans les recherches des locataires."
    ),
}

ACK_TEXT = (
    "C'est bien noté. Continuez quand vous voulez, je vous pose la question "
    "suivante dès que j'ai besoin d'une précision."
)

CLARIFY_TEXT = (
    "Je n'ai pas bien compris. Pouvez-vous reformuler ?\n"
    "Par exemple : « Je veux louer un appartement à Bastos » ou "
    "« Je cherche un studio à Douala à 150 000 FCFA »."
)

SUPPORT_TEXT = (
    "Je peux vous aider à publier un logement, à en chercher un ou à "
    "planifier une visite. Dites-moi simplement ce dont vous avez besoin."
)

# Intents where the platform understood the message but has no question to ask:
# answering with a generic "noted" would be a dead end, so these get an answer
# that actually moves the conversation.
NO_QUESTION_TEXT: dict[str, str] = {
    "SUPPORT": SUPPORT_TEXT,
}


def render_reply(
    *,
    reply_kind: str,
    question_key: str | None,
    intent: str | None = None,
) -> str:
    """The sentence to send, never ``None``: a silent thread is the failure."""
    if reply_kind == "QUESTION":
        return QUESTION_TEXT.get(question_key or "", CLARIFY_TEXT)
    if reply_kind == "ACK":
        if intent:
            return NO_QUESTION_TEXT.get(intent, ACK_TEXT)
        return ACK_TEXT
    return CLARIFY_TEXT
