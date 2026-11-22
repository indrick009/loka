"""Which language this one message is in, for the desks that answer before the model.

The model lanes set the session's language from each reply. The desks
(privacy, complaints) answer before any model reads the message, so they
answered in the language of the previous turn: « je veux faire une
réclamation » after « hi, I'm looking for a room » got its reply in English
(test of 22 Nov 2026). A message that says nothing either way — « 5 »,
« OUI » — keeps the conversation's language.
"""

from __future__ import annotations

import re

from loka.bounded_contexts.conversation.application.services.privacy import fold

_FRENCH = frozenset(
    [
        "je", "j", "tu", "il", "elle", "nous", "vous", "mes", "mon", "ma", "le",
        "la", "les", "des", "une", "un", "du", "au", "aux", "et", "est", "pas",
        "veux", "voudrais", "faire", "supprimer", "donnees", "reclamation",
        "confidentialite", "bonjour", "salut", "merci", "svp", "stp", "plainte",
        "cherche", "aide"
    ]
)
_ENGLISH = frozenset(
    [
        "i", "my", "me", "the", "a", "an", "and", "is", "not", "want", "would",
        "like", "delete", "data", "privacy", "policy", "complaint", "report",
        "hello", "hi", "thanks", "thank", "please", "looking", "help"
    ]
)
_ACCENTS = re.compile(r"[éèêëàâçùûôîï]")


def language_of(text: str | None, fallback: str) -> str:
    raw = text or ""
    words = re.findall(r"[a-z]+", fold(raw))
    french = sum(word in _FRENCH for word in words) + (2 if _ACCENTS.search(raw.lower()) else 0)
    english = sum(word in _ENGLISH for word in words)
    if french > english:
        return "fr"
    if english > french:
        return "en"
    return fallback
