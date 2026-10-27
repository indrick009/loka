"""Deterministic validation of extracted facts.

Whatever produced the candidates — regex rules or a language model — the
values must survive the *domain's* value objects before they are allowed
anywhere near a session. A model is a good guesser and a bad authority:
"150 000 000 000" or "-2 chambres" must never reach a listing, and the cheapest
place to refuse them is here, in code, with one rule for every source.

Refused fields are dropped, never repaired. The conversation asks again."""

from loka.bounded_contexts.conversation.domain.services.extracted_facts import (
    ExtractedFacts,
)
from loka.bounded_contexts.housing.conversation.facts.extractor import (
    FactExtractor,
)
from loka.bounded_contexts.housing.conversation.facts.forms import (
    NEXT_LODGINGS_KEY,
    PROPERTY_REQUIREMENTS,
    SEARCH_REQUIREMENTS,
    question_key_for,
    question_to_ask,
)
from loka.bounded_contexts.housing.conversation.facts.intents import (
    FLOW_BY_OPENING_INTENT,
    LISTING_FLOWS,
    SUPPORTED_INTENTS,
    is_supported_intent,
)
from loka.bounded_contexts.housing.conversation.facts.limits import (
    MAX_PLAUSIBLE_RENT_XAF,
    MAX_RESULT_CHOICES,
    MIN_ACCEPTED_CONFIDENCE,
    MIN_PLAUSIBLE_RENT_XAF,
)

__all__ = [
    "FLOW_BY_OPENING_INTENT",
    "LISTING_FLOWS",
    "MAX_PLAUSIBLE_RENT_XAF",
    "MAX_RESULT_CHOICES",
    "MIN_ACCEPTED_CONFIDENCE",
    "MIN_PLAUSIBLE_RENT_XAF",
    "NEXT_LODGINGS_KEY",
    "PROPERTY_REQUIREMENTS",
    "SEARCH_REQUIREMENTS",
    "SUPPORTED_INTENTS",
    "ExtractedFacts",
    "FactExtractor",
    "is_supported_intent",
    "question_key_for",
    "question_to_ask",
]
