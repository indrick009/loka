"""Requests close to what idia does, which idia does not offer yet.

« un studio meublé », « une chambre pour une nuit », « acheter un terrain »: a
model asked these answers something — a rent question again, a promise. The
user deserves better: a person looks at it. The request is handed to the
admin (``WHATSAPP__ALERT_PHONES``), the user reads that idia checks with the
team, and the conversation waits for the admin's decision (see
``interfaces/worker/stuck_guard``).

The list is short on purpose and grows with what users actually ask; a
request it misses can still be handed over by the agent (its ``ask_team``
tool).
"""

from __future__ import annotations

import re

from loka.bounded_contexts.conversation.application.services.privacy import fold

_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("logement meublé", re.compile(r"\b(meuble|meublee|meubles|meublees|furnished)\b")),
    (
        "location courte durée (nuitée)",
        re.compile(
            r"\b(nuitee|nuitees|courte duree|short stay|airbnb|per night|"
            r"(?:pour|par|a la) (?:une |la )?nuit)\b"
        ),
    ),
    (
        "achat ou vente de bien immobilier",
        re.compile(
            r"\b(acheter|achat|vendre|vente|buy|sell)\b.{0,40}\b(terrain|parcelle|maison|"
            r"villa|appartement|immeuble|land|house)\b"
        ),
    ),
    ("location de véhicule", re.compile(r"\blocation (?:de |d')?(voiture|vehicule|car)\b")),
    # « Je voudrais parler à un responsable » got « je suis une assistante IA »
    # (journal of 25 Nov 2026, 15:52): a person answers.
    (
        "parler à un responsable",
        re.compile(
            r"\b(parler|discuter|joindre|contacter|echanger)\b.{0,30}\b(responsable|humain|"
            r"agent|conseiller|equipe|manager|patron|administrat\w*|vraie personne|"
            r"personne reelle)\b|\b(speak|talk) to (a |the )?(human|person|manager|agent)\b"
        ),
    ),
)


def unsupported_topic(text: str) -> str | None:
    """The topic idia does not offer yet that this message asks for, if any."""
    folded = fold(text)
    return next((topic for topic, pattern in _TOPICS if pattern.search(folded)), None)
