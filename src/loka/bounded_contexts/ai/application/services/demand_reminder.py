"""The month-on question — "tu cherches toujours ?" — and how his answer reads.

Read by a rule, like the publication's confirmation: the answer ends or renews
a search, and a model misreading "non merci j'ai trouvé" as a yes would send
him lodgings for another month. Anything the rule cannot settle — "oui mais à
Bastos maintenant" — goes on to the ordinary pipeline, which reads it as the
new criteria it is.
"""

from __future__ import annotations

import re

STILL_SEARCHING_KEY = "ask.still_searching"

_YES_WORDS = frozenset({"oui", "ok", "okay", "toujours", "yes", "yep", "still", "dac"})
_NO_WORDS = frozenset({"non", "no", "nope", "stop", "arrête", "arrete"})
_NO_PHRASES = ("plus besoin", "j'ai trouvé", "j ai trouvé", "j'ai deja", "j'ai déjà",
               "déjà trouvé", "deja trouvé", "found")
# Words that turn a yes into a new search, left to the pipeline to read.
_MORE = ("mais", "but", "maintenant", "plutôt", "plutot", "change", "autre")

_TEXT: dict[str, dict[str, str]] = {
    "fr": {
        "ask": (
            "Bonjour ! Il y a un mois, vous cherchiez un logement avec Kora. "
            "Cherchez-vous toujours ? Répondez OUI pour que je continue à vous "
            "envoyer les logements qui correspondent, ou NON pour arrêter."
        ),
        "renewed": (
            "C'est noté, je continue à chercher. Dès qu'un logement correspond, "
            "je vous l'envoie ici."
        ),
        "stopped": (
            "D'accord, j'arrête la recherche. Écrivez-moi quand vous voulez en "
            "lancer une nouvelle."
        ),
    },
    "en": {
        "ask": (
            "Hello! A month ago you were looking for a place with Kora. Are you "
            "still looking? Reply YES and I will keep sending you the places "
            "that match, or NO to stop."
        ),
        "renewed": (
            "Noted, I keep looking. As soon as a place matches, I will send it "
            "to you here."
        ),
        "stopped": "All right, I have stopped the search. Write to me whenever you want a new one.",
    },
}


def reminder_text(kind: str, language: str) -> str:
    """``ask``, ``renewed`` or ``stopped``, in the thread's language."""
    return _TEXT.get(language, _TEXT["fr"])[kind]


def still_searching(text: str | None) -> bool | None:
    """``True`` for a plain yes, ``False`` for a no, ``None`` for anything else."""
    stripped = (text or "").strip().casefold()
    words = re.findall(r"[\w']+", stripped)
    if not words:
        return None
    if words[0] in _NO_WORDS or any(phrase in stripped for phrase in _NO_PHRASES):
        return False
    if (words[0] in _YES_WORDS or stripped.startswith("je cherche toujours")) and not any(
        word in words for word in _MORE
    ):
        return True
    return None
