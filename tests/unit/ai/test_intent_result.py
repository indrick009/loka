"""``IntentResult.from_structured`` — a model that extracted nothing is not a failure.

Measured against the live pipeline on 2026-10-11: **28% of turns were lost**
because the model answered with ``"entities": null`` instead of ``{}``. The
answer was otherwise perfectly valid — right intent, right reply — but the guard
refused it, the exception escaped the analysis, the consumer retried, and the
user received nothing at all. The failure concentrated on social messages and
meta-questions, which is the start and the end of every conversation, and it hit
4/4 on a prompt-injection attempt.

See ``docs/conversation-defects-v1.md`` (D1) for the measurement.

The distinction this file locks down: *nothing extracted* is an ordinary
outcome and must flow through as an empty map, while *a field of the wrong
type* is a broken contract and stays an error, so a retry can sample a better
answer and the metric stays visible.
"""

from __future__ import annotations

from typing import Any

import pytest

from loka.bounded_contexts.ai.domain.services.ai_provider import IntentResult
from loka.shared.domain.errors import ExternalServiceUnavailable

pytestmark = pytest.mark.unit


def payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent": "SUPPORT",
        "confidence": 0.8,
        "language": "fr",
        "reply": "Bonjour, comment puis-je vous aider ?",
        "entities": {},
    }
    base.update(overrides)
    return base


class TestNothingExtracted:
    """The measured failure: a valid answer carrying no entities."""

    def test_a_null_entity_map_is_read_as_empty(self) -> None:
        result = IntentResult.from_structured(payload(entities=None))

        assert result.entities == {}
        assert result.intent == "SUPPORT"
        assert result.reply == "Bonjour, comment puis-je vous aider ?"

    def test_a_missing_entity_map_is_read_as_empty(self) -> None:
        body = payload()
        del body["entities"]

        assert IntentResult.from_structured(body).entities == {}

    def test_the_reply_survives_an_absent_entity_map(self) -> None:
        """The whole point: the turn still has something to send back."""
        result = IntentResult.from_structured(payload(entities=None))

        assert result.reply is not None


class TestBrokenContract:
    """A field of the wrong type is still refused: retrying may sample better."""

    @pytest.mark.parametrize("value", [[], ["studio"], "studio", 42])
    def test_an_entity_map_of_the_wrong_type_is_refused(self, value: Any) -> None:
        with pytest.raises(ExternalServiceUnavailable):
            IntentResult.from_structured(payload(entities=value))

    @pytest.mark.parametrize("value", ["high", None, "", [0.5]])
    def test_a_non_numeric_confidence_is_a_typed_error(self, value: Any) -> None:
        """Not a bare ``ValueError``.

        ``float("high")`` raises ``ValueError``, which is not a ``DomainError``:
        it escaped the adapter untyped and crashed the consumer on a path nobody
        was watching. A provider that answers badly must always produce the same
        typed failure.
        """
        with pytest.raises(ExternalServiceUnavailable):
            IntentResult.from_structured(payload(confidence=value))

    @pytest.mark.parametrize("value", [-0.1, 1.1, 42])
    def test_a_confidence_outside_the_unit_interval_is_refused(self, value: Any) -> None:
        with pytest.raises(ExternalServiceUnavailable):
            IntentResult.from_structured(payload(confidence=value))


class TestValidPayloadsKeepWorking:
    def test_entities_are_carried_through(self) -> None:
        result = IntentResult.from_structured(
            payload(entities={"property_type": "STUDIO", "price": 60000})
        )

        assert result.entities == {"property_type": "STUDIO", "price": 60000}

    def test_an_absent_intent_degrades_to_unknown(self) -> None:
        body = payload()
        del body["intent"]

        assert IntentResult.from_structured(body).intent == "UNKNOWN"

    def test_a_blank_reply_becomes_none(self) -> None:
        """An empty sentence must not be sent; the template takes over."""
        assert IntentResult.from_structured(payload(reply="   ")).reply is None
