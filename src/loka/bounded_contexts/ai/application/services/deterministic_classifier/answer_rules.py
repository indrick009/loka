"""How a short answer is read, depending on the question it answers."""

from __future__ import annotations

import re

# A short answer means whatever the open question asked for. A bare "5" is five
# *months* under the minimum-duration question, five *francs* under the rent
# one; "immédiat" answers availability. Deciding this from the message alone is
# impossible, which is why the pending question — not the model's prior — binds
# the value to its field.
_BILLABLE_ANSWER_FIELDS: dict[str, tuple[str, str]] = {
    "ask.rent": ("COLLECT_PRICE", "price"),
    "ask.search_budget": ("COLLECT_PRICE", "price"),
    "ask.charges": ("COLLECT_CHARGES", "charges"),
}


# The caution and the advance are counted in months of rent, so a bare "2"
# under either is two months — not two francs, and not a number too small to be
# a rent. Separate from the duration binder because zero is a real answer here:
# "aucune caution" is ordinary, while a lease of zero months is nonsense.
# Which side of the market the user is on, answered in his own words. Kept
# generous: this is the question that unsticks a conversation, and a user who
# has already been misread once must not be misread again.
_PUBLISH_ANSWER = re.compile(
    r"\b(publier|publie|poster|mettre\s+en\s+(?:ligne|location)|louer\s+mon|"
    r"j\s*'?\s*ai\s+un\s+(?:logement|studio|appartement|appart|chambre|maison)|"
    r"je\s+suis\s+(?:le\s+)?(?:bailleur|propri[ée]taire)|bailleur|propri[ée]taire|"
    r"proposer|offrir|list|publish|landlord|owner)\b",
    re.I,
)


_SEARCH_ANSWER = re.compile(
    r"\b(chercher|cherche|recherche|trouver|je\s+veux\s+louer|louer\s+un|"
    r"je\s+suis\s+(?:le\s+)?locataire|locataire|visiter|"
    r"search|look|find|tenant|rent\s+a)\b",
    re.I,
)


# "Pour l'eau et l'électricité, comment ça se passe ?" is answered in local
# terms. A meter of his own or a shared one means he pays on top; "compris"
# means it is in the rent. A bare yes or no answers nothing here.
_UTILITIES_INCLUDED = re.compile(r"\b(compris|inclus|incluse|included)\b")


_UTILITIES_EXTRA = re.compile(
    r"\b(compteurs?|pr[ée]pay[ée]s?|forage|camwater|eneo|partag[ée]s?|"
    r"à part|a part|chacun|meters?|borehole|prepaid)\b"
)


# "6 mois, pas de caution", "6 mois avec 2 mois de caution", "caution 1 mois
# et 6 mois d'avance": the advance and the caution in one breath.
_NO_CAUTION = re.compile(r"\b(pas de|sans|aucune|0|zéro|zero)\s+caution\b|\bno caution\b")


_CAUTION_MONTHS = re.compile(
    r"(?P<a>\d{1,2})\s*(?:mois)?\s*(?:de\s+)?caution|caution\s*(?:de\s+|:\s*)?(?P<b>\d{1,2})"
)


_ANY_MONTHS = re.compile(r"(?<!\d)(\d{1,2})(?!\d)")


_MONTH_ANSWER_FIELDS: dict[str, tuple[str, str]] = {
    "ask.deposit": ("COLLECT_DEPOSIT", "deposit_months"),
    "ask.advance": ("COLLECT_ADVANCE", "advance_months"),
    "ask.search_advance": ("COLLECT_ADVANCE", "advance_months"),
}


# Questions whose answer is counted in something other than francs. A bare
# number given to one of these is months, a count or a date — never a price —
# so if it does not parse in that unit it must not be bound at all.
_UNIT_BOUND_QUESTIONS = frozenset(
    {
        "ask.deposit",
        "ask.advance",
        "ask.search_advance",
        "ask.quantity",
        "ask.minimum_duration",
        "ask.availability",
        "ask.search_choice",
    }
)


# A number on its own, with or without a money suffix: "2", "250 000", "60k".
_BARE_AMOUNT_PATTERN = re.compile(
    r"^\s*\d[\d\s.,]*\s*(?:k|m|mil|mille|f|fr|frs|fcfa|xaf)?\s*$", re.IGNORECASE
)


# "2", "2 mois", "02". Zero is allowed; the caller decides whether it means
# anything for the field it is binding to.
_MONTH_COUNT_PATTERN = re.compile(
    r"^\s*(?P<count>\d{1,3})\s*(?:mois|month[s]?)?\s*$", re.IGNORECASE
)


# The heaviest entry the business spec cites is a year of advance; RentMonths
# refuses past two years, and this stops short of it for the same reason.
_MAX_ENTRY_MONTHS = 24


_DURATION_ANSWER_PATTERN = re.compile(
    r"^\s*(?P<count>\d{1,2})\s*(?:mois|month[s]?)?\s*$", re.IGNORECASE
)


_IMMEDIATE_ANSWER_WORDS = (
    "immediat",
    "immédiat",
    "maintenant",
    "tout de suite",
    "disponible",
    "dispo",
    "asap",
    "des que possible",
    "dès que possible",
)


_DATE_ANSWER_PATTERN = re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b")


# Only a short message is a bare answer: a full sentence ("je veux un appart à
# 50 000") carries facts of its own and must still be read by the model.
_MAX_ANSWER_WORDS = 4
