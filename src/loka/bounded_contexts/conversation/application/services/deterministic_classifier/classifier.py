"""The classifier object handed to the pipeline."""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.services.deterministic_classifier.match import (
    DeterministicMatch,
)
from loka.bounded_contexts.conversation.application.services.deterministic_classifier.pending import (  # noqa: E501
    denial_answer,
    pending_answer,
)
from loka.bounded_contexts.conversation.application.services.deterministic_classifier.reading import (  # noqa: E501
    classify,
    shortcut,
    shorthand_amount,
    to_intent_result,
)
from loka.bounded_contexts.conversation.domain.services.ai_provider import IntentResult


class DeterministicClassifier:
    """Pure function from message text to a candidate intent.

    A thin object over the rule functions, so a pipeline can be handed one.
    """

    def classify(self, text: str, *, pending: str | None = None) -> DeterministicMatch | None:
        return classify(text, pending=pending)

    def shortcut(self, text: str, *, pending: str | None = None) -> DeterministicMatch | None:
        return shortcut(text, pending=pending)

    @staticmethod
    def denial_answer(pending: str | None) -> DeterministicMatch | None:
        return denial_answer(pending)

    def pending_answer(self, normalized: str, pending: str | None) -> DeterministicMatch | None:
        return pending_answer(normalized, pending)

    def shorthand_amount(self, text: str) -> int | None:
        return shorthand_amount(text)

    def to_intent_result(self, match: DeterministicMatch) -> IntentResult:
        return to_intent_result(match)
