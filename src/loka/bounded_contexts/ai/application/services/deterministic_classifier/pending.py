"""A short answer bound to the question the platform actually asked."""

from __future__ import annotations

from loka.bounded_contexts.ai.application.services.deterministic_classifier.answer_readers import (
    _entry_months,
    _security_answer,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier.answer_rules import (
    _BILLABLE_ANSWER_FIELDS,
    _DATE_ANSWER_PATTERN,
    _DURATION_ANSWER_PATTERN,
    _IMMEDIATE_ANSWER_WORDS,
    _MAX_ANSWER_WORDS,
    _MAX_ENTRY_MONTHS,
    _MONTH_ANSWER_FIELDS,
    _MONTH_COUNT_PATTERN,
    _PUBLISH_ANSWER,
    _SEARCH_ANSWER,
    _UTILITIES_EXTRA,
    _UTILITIES_INCLUDED,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier.match import (
    DeterministicMatch,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier.text import (
    _find_amount,
    _find_digits,
    _parse_amount,
)
from loka.bounded_contexts.ai.application.services.deterministic_classifier.vocabulary import (
    _DENIALS,
    _NO_MEANS,
    _ONE_ONLY,
    _YES_MEANS,
    AFFIRMATIVE,
    NEGATIVE,
)
from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    MAX_RESULT_CHOICES,
    MIN_PLAUSIBLE_RENT_XAF,
)


def denial_answer(pending: str | None) -> DeterministicMatch | None:
    """What a "no" means as the answer to the open question, if one thing.

    For callers that already know the message is a refusal — the model
    said NEGATIVE — and only need the value it gives the question.
    """
    meaning = _NO_MEANS.get(pending or "")
    if meaning is None:
        return None
    intent, entities = meaning
    return DeterministicMatch(intent=intent, confidence=0.95, entities=dict(entities))


def _no_to_a_form_question(
    normalized: str, pending: str | None
) -> DeterministicMatch | None:
    """Read "non" as the value of the open question, not as a refusal.

    Observed live: a landlord answered the caution question with "non", the
    model read it as the dialogue act NEGATIVE, that is not a listing
    intent, so the turn produced no step and the same question came back.
    A landlord with no caution could never finish the form.

    Bound here rather than left to the model because the meaning is not a
    matter of interpretation: no caution is zero months, no guard and no
    parking is both absent, and "not several lodgings" is one. Only the
    unambiguous side is bound — a bare "oui" to "gardien ? parking ?" does
    not say which of the two, so that still goes to the model.
    """
    meaning = _NO_MEANS.get(pending or "")
    if meaning is None:
        return None
    if normalized not in _DENIALS and not (
        pending == "ask.quantity" and normalized in _ONE_ONLY
    ):
        return None
    intent, entities = meaning
    return DeterministicMatch(intent=intent, confidence=0.95, entities=dict(entities))


def _yes_to_a_form_question(
    normalized: str, pending: str | None
) -> DeterministicMatch | None:
    """Read "oui" as the value of the open question, where it means one thing.

    The counterpart of :meth:`_no_to_a_form_question`, for the questions
    whose "yes" is not ambiguous: "l'eau et l'électricité sont-elles
    incluses ?" answered "oui" means the rent covers them. Left to the model
    it was read as a bare dialogue act and produced no step, and because the
    single-call writer still named the next question in its sentence the
    "2" that answered the caution was filed as the charges. A question that
    names two things (gardien ? parking ?) or wants a count (combien de
    mois ?) is not bound here — its "oui" says neither, so it stays a model
    job that can ask for the missing half.
    """
    meaning = _YES_MEANS.get(pending or "")
    if meaning is None:
        return None
    if normalized not in AFFIRMATIVE:
        return None
    intent, entities = meaning
    return DeterministicMatch(intent=intent, confidence=0.95, entities=dict(entities))


def _standing_reply(
    normalized: str, pending: str | None
) -> DeterministicMatch | None:
    """Read "oui"/"non" as MODERN/NON_MODERN when that is the open question."""
    if pending != "ask.standing":
        return None
    if normalized in AFFIRMATIVE:
        return DeterministicMatch(
            intent="COLLECT_STANDING",
            confidence=0.95,
            entities={"standing": "MODERN"},
        )
    if normalized in NEGATIVE:
        return DeterministicMatch(
            intent="COLLECT_STANDING",
            confidence=0.95,
            entities={"standing": "NON_MODERN"},
        )
    return None


def pending_answer(normalized: str, pending: str | None
) -> DeterministicMatch | None:
    """Bind a short answer to the field the platform actually asked for.

    The model is told the same thing (see ``awaiting_field`` in the analysis
    prompt), but a value whose meaning depends entirely on the open question
    is a rule, not a guess: a bare "5" answered to the rent question is five
    francs and answered to the minimum-duration question is five months.
    Deciding it here keeps the number out of the wrong field and keeps the
    turn off the model entirely.
    """
    if not pending or not normalized:
        return None

    # Both read before the word cap: a landlord's real answer to either is
    # longer than four words, and neither carries anything a model adds.
    if pending == "ask.advance":
        entry = _entry_months(normalized)
        if entry is not None:
            return DeterministicMatch(intent="COLLECT_ADVANCE", confidence=0.95, entities=entry)
    if pending == "ask.security":
        security = _security_answer(normalized)
        if security is not None:
            return DeterministicMatch(
                intent="COLLECT_SECURITY",
                confidence=0.95,
                entities={"amenities": security},
            )
    if pending == "ask.charges" and _find_digits(normalized) is None:
        if _UTILITIES_INCLUDED.search(normalized):
            return DeterministicMatch(
                intent="COLLECT_CHARGES",
                confidence=0.95,
                entities={"charging_policy": "INCLUDED", "charges": 0},
            )
        if _UTILITIES_EXTRA.search(normalized):
            return DeterministicMatch(
                intent="COLLECT_CHARGES",
                confidence=0.95,
                entities={"charging_policy": "EXTRA", "charges": 0},
            )

    if pending == "ask.publish_or_search":
        # Read before the word cap, and before anything else: this is the
        # one question that gets a user out of a conversation that never
        # opened, and "je veux publier mon studio" is longer than a short
        # answer while being as unambiguous as they come.
        if _PUBLISH_ANSWER.search(normalized):
            return DeterministicMatch(
                intent="CREATE_PROPERTY", confidence=0.95, entities={}
            )
        if _SEARCH_ANSWER.search(normalized):
            return DeterministicMatch(
                intent="PROPERTY_SEARCH", confidence=0.95, entities={}
            )
        return None

    if len(normalized.split()) > _MAX_ANSWER_WORDS:
        return None

    billable = _BILLABLE_ANSWER_FIELDS.get(pending)
    if billable is not None:
        raw, multiplier = _find_amount(normalized)
        if raw is None:
            return None
        value = _parse_amount(raw) * multiplier
        if value < MIN_PLAUSIBLE_RENT_XAF:
            return None
        intent, field = billable
        entities: dict[str, object] = {field: value}
        if field == "charges":
            # An amount given for the charges question is money asked on top
            # of the rent; "incluses" stays a model job because it carries no
            # number to bind.
            entities["charging_policy"] = "EXTRA"
        return DeterministicMatch(intent=intent, confidence=0.95, entities=entities)

    months_field = _MONTH_ANSWER_FIELDS.get(pending)
    if months_field is not None:
        match = _MONTH_COUNT_PATTERN.match(normalized)
        if match is None:
            return None
        months = int(match.group("count"))
        if months > _MAX_ENTRY_MONTHS:
            # Past two years it is a franc amount answered to a months
            # question. Handing it back to the model is how the landlord
            # gets asked again instead of having a typo stored.
            return None
        intent, field = months_field
        return DeterministicMatch(
            intent=intent, confidence=0.95, entities={field: months}
        )

    if pending == "ask.search_choice":
        match = _MONTH_COUNT_PATTERN.match(normalized)
        if match is not None:
            choice = int(match.group("count"))
            if 1 <= choice <= MAX_RESULT_CHOICES:
                return DeterministicMatch(
                    intent="COLLECT_PROPERTY_CHOICE",
                    confidence=0.95,
                    entities={"property_choice": choice},
                )
        return None

    if pending == "ask.quantity":
        match = _MONTH_COUNT_PATTERN.match(normalized)
        if match is not None:
            quantity = int(match.group("count"))
            if 1 <= quantity <= 50:
                return DeterministicMatch(
                    intent="COLLECT_QUANTITY",
                    confidence=0.95,
                    entities={"quantity": quantity},
                )
        return None

    if pending == "ask.minimum_duration":
        match = _DURATION_ANSWER_PATTERN.match(normalized)
        if match:
            months = int(match.group("count"))
            if months > 0:
                return DeterministicMatch(
                    intent="COLLECT_MINIMUM_DURATION",
                    confidence=0.95,
                    entities={"minimum_duration_months": months},
                )
        return None

    if pending == "ask.availability":
        if any(word in normalized for word in _IMMEDIATE_ANSWER_WORDS):
            return DeterministicMatch(
                intent="COLLECT_AVAILABILITY",
                confidence=0.95,
                entities={"availability": "IMMEDIATE"},
            )
        date_match = _DATE_ANSWER_PATTERN.search(normalized)
        if date_match:
            return DeterministicMatch(
                intent="COLLECT_AVAILABILITY",
                confidence=0.95,
                entities={"availability": date_match.group(0)},
            )
    return None
