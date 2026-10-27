"""Read a whole message: the full rule set, the fast lane, local amounts."""

from __future__ import annotations

from loka.bounded_contexts.conversation.domain.services.ai_provider import IntentResult
from loka.bounded_contexts.housing.conversation.facts import (
    MIN_PLAUSIBLE_RENT_XAF,
)
from loka.bounded_contexts.housing.conversation.rules.answer_rules import (
    _BARE_AMOUNT_PATTERN,
    _UNIT_BOUND_QUESTIONS,
)
from loka.bounded_contexts.housing.conversation.rules.match import (
    DeterministicMatch,
)
from loka.bounded_contexts.housing.conversation.rules.patterns import (
    _ACQUISITION_QUESTION,
    _LANDLORD_OWNERSHIP_PATTERN,
    ACQUISITION_SOURCE_PATTERNS,
    AVAILABILITY_PATTERNS,
    LISTING_INTENT_PATTERN,
    PROPERTY_TYPE_PATTERNS,
    SEARCH_INTENT_PATTERN,
    SHORTHAND_MILLION_PATTERN,
    SHORTHAND_THOUSAND_PATTERN,
)
from loka.bounded_contexts.housing.conversation.rules.pending import (
    _no_to_a_form_question,
    _standing_reply,
    _yes_to_a_form_question,
    pending_answer,
)
from loka.bounded_contexts.housing.conversation.rules.text import (
    _extract_entities,
    _first_match,
    _normalize,
    _parse_amount,
)
from loka.bounded_contexts.housing.conversation.rules.vocabulary import (
    _NO_MEANS,
    AFFIRMATIVE,
    GREETINGS,
    NEGATIVE,
)


def classify(text: str, *, pending: str | None = None) -> DeterministicMatch | None:
    normalized = _normalize(text)
    if not normalized:
        return None

    answer = pending_answer(normalized, pending)
    if answer is not None:
        return answer
    if pending in _UNIT_BOUND_QUESTIONS and _BARE_AMOUNT_PATTERN.match(normalized):
        # The open question fixes the unit of a bare answer, and this one
        # did not parse in that unit. Falling through to the generic rules
        # is how "250000" answered to the caution question was stored as
        # the rent: a number that failed the question's own unit must reach
        # the model, which will ask again, rather than be bound to whatever
        # field a pattern happens to match.
        return None

    availability = _first_match(AVAILABILITY_PATTERNS, normalized)
    if availability:
        return DeterministicMatch(
            intent=f"AVAILABILITY_{availability}",
            confidence=0.95,
            entities={"availability": availability},
        )

    # Only an answer to "comment avez-vous connu Kora ?". Read unprompted,
    # "mon frère" — how half of Douala addresses anyone — became "heard of
    # us through family", and "Bonsoir mon frère, je cherche un studio
    # moderne à Douala" lost the studio, the standing and the city.
    source = (
        _first_match(ACQUISITION_SOURCE_PATTERNS, normalized)
        if pending == _ACQUISITION_QUESTION
        else None
    )
    if source:
        return DeterministicMatch(
            intent="COLLECT_ACQUISITION_SOURCE",
            confidence=0.93,
            entities={"acquisition_source": source},
        )

    entities = _extract_entities(normalized)
    property_type = _first_match(PROPERTY_TYPE_PATTERNS, normalized)
    if property_type:
        entities["property_type"] = property_type

    wants_to_list = bool(
        LISTING_INTENT_PATTERN.search(normalized)
        or _LANDLORD_OWNERSHIP_PATTERN.search(normalized)
    )
    wants_to_search = bool(SEARCH_INTENT_PATTERN.search(normalized))

    # A tenant asking for a flat is never asking to publish one: the two
    # intents open different flows and confusing them would send a tenant
    # through a landlord's form. The possessive wins when both match
    # ("je veux louer mon studio" is a landlord), so listing is tested here
    # only when the message claims ownership or uses a publishing verb.
    if wants_to_search and not wants_to_list:
        return DeterministicMatch(
            intent="PROPERTY_SEARCH", confidence=0.85, entities=entities
        )
    if property_type and (wants_to_list or "price" in entities or "bedrooms" in entities):
        return DeterministicMatch(
            intent="CREATE_PROPERTY", confidence=0.9, entities=entities
        )
    if property_type:
        return DeterministicMatch(
            intent="COLLECT_PROPERTY_TYPE", confidence=0.9, entities=entities
        )
    if "price" in entities:
        return DeterministicMatch(intent="COLLECT_PRICE", confidence=0.92, entities=entities)
    if {"bedrooms", "bathrooms", "surface_area"} & entities.keys():
        return DeterministicMatch(
            intent="COLLECT_FEATURES", confidence=0.9, entities=entities
        )

    standing = _standing_reply(normalized, pending)
    if standing is not None:
        return standing
    denial = _no_to_a_form_question(normalized, pending)
    if denial is not None:
        return denial
    if "standing" in entities:
        return DeterministicMatch(
            intent="COLLECT_STANDING", confidence=0.9, entities=entities
        )

    # A place name with nothing else is a location answer, not noise: the
    # open location question is what it closes. Without this the rule set
    # stays silent on "Bastos" and a deployment without a model would ask
    # the same question for ever.
    if {"city", "neighbourhood", "location_hint"} & entities.keys():
        return DeterministicMatch(
            intent="COLLECT_LOCATION", confidence=0.9, entities=entities
        )

    if normalized in AFFIRMATIVE:
        yes = _yes_to_a_form_question(normalized, pending)
        if yes is not None:
            return yes
        if pending in _NO_MEANS:
            # Same reason as in the fast lane: a bare "oui" to a question
            # that wants a number or names two things says neither.
            return None
        return DeterministicMatch(intent="AFFIRMATIVE", confidence=0.98)
    if normalized in NEGATIVE:
        return DeterministicMatch(intent="NEGATIVE", confidence=0.98)
    if normalized in GREETINGS:
        return DeterministicMatch(intent="SUPPORT", confidence=0.9)

    return None


def shortcut(text: str, *, pending: str | None = None
) -> DeterministicMatch | None:
    """The unambiguous replies that must never cost a model call.

    Strictly narrower than :meth:`classify`: when a model is available the
    rules are only a fast lane, not the primary reader. An exact "oui"/"non"
    and a message whose only content is a price are the cases where the
    model can add nothing; "je cherche un appartement à Bastos" is not,
    because the intent (search vs listing) is exactly what the model is for.

    ``pending`` is the question the user is answering. It matters for
    "moderne ou non", where a bare "non" is a *value* for the field and not a
    refusal: without it the fast lane would re-ask the question.
    """
    normalized = _normalize(text)
    if not normalized:
        return None
    standing = _standing_reply(normalized, pending)
    if standing is not None:
        return standing
    denial = _no_to_a_form_question(normalized, pending)
    if denial is not None:
        return denial
    answer = pending_answer(normalized, pending)
    if answer is not None:
        return answer
    if normalized in AFFIRMATIVE:
        yes = _yes_to_a_form_question(normalized, pending)
        if yes is not None:
            return yes
        if pending in _NO_MEANS:
            # "Oui" to "y a-t-il une caution ?" or to "gardien ? parking ?"
            # says there is one without saying how many months or which of
            # the two. Returned as a bare dialogue act it carries no value,
            # produces no step, and the landlord gets the same question
            # back — so it goes to the model, which reads it alongside the
            # question and can ask for the missing half.
            return None
        return DeterministicMatch(intent="AFFIRMATIVE", confidence=0.98)
    if normalized in NEGATIVE:
        return DeterministicMatch(intent="NEGATIVE", confidence=0.98)
    entities = _extract_entities(normalized)
    if "price" in entities and len(normalized.split()) <= 4:
        return DeterministicMatch(
            intent="COLLECT_PRICE",
            confidence=0.92,
            entities={"price": entities["price"]},
        )
    # Only when no question is open: mid-form a greeting may still come with
    # an answer the model should read together with the open question.
    if pending is None and normalized in GREETINGS:
        return DeterministicMatch(intent="SUPPORT", confidence=0.9)
    return None


def shorthand_amount(text: str) -> int | None:
    """The amount when the message spells it as local shorthand, else None.

    Only the shorthand case is returned: it is exactly where the model's
    prior ("mil" = million) is wrong for this market, so the code owns the
    meaning and overrides the model's number. A plain "250 000" is left to
    the model, which reads it reliably.
    """
    normalized = _normalize(text)
    for pattern, multiplier in (
        (SHORTHAND_MILLION_PATTERN, 1_000_000),
        (SHORTHAND_THOUSAND_PATTERN, 1_000),
    ):
        match = pattern.search(normalized)
        if match:
            value = _parse_amount(match.group("amount")) * multiplier
            if value >= MIN_PLAUSIBLE_RENT_XAF:
                return value
    return None


def to_intent_result(match: DeterministicMatch) -> IntentResult:
    return IntentResult(
        intent=match.intent,
        confidence=match.confidence,
        entities=dict(match.entities),
    )
