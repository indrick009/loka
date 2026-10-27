"""The intents the pipeline accepts, and the flows they open."""

from __future__ import annotations

from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
)

# Intents that concern a listing or a search. Everything else — "merci", "c'est
# loué", "je vais réfléchir" — records its turn but must never move a listing
# form forward.
LISTING_INTENTS = frozenset(
    {
        "CREATE_PROPERTY",
        "PROPERTY_SEARCH",
        "COLLECT_PROPERTY_TYPE",
        "COLLECT_LOCATION",
        "COLLECT_PRICE",
        "COLLECT_FEATURES",
        "COLLECT_STANDING",
        "COLLECT_CHARGES",
        "COLLECT_DEPOSIT",
        "COLLECT_ADVANCE",
        "COLLECT_QUANTITY",
        "COLLECT_SECURITY",
        "COLLECT_PROPERTY_CHOICE",
        "COLLECT_MINIMUM_DURATION",
        "COLLECT_AVAILABILITY",
        "COLLECT_CONDITIONS",
        "COLLECT_SEARCH_CRITERIA",
    }
)


# The intents that *open* a flow, mapped to the flow they open. Recorded rather
# than inferred, because starting the wrong flow strands the user in a form for
# something they never asked for.
FLOW_BY_OPENING_INTENT: dict[str, FlowName] = {
    "CREATE_PROPERTY": FlowName.PROPERTY_CREATION,
    "PROPERTY_SEARCH": FlowName.PROPERTY_SEARCH,
}


LISTING_FLOWS = frozenset({FlowName.PROPERTY_CREATION, FlowName.PROPERTY_SEARCH})


# Intents the pipeline may act on. Anything else means the model invented a
# state the platform does not have, so the message is treated as untrusted.
#
# `test_fact_extraction.py` asserts that every intent `DeterministicClassifier`
# can emit appears here: this set is the trust boundary, so it has to be closed
# by a test rather than by memory.
SUPPORTED_INTENTS = frozenset(
    {
        # Deterministic classifier.
        "CREATE_PROPERTY",
        "COLLECT_PROPERTY_TYPE",
        "COLLECT_PRICE",
        "COLLECT_FEATURES",
        "COLLECT_STANDING",
        "COLLECT_ACQUISITION_SOURCE",
        "COLLECT_DEPOSIT",
        "COLLECT_ADVANCE",
        "COLLECT_QUANTITY",
        "COLLECT_SECURITY",
        "COLLECT_PROPERTY_CHOICE",
        "AVAILABILITY_STILL_AVAILABLE",
        "AVAILABILITY_ONE_TAKEN",
        "AVAILABILITY_RENTED",
        "AVAILABILITY_TEMPORARILY_UNAVAILABLE",
        "AFFIRMATIVE",
        "NEGATIVE",
        # Flow-level intents a model may propose.
        "COLLECT_LOCATION",
        "COLLECT_MEDIA",
        "COLLECT_CHARGES",
        "COLLECT_MINIMUM_DURATION",
        "COLLECT_AVAILABILITY",
        "COLLECT_CONDITIONS",
        "COLLECT_SEARCH_CRITERIA",
        "PROPERTY_SEARCH",
        "AVAILABILITY_CHECK",
        "SUPPORT",
        "UNKNOWN",
    }
)


def is_supported_intent(intent: str) -> bool:
    return intent in SUPPORTED_INTENTS
