"""Every sentence Found writes itself. None names a document's owner."""

from __future__ import annotations


def _fr(language: str) -> bool:
    return not language.startswith("en")


PROOF_KINDS_FR = (
    "Envoie tes preuves de propriété : une photocopie ou une ancienne photo du "
    "document, l'attestation de perte du commissariat ou de la gendarmerie, le "
    "récépissé de demande, un autre document à ton nom, ou une information qui "
    "figure sur le document et que seul son propriétaire connaît. Tu peux écrire "
    "ou envoyer des photos."
)
PROOF_KINDS_EN = (
    "Send your proof of ownership: a copy or old photo of the document, the "
    "police loss certificate, the application receipt, another document in your "
    "name, or information on the document only its owner knows. Text or photos."
)


def declared(language: str) -> str:
    if _fr(language):
        return (
            "Merci beaucoup ! Ta déclaration est enregistrée. Si son propriétaire le "
            "cherche, il devra d'abord prouver qu'il est à lui, et l'équipe Kora "
            "vérifiera avant toute mise en relation. La restitution se fera dans un "
            "lieu public ou au commissariat, et aucun argent n'est dû entre vous. "
            "Tu peux envoyer d'autres photos si tu veux."
        )
    return (
        "Thank you! Your declaration is recorded. Its owner will have to prove it is "
        "theirs, and the Kora team checks before anyone is put in touch. The "
        "handover happens in a public place or at a police station, and no money is "
        "owed between you."
    )


def photo_added(language: str) -> str:
    return "Photo ajoutée, merci." if _fr(language) else "Photo added, thank you."


def possible_match(language: str) -> str:
    if _fr(language):
        return f"Une correspondance possible existe. Pour la vérifier :\n{PROOF_KINDS_FR}"
    return f"A possible match exists. To check it:\n{PROOF_KINDS_EN}"


def possible_match_later(language: str) -> str:
    if _fr(language):
        return (
            "Bonne nouvelle : un document qui pourrait correspondre à ta déclaration "
            f"de perte vient d'être signalé.\n{PROOF_KINDS_FR}"
        )
    return f"Good news: a document that might match your loss was just reported.\n{PROOF_KINDS_EN}"


def nothing_yet(language: str) -> str:
    if _fr(language):
        return (
            "Ta recherche est enregistrée. Aucun document correspondant n'a été "
            "déclaré pour l'instant : je te préviens dès qu'il y en a un."
        )
    return "Your search is recorded. Nothing matching has been declared yet; I will tell you."


def proof_received(language: str) -> str:
    if _fr(language):
        return (
            "Preuve reçue, merci. Une personne de l'équipe Kora va la vérifier ; je "
            "te préviens dès que c'est fait. Tu peux ajouter d'autres preuves."
        )
    return "Proof received. A person from the Kora team will check it; I will let you know."


def too_many_searches(language: str) -> str:
    if _fr(language):
        return "Tu as atteint le nombre de recherches autorisées pour aujourd'hui. Réessaie demain."
    return "You have reached today's number of searches. Please try again tomorrow."


def validated(language: str) -> str:
    if _fr(language):
        return (
            "Bonne nouvelle : l'équipe Kora a validé que ce document est bien à toi. "
            "La mise en relation avec la personne qui l'a trouvé coûte 1 000 FCFA, "
            "seuls frais à payer : réponds PAYER (Mobile Money sur ce numéro), ou "
            "envoie le numéro à utiliser. "
            "La restitution se fera dans un lieu public ou au commissariat, et rien "
            "d'autre ne peut t'être demandé — signale-nous toute demande d'argent."
        )
    return (
        "Good news: the Kora team validated that this document is yours. Being put "
        "in touch with the finder costs 1,000 FCFA, the only fee; payment opens very "
        "soon. The handover happens in a public place or a police station."
    )


def refused(language: str) -> str:
    if _fr(language):
        return (
            "L'équipe Kora n'a pas pu confirmer que ce document t'appartient. Si tu as "
            "d'autres preuves, tu peux les envoyer ici."
        )
    return (
        "The Kora team could not confirm this document is yours. "
        "Send other proof if you have it."
    )


def unlock_sent(phone: str) -> str:
    return (
        f"J'ai envoyé la demande de paiement de 1 000 FCFA au {phone}. Valide-la sur ton "
        "téléphone : je te mets en relation dès que c'est fait."
    )


def pay_or_not() -> str:
    return "Réponds PAYER pour être mis en relation (1 000 FCFA), ou NON."


def unlock_later() -> str:
    return "D'accord. Réponds PAYER quand tu veux être mis en relation."


def unlock_failed() -> str:
    return "Le paiement n'a pas pu être lancé. Envoie un autre numéro Mobile Money."
