"""The engine and the adapter must agree on what an availability answer is."""

from __future__ import annotations

import pytest

from loka.bounded_contexts.housing.conversation.adapters.conversation_availability import (
    ANSWER_BY_INTENT,
)
from loka.bounded_contexts.housing.conversation.turns.keys import (
    AVAILABILITY_INTENTS,
)

pytestmark = pytest.mark.unit


def test_every_availability_intent_the_engine_reads_is_one_the_adapter_records() -> None:
    assert set(ANSWER_BY_INTENT) == AVAILABILITY_INTENTS
