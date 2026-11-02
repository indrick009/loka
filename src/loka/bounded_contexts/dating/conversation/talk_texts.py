"""What Kora writes while two members ask each other questions through it."""

from __future__ import annotations

from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.talk import MESSAGES_PER_PACK, PHOTOS_PER_PACK

_DECIDE = "Quand tu es décidé(e), écris CONFIRMER si {name} te plaît, ou STOP pour arrêter."


def _fr(language: str) -> bool:
    return not language.startswith("en")


def opened(other: Profile, language: str) -> str:
    if _fr(language):
        return (
            f"Bonne nouvelle : {other.first_name} a aussi dit oui ! Avant de confirmer, "
            "vous pouvez vous poser des questions et échanger des photos à travers moi : "
            "écris ici, je transmets. Vos numéros restent cachés.\n"
            f"Tu as {MESSAGES_PER_PACK} messages et {PHOTOS_PER_PACK} photos.\n"
            + _DECIDE.format(name=other.first_name)
        )
    return (
        f"Good news: {other.first_name} said yes too! Before confirming, you can ask "
        "each other questions and share photos through me: write here, I pass it on. "
        "Your numbers stay hidden.\n"
        f"You have {MESSAGES_PER_PACK} messages and {PHOTOS_PER_PACK} photos.\n"
        f"When you have decided, write CONFIRM if you like {other.first_name}, or STOP."
    )


def waiting(other: Profile) -> str:
    return f"{other.first_name} t'a aussi dit oui ! Je vous mets en relation très bientôt."


def relayed(sender: Profile, text: str) -> str:
    return f"{sender.first_name} : « {text} »"


def photo_caption(sender: Profile) -> str:
    return f"Photo de {sender.first_name}"


def sent(other: Profile, left: int) -> str:
    return f"✓ Transmis à {other.first_name}. Il te reste {left} message(s)."


def photo_sent(other: Profile, left: int) -> str:
    return f"✓ Photo transmise à {other.first_name}. Il te reste {left} photo(s)."


def contact_refused() -> str:
    return (
        "Je ne transmets ni numéro, ni compte, ni lien : vos contacts se débloquent "
        "quand vous avez confirmé tous les deux. Reformule ton message."
    )


def inappropriate() -> str:
    return "Ce message ne peut pas être transmis. Reste respectueux(se), s'il te plaît."


def only_text_and_photos() -> str:
    return "Je ne transmets que les messages écrits et les photos."


def used_up(other: Profile, what: str) -> str:
    return (
        f"Tu as utilisé tes {what}. Écris CONFIRMER si {other.first_name} te plaît, "
        f"STOP pour arrêter, ou PLUS pour {MESSAGES_PER_PACK} messages et "
        f"{PHOTOS_PER_PACK} photos de plus (1 000 FCFA)."
    )


def confirmed(other: Profile) -> str:
    return (
        f"C'est noté, tu as confirmé. J'attends la réponse de {other.first_name} ; "
        "vous pouvez encore échanger en attendant."
    )


def they_confirmed(sender: Profile) -> str:
    return (
        f"{sender.first_name} a confirmé : tu lui plais ! Écris CONFIRMER si c'est "
        "réciproque, ou STOP."
    )


def stopped() -> str:
    return "D'accord, j'arrête cette discussion."


def they_stopped(sender: Profile) -> str:
    return (
        f"{sender.first_name} a préféré ne pas aller plus loin. Ne t'en fais pas : "
        "je continue à te chercher la bonne personne."
    )


def plus_offer(other: Profile) -> str:
    return (
        f"Pour 1 000 FCFA, tu as {MESSAGES_PER_PACK} messages et {PHOTOS_PER_PACK} "
        f"photos de plus avec {other.first_name}. Réponds PAYER pour payer avec ce "
        "numéro, envoie le numéro Mobile Money à utiliser, ou NON."
    )


def plus_requested(phone: str) -> str:
    return (
        f"J'ai envoyé la demande de 1 000 FCFA au {phone}. Valide-la sur ton téléphone : "
        "tes messages en plus arrivent dès que c'est fait. En attendant, tu peux "
        "écrire CONFIRMER ou STOP."
    )


def plus_declined(other: Profile) -> str:
    return "D'accord. " + _DECIDE.format(name=other.first_name)


def plus_unavailable(other: Profile) -> str:
    return "Le paiement n'est pas disponible pour le moment. " + _DECIDE.format(
        name=other.first_name
    )
