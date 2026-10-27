"""Validate a proposal through the domain, before anything is written."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.flow_rules import (  # noqa: E501
    known_facts,
    target_flow,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    Verdict,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.housing.conversation.facts import (
    MIN_ACCEPTED_CONFIDENCE,
    FactExtractor,
    is_supported_intent,
)
from loka.bounded_contexts.housing.conversation.turns.places import (
    one_place,
)


class Judge:
    """Decides what one proposed intent is worth, with the reply still unwritten.

    Kept apart from writing the turn so the reply can be phrased from the
    *outcome* while the write transaction is still closed.
    """

    def __init__(self, extractor: FactExtractor) -> None:
        self._extractor = extractor

    def __call__(
        self,
        session: ConversationSession,
        *,
        intent: str,
        confidence: float,
        entities: dict[str, Any],
        reason: str | None,
    ) -> Verdict:
        if not is_supported_intent(intent):
            intent, reason = "UNKNOWN", reason or "unsupported_intent"
        if reason is None and confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = "low_confidence"
        facts = self._extractor.validate(one_place(entities, session))
        # The next step must consider the whole form, not just this message: a
        # landlord who gives their city after naming the property type is one
        # step further, not back at the type question. When this message opens a
        # *new* flow, the old facts belong to the abandoned attempt and are
        # deliberately ignored — matching the reset the session writer does.
        next_step = (
            self._extractor.next_step(
                intent,
                facts,
                flow=target_flow(session, intent),
                known=known_facts(session, intent),
            )
            if reason is None
            else None
        )
        return Verdict(
            intent=intent,
            facts=facts,
            accepted=reason is None,
            reason=reason,
            next_step=next_step,
        )
