"""Rules that only mean something while a housing question is open."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.services.deterministic_classifier import (
    DeterministicClassifier,
    DeterministicMatch,
)
from loka.bounded_contexts.conversation.application.services.negotiation_copy import (
    OFFER_QUESTION_KEY,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.housing.keys import (  # noqa: E501
    AVAILABILITY_QUESTION_KEY,
    CONFIRM_QUESTION_KEY,
    is_availability_answer,
)

# How a question opens, when it does not end with "?" — WhatsApp users often
# leave it off. Kept short: a false "question" only re-shows the recap.
_QUESTION_OPENINGS = (
    "est-ce", "pourquoi", "comment", "combien", "quand", "quel", "quelle",
    "c'est quoi", "tu as", "as-tu", "what", "why", "how", "where", "when", "did ",
)

_AFFIRMATIONS = ("oui", "ok", "okay", "d'accord", "dac", "c'est bon", "vas y", "vas-y",
                 "publie", "go", "yes", "yep", "valide", "confirme", "exact", "parfait")
_HESITATIONS = ("non", "pas ", "attends", "attend ", "corrige", "change", "modifie",
                "erreur", "trompé", "trompe", "mais ")


def is_a_question(text: str | None) -> bool:
    """Whether a message asks something rather than answering.

    Observed live: "Tu as retenu la localisation ?", sent at the summary, was
    read as AFFIRMATIVE and the listing was registered — nobody had said yes.
    A confirmation publishes a listing; a question is never one.
    """
    stripped = (text or "").strip().casefold()
    return stripped.endswith("?") or stripped.startswith(_QUESTION_OPENINGS)


def confirms_the_summary(text: str | None) -> bool:
    """A plain yes to "confirmez-vous la publication ?", with no "but" in it."""
    stripped = (text or "").strip().casefold()
    if not stripped or is_a_question(stripped):
        return False
    if any(word in stripped for word in _HESITATIONS):
        return False
    return stripped.startswith(_AFFIRMATIONS)


def availability_reading(
    pending: str | None,
    hint: DeterministicMatch | None,
    intent: str,
    entities: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """His answer to "le logement est encore disponible ?", when that is open.

    A rule, not a guess. Observed live: "Oui, il est toujours disponible" came
    back AFFIRMATIVE, was read as a reply to his photo question, and the tenant
    waiting on it was never released. The rule had it right; the model did not
    know the question was open.

    A bare "oui" is a yes to the question asked. A bare "non" is left to the
    model: taken for good or for now are different answers, and only one of
    them takes the listing out of search.
    """
    if pending not in (AVAILABILITY_QUESTION_KEY, OFFER_QUESTION_KEY):
        return intent, entities
    if hint is not None and is_availability_answer(hint.intent):
        return hint.intent, {**entities, **hint.entities}
    if intent == "AFFIRMATIVE":
        return "AVAILABILITY_STILL_AVAILABLE", entities
    return intent, entities


def read_open_question(
    classifier: DeterministicClassifier,
    *,
    pending_key: str | None,
    hint: DeterministicMatch | None,
    intent: str,
    entities: dict[str, Any],
    text: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Apply the rules that only mean something while a question is open.

    Both lanes end here — the fast regex lane and the model lane — so a bare
    "oui" answers "le logement est encore disponible ?" whichever one read
    it. Observed live: the regex lane returned AFFIRMATIVE for a landlord's
    "oui", skipped this reading, and the tenant waiting on his answer stayed
    blocked until the landlord rephrased as "oui c'est disponible".

    A "non" the model read as the bare dialogue act still answers the open
    question when that question has exactly one "no": no caution, neither
    guard nor parking, a single lodging. Observed live: "Non, ni gardien ni
    parking" came back NEGATIVE, nothing was recorded, and the landlord was
    asked about "other equipment" until he gave up.
    """
    denial = classifier.denial_answer(pending_key) if intent == "NEGATIVE" else None
    if denial is not None:
        intent, entities = denial.intent, {**denial.entities, **entities}
    if pending_key == CONFIRM_QUESTION_KEY and confirms_the_summary(text):
        # The most consequential answer in the flow is read by a rule, not
        # left to a label. Observed live: "oui c'est bon publie" came back
        # CREATE_PROPERTY, the listing was never registered, and the
        # landlord was shown his summary again.
        intent = "AFFIRMATIVE"
    return availability_reading(pending_key, hint, intent, entities)
