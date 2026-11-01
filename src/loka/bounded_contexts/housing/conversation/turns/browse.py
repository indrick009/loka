"""A tenant asking to see what there is, while his search waits.

Observed live: « il y a quelque chose à Bafoussam ? » was answered with the
waiting sentence while a studio stood in the next district. Asking to see is
answered with the town's lodgings closest to his demand — five at most, sent by
the proposals presenter with their photos — never with the whole catalogue.
"""

from __future__ import annotations

import re
import unicodedata

_BROWSE = re.compile(
    r"\b(il ?y ?a|ya|y a|tu as|vous avez|t as)\b.{0,40}\b(quelque chose|qqch|q ?q ?ch|"
    r"rien|logements?|maisons?|studios?|chambres?|apparts?|appartements?)\b"
    r"|\b(montre|montrez|envoie|envoyez|liste|voir|vois)\b.{0,30}"
    r"\b(ce qu\w*|logements?|disponibles?|dispo|liste|tout)\b"
    r"|\b(do you have|show me|send me|wetin you get|una get)\b"
)

_SENDING = {
    "fr": (
        "Je vous envoie ce qu'il y a en ce moment à {city}, au plus proche de "
        "votre demande. Répondez avec le numéro du logement qui vous intéresse."
    ),
    "en": (
        "Here is what {city} has right now, closest to your request. Reply with "
        "the number of the one you like."
    ),
}
_NOTHING = {
    "fr": (
        "Pour le moment, je n'ai aucun logement à {city} qui s'approche de votre "
        "demande. Votre recherche reste enregistrée : dès qu'un logement "
        "correspond, je reviens vers vous ici."
    ),
    "en": (
        "Right now I have no lodging in {city} close to your request. Your "
        "search stays recorded: as soon as one matches, I will come back to you here."
    ),
}


def asks_to_browse(text: str | None) -> bool:
    if not text:
        return False
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return bool(_BROWSE.search(folded.casefold().replace("'", " ")))


def browse_text(count: int, city: str, language: str) -> str:
    copy = _SENDING if count else _NOTHING
    return copy.get(language, copy["fr"]).format(city=city)
