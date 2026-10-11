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
# and SEARCH_REQUIREMENTS in the AI context); these are only the sentences that
# ask for each item. The search keys word the tenant's side of the same steps:
# a landlord is asked where the property is, a tenant where they want to look.
QUESTION_TEXT: dict[str, str] = {
    "ask.property_type": (
        "Quel type de logement souhaitez-vous publier ?\n"
        "Exemple : appartement, studio, chambre, maison, local."
    ),
    "ask.standing": (
        "Le logement est-il moderne ou non ?\n"
        "Répondez : moderne, ou non."
    ),
    "ask.location": (
        "Dans quelle ville et dans quel quartier se trouve le logement ?\n"
        "Exemple : Douala, Bastos."
    ),
    "ask.rent": "Quel est le loyer mensuel, en FCFA ?",
    "ask.features": (
        "Combien de chambres ?\n"
        "Vous pouvez aussi indiquer la superficie en m2."
    ),
    "ask.charges": (
        "Les charges mensuelles sont-elles incluses dans le loyer ?\n"
        "Si non, quel est leur montant en FCFA ?"
    ),
    "ask.deposit": (
        "Quel est le montant de la caution (dépôt de garantie), en FCFA ?\n"
        "C'est la somme remboursable demandée au locataire à l'entrée."
    ),
    "ask.minimum_duration": "Quelle est la durée minimale de location souhaitée ?",
    "ask.availability": (
        "À partir de quand le logement est-il disponible ?\n"
        "Répondez : immédiatement, ou une date (exemple : 01/11/2026)."
    ),
    "ask.conditions": (
        "Quelles sont les conditions d'entrée ?\n"
        "Précisez par exemple l'avance (nombre de mois à payer d'avance), "
        "les frais d'agence, et le type de locataire souhaité."
    ),
    "ask.confirm_property": (
        "Voici ce que je retiens. Confirmez-vous pour publier l'annonce ?\n"
        "Répondez oui pour publier, ou indiquez ce qu'il faut corriger."
    ),
    # The listing exists but cannot be published without photos. This is the
    # last thing between a draft and a tenant seeing it, so it is asked plainly
    # rather than acknowledged.
    "ask.media": (
        "Envoyez maintenant les photos du logement, au moins une.\n"
        "Elles sont indispensables pour publier l'annonce."
    ),
    "confirm.published": (
        "Votre bien est publié. Il apparaîtra dans les recherches des locataires."
    ),
    # The tenant's side: what to look for, and what to do with what is found.
    "ask.search_type": (
        "Quel type de logement cherchez-vous ?\n"
        "Exemple : appartement, studio, maison, chambre."
    ),
    "ask.search_location": (
        "Où cherchez-vous ?\n"
        "Indiquez une ville ou un quartier, par exemple : Douala, Bastos."
    ),
    "ask.search_budget": "Quel est votre budget maximum de loyer mensuel, en FCFA ?",
    "ask.search_criteria": (
        "Quel critère voulez-vous modifier ?\n"
        "Par exemple : la ville, le quartier, le type de logement ou le budget."
    ),
    "ask.search_results": (
        "Je n'ai pas pu afficher les logements trouvés pour le moment.\n"
        "Réessayez dans un instant."
    ),
    "ask.search_choice": "Indiquez le numéro du logement qui vous intéresse.",
}

# The same sentences, for a conversation the model read as English. A parallel
# table rather than a nested one: an untranslated key must degrade to the
# clarification, never silently fall back to French mid-thread.
_QUESTION_TEXT_EN: dict[str, str] = {
    "ask.property_type": (
        "What type of property would you like to list?\n"
        "Example: apartment, studio, room, house, commercial space."
    ),
    "ask.standing": (
        "Is the property modern or not?\n"
        "Answer: modern, or not."
    ),
    "ask.location": (
        "In which city and neighbourhood is the property?\n"
        "Example: Douala, Bastos."
    ),
    "ask.rent": "What is the monthly rent, in FCFA?",
    "ask.features": (
        "How many bedrooms?\n"
        "You can also give the surface area in m2."
    ),
    "ask.charges": (
        "Are the monthly charges included in the rent?\n"
        "If not, what is their amount in FCFA?"
    ),
    "ask.deposit": (
        "What is the security deposit amount, in FCFA?\n"
        "It is the refundable sum asked from the tenant on move-in."
    ),
    "ask.minimum_duration": "What is the minimum rental duration you want?",
    "ask.availability": (
        "From when is the property available?\n"
        "Answer: immediately, or a date (example: 01/11/2026)."
    ),
    "ask.conditions": (
        "What are the move-in conditions?\n"
        "State for example the advance (number of months to pay upfront), the "
        "agency fees, and the type of tenant you want."
    ),
    "ask.confirm_property": (
        "Here is what I understood. Do you confirm publishing the listing?\n"
        "Answer yes to publish, or tell me what to correct."
    ),
    "ask.media": (
        "Please send the property photos now, at least one.\n"
        "They are required before the listing can be published."
    ),
    "confirm.published": (
        "Your property is published. It will appear in tenants' searches."
    ),
    "ask.search_type": (
        "What type of property are you looking for?\n"
        "Example: apartment, studio, house, room."
    ),
    "ask.search_location": (
        "Where are you looking?\n"
        "Give a city or a neighbourhood, for example: Douala, Bastos."
    ),
    "ask.search_budget": "What is your maximum monthly rent, in FCFA?",
    "ask.search_criteria": (
        "Which criterion would you like to change?\n"
        "For example: the city, the neighbourhood, the property type or the budget."
    ),
    "ask.search_results": (
        "I could not show the properties found just now.\n"
        "Please try again in a moment."
    ),
    "ask.search_choice": "Give the number of the property you are interested in.",
}

ACK_TEXT = (
    "C'est bien noté. Continuez quand vous voulez, je vous pose la question "
    "suivante dès que j'ai besoin d'une précision."
)

_ACK_TEXT_EN = (
    "Noted. Continue whenever you like, I will ask the next question as soon "
    "as I need a detail."
)

CLARIFY_TEXT = (
    "Je n'ai pas bien compris. Pouvez-vous reformuler ?\n"
    "Par exemple : « Je veux louer un appartement à Bastos » ou "
    "« Je cherche un studio à Douala à 150 000 FCFA »."
)

_CLARIFY_TEXT_EN = (
    "I did not quite understand. Could you rephrase?\n"
    'For example: "I want to rent an apartment in Bastos" or "I am looking '
    'for a studio in Douala at 150,000 FCFA".'
)

# Offering to schedule a viewing was a promise the platform cannot keep: Kora
# introduces a tenant to a landlord and stops there. Naming it here is how a
# user ends up waiting for a visit nobody will arrange.
SUPPORT_TEXT = (
    "Je peux vous aider à publier un logement ou à en trouver un. "
    "Dites-moi simplement ce dont vous avez besoin."
)

_SUPPORT_TEXT_EN = (
    "I can help you list a property or find one. Just tell me what you need."
)

# Intents where the platform understood the message but has no question to ask:
# answering with a generic "noted" would be a dead end, so these get an answer
# that actually moves the conversation.
NO_QUESTION_TEXT: dict[str, str] = {
    "SUPPORT": SUPPORT_TEXT,
}

_NO_QUESTION_TEXT_EN: dict[str, str] = {
    "SUPPORT": _SUPPORT_TEXT_EN,
}


def _is_english(language: str | None) -> bool:
    if language is None:
        return False
    return language.casefold().startswith("en")


def render_reply(
    *,
    reply_kind: str,
    question_key: str | None,
    intent: str | None = None,
    language: str | None = None,
) -> str:
    """The sentence to send, never ``None``: a silent thread is the failure.

    ``language`` is the one the conversation settled on; unknown values stay in
    French, the platform's default.
    """
    if _is_english(language):
        question, ack, clarify, no_question = (
            _QUESTION_TEXT_EN,
            _ACK_TEXT_EN,
            _CLARIFY_TEXT_EN,
            _NO_QUESTION_TEXT_EN,
        )
    else:
        question, ack, clarify, no_question = (
            QUESTION_TEXT,
            ACK_TEXT,
            CLARIFY_TEXT,
            NO_QUESTION_TEXT,
        )
    if reply_kind == "QUESTION":
        return question.get(question_key or "", clarify)
    if reply_kind == "ACK":
        if intent:
            return no_question.get(intent, ack)
        return ack
    return clarify
