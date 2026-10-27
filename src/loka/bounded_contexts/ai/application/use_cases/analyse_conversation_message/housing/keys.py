"""Session keys and intents the housing conversation shares with the engine.

Constants only, so the engine can read them without importing housing logic.
"""

from __future__ import annotations

# Photos sent before the listing exists — very common, and they used to be
# lost. Kept as ``[{"key", "kind"}]`` and attached the moment it is saved.
# The leading underscore keeps them out of every prompt.
PENDING_MEDIA_KEY = "_pending_media"

# Staged on a landlord's thread when a tenant picks his lodging. Set as the
# open question there, so his next message is read as the answer to it.
AVAILABILITY_QUESTION_KEY = "ask.availability_check"

CONFIRM_QUESTION_KEY = "ask.confirm_property"

# A landlord's answers to "le logement est encore disponible ?". An intent
# outside this set is never mapped to the nearest one: guessing between "one
# was taken" and "all were taken" costs a landlord a lodging.
AVAILABILITY_INTENTS = frozenset(
    {
        "AVAILABILITY_STILL_AVAILABLE",
        "AVAILABILITY_ONE_TAKEN",
        "AVAILABILITY_RENTED",
        "AVAILABILITY_TEMPORARILY_UNAVAILABLE",
    }
)


def is_availability_answer(intent: str) -> bool:
    return intent in AVAILABILITY_INTENTS
