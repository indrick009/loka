"""The words that greet, agree and refuse."""

from __future__ import annotations

from typing import Any

AFFIRMATIVE = frozenset(
    {
        "oui",
        "ouais",
        "yes",
        "y",
        "ok",
        "okay",
        "daccord",
        "d accord",
        "ca me va",
        "exact",
        "parfait",
        "super",
        "merci",
        "ca marche",
        "bien",
        "valide",
        "confirme",
        "confirmation",
    }
)


NEGATIVE = frozenset(
    {
        "non",
        "no",
        "n",
        "jamais",
        "refuse",
        "refusee",
        "annule",
        "annulee",
        "je ne veux pas",
        "autre chose",
        "stop",
        "laisse tomber",
        "finalement non",
    }
)


# A bare greeting is the single most frequent message that carries no intent at
# all. When no question is open it can be answered from a template, so it must
# not spend a model call.
GREETINGS = frozenset(
    {
        "bonjour",
        "bonsoir",
        "salut",
        "hello",
        "hi",
        "allo",
        "coucou",
        "bonjour monsieur",
        "bonjour madame",
        "bonjour monsieur madame",
        "bjr",
        "slt",
    }
)


# "Non" said to a form question, in the shapes a landlord actually types. The
# bare NEGATIVE words are reused so a refusal and a zero stay one vocabulary.
_DENIALS = NEGATIVE | frozenset(
    {
        "aucun",
        "aucune",
        "rien",
        "nope",
        "pas du tout",
        "pas de caution",
        "pas de garantie",
        "pas d avance",
        "pas de gardien",
        "pas de parking",
        "il n y a pas",
        "non rien",
    }
)


# "Combien de logements identiques ?" answered without a digit.
_ONE_ONLY = frozenset(
    {
        "un",
        "une",
        "un seul",
        "une seule",
        "juste un",
        "juste une",
        "un seulement",
        "un seul logement",
        "juste un seul",
        "juste une seule",
        "seulement un",
        "seulement une",
        "un seul seulement",
        "one",
        "just one",
        "only one",
    }
)


# What a denial means for each question, where it means exactly one thing.
_NO_MEANS: dict[str, tuple[str, dict[str, Any]]] = {
    "ask.deposit": ("COLLECT_DEPOSIT", {"deposit_months": 0}),
    "ask.advance": ("COLLECT_ADVANCE", {"advance_months": 0}),
    "ask.security": (
        "COLLECT_SECURITY",
        {"amenities": {"security": False, "parking": False}},
    ),
    "ask.quantity": ("COLLECT_QUANTITY", {"quantity": 1}),
}


# What an affirmation means for each question, where it means exactly one thing.
# The counterpart of ``_NO_MEANS``: a bare "oui" is a dialogue act that carries
# no value, so on a question whose yes does have one it must be bound here rather
# than left to the model. Observed live: "oui" to "l'eau et l'électricité
# sont-elles incluses ?" was read as a bare AFFIRMATIVE, produced no step, and
# because the single-call writer still put the next question in its sentence the
# following "2" was filed as the charges instead of the caution. Only questions
# where "oui" names one thing belong here: "gardien ? parking ?" and "combien de
# mois ?" still go to the model, which can ask for the missing half.
_YES_MEANS: dict[str, tuple[str, dict[str, Any]]] = {}
