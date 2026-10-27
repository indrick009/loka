"""The model's reading of a message, and the sentence written with it."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.conversation.application.ports import Exchange, IntentModel, TurnSummary
from loka.bounded_contexts.conversation.application.services.deterministic_classifier import (
    DeterministicClassifier,
)
from loka.bounded_contexts.conversation.application.services.prompts import build_analysis_prompt
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.flow_rules import (  # noqa: E501
    target_flow,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.reply_fit import (  # noqa: E501
    inline_reply_fits,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.reply_writer import (  # noqa: E501
    ReplyWriter,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnCost,
    Verdict,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    CallUsage,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.verdicts import (  # noqa: E501
    Judge,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.bounded_contexts.conversation.domain.repositories.inbound_message_repository import (
    StoredInboundMessage,
)
from loka.bounded_contexts.conversation.domain.services.ai_provider import SUPPORTED_LANGUAGES
from loka.bounded_contexts.housing.conversation.turns.turns import (
    HousingTurns,
)
from loka.shared.domain.errors import ExternalServiceUnavailable
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import ai_bypass_total

_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ModelTurn:
    verdict: Verdict
    cost: TurnCost
    call: CallUsage
    reply_text: str | None


class ModelLane:
    def __init__(
        self,
        model: IntentModel,
        *,
        classifier: DeterministicClassifier,
        housing: HousingTurns,
        judge: Judge,
        replies: ReplyWriter,
    ) -> None:
        self._model = model
        self._classifier = classifier
        self._housing = housing
        self._judge = judge
        self._replies = replies

    async def read(
        self,
        session: ConversationSession,
        *,
        message: StoredInboundMessage,
        message_id: uuid.UUID,
        pending_key: str | None,
        history: list[TurnSummary],
        thread: list[Exchange],
        now: datetime,
    ) -> ModelTurn | None:
        """The model's turn, or ``None`` when the provider could not answer.

        The provider being down, timing out or answering with something
        unusable used to escape: the broker retried the message and parked it in
        the DLQ, and the user was never answered — the silence this pipeline
        exists to prevent. ``None`` sends the turn to the rules instead; a worse
        reply beats no reply, and the turn is recorded so the outage stays
        measurable.
        """
        text = message.text or ""
        ai_bypass_total.labels(stage="llm").inc()
        # A cheap rule reading only picks which form the prompt shows; the model
        # still chooses the intent. On an opening turn it is what tells a tenant
        # the search fields instead of the landlord's.
        hint = self._classifier.classify(text, pending=pending_key)
        prompt_flow = target_flow(session, hint.intent) if hint is not None else session.flow
        prompt = build_analysis_prompt(
            text=message.text,
            default_language=session.language,
            history=history,
            flow=prompt_flow,
            step=session.step,
            pending_question=pending_key,
            known_facts=dict(session.context),
            exchanges=thread,
        )
        try:
            result = await self._model.understand_intent(prompt)
        except ExternalServiceUnavailable as exc:
            _logger.warning(
                "analysis_degraded_to_rules",
                session_id=str(session.id),
                message_id=str(message_id),
                error=exc.message,
            )
            ai_bypass_total.labels(stage="provider_down").inc()
            return None
        # The model read the message, so it is the one that knows which language
        # the user is writing in. Adopting its answer keeps a bare "150000" in
        # tomorrow's fallback in the right language instead of snapping to French.
        if result.language in SUPPORTED_LANGUAGES:
            session.language = result.language
        entities = dict(result.entities)
        # The locale shorthand ("50 mil") is a rule, not a guess: when the
        # message spells one it overrides whatever number the model proposed,
        # because the model's prior reads "mil" as a million.
        shorthand = self._classifier.shorthand_amount(text)
        if shorthand is not None:
            entities["price"] = shorthand
        intent, entities = self._housing.read_open_question(
            pending_key=pending_key, hint=hint, intent=result.intent, entities=entities, text=text
        )
        verdict = self._judge(
            session, intent=intent, confidence=result.confidence, entities=entities, reason=None
        )

        # Preferred path: the classification call already wrote the reply in its
        # JSON, so the whole turn costs one round-trip. Only a reply that does
        # not ask what was decided pays for a second call, written before the
        # write transaction opens.
        reply_text: str | None = result.reply
        if not inline_reply_fits(
            prompt_flow, session=session, verdict=verdict, asking=result.asking
        ):
            reply_text = None
        reply_usage = None
        if reply_text is None:
            reply_text, reply_usage = await self._replies.compose(
                verdict=verdict, text=text, session=session, history=history, thread=thread
            )

        call = CallUsage.of(result.usage, reply_usage, model=self._model.model)
        call.observe()
        return ModelTurn(
            verdict=verdict,
            cost=TurnCost(
                message_id=message_id,
                confidence=result.confidence,
                used_llm=True,
                latency_ms=call.latency_ms,
                cost_usd=call.cost_usd,
                now=now,
            ),
            call=call,
            reply_text=reply_text,
        )
