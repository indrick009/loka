"""The second model call: a sentence written from the turn's outcome."""

from __future__ import annotations

from loka.bounded_contexts.ai.application.ports import Exchange, ResponseModel, TurnSummary
from loka.bounded_contexts.ai.application.services.prompts import build_reply_prompt
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.flow_rules import (
    carried_question,
    target_flow,
)
from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message.turn import (
    USE_CASE,
    Verdict,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import LlmUsage
from loka.bounded_contexts.ai.domain.services.fact_extraction import question_to_ask
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
)
from loka.shared.domain.errors import DomainError
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.metrics import llm_calls_total

_logger = get_logger(__name__)


class ReplyWriter:
    def __init__(self, model: ResponseModel | None) -> None:
        self._model = model

    async def compose(
        self,
        *,
        verdict: Verdict,
        text: str,
        session: ConversationSession,
        history: list[TurnSummary],
        thread: list[Exchange] | None = None,
    ) -> tuple[str | None, LlmUsage | None]:
        """Let the model phrase the answer, or return ``(None, None)``.

        ``None`` means "use the template": either no response model is
        configured, or writing it failed. A reply failure must never cost the
        user their turn, so the exception is swallowed here and the outbound
        layer falls back to its stable copy.
        """
        if self._model is None or not text.strip():
            return None, None

        # A refused turn moved nothing, so the question still open is the one
        # to ask again — with the rephrase request, never past it.
        next_key: str | None = carried_question(session)
        if verdict.accepted:
            # A turn that advanced nothing leaves the previous question open,
            # and the writer has to be told *that* one. Handed no question at
            # all it invents one, and then the sentence and the session
            # disagree about what was asked.
            #
            # Observed live: a landlord answered "aucun" to the caution, the
            # extractor recorded no value, so no step was decided and the
            # writer was given nothing to ask. It wrote "D'accord, pas de
            # caution. Combien de mois d'avance ?" — so he was asked the
            # advance while the session still waited for the caution, and his
            # "6" would have been filed as six months of caution. Silently.
            #
            # The same rule as the session writer's ``advance``, through one
            # helper, so the question asked and the question recorded cannot
            # drift apart again.
            next_key = question_to_ask(
                next_step=verdict.next_step,
                flow=target_flow(session, verdict.intent),
                pending=carried_question(session),
            )
        prompt = build_reply_prompt(
            text=text,
            language=session.language,
            intent=verdict.intent,
            accepted=verdict.accepted,
            accepted_facts=verdict.facts.as_context(),
            next_key=next_key,
            history=history,
            # The same flow ``next_key`` was computed from, and not
            # ``session.flow``: on the message that *opens* a flow the session
            # still reads SUPPORT, so the writer was handed the wrong role and
            # the wrong form. Observed live: a tenant said he was looking for a
            # studio, the backend decided "where are you looking?", and the
            # writer asked "moderne ou non ?" — a landlord field. He answered
            # it, the answer was filed as a bare "oui" against a question
            # nobody had asked him, and his "moderne" was lost.
            flow=target_flow(session, verdict.intent),
            known_facts=dict(session.context),
            exchanges=thread,
        )
        model_name = str(getattr(self._model, "model", "unknown"))
        try:
            response = await self._model.generate_response(prompt)
        except DomainError as exc:
            # The user still gets a sentence — the outbound layer renders the
            # template — but the turn's own call has already been counted as
            # "ok", so without these two lines a response model that is down
            # looks perfectly healthy: every answer quietly degrades to a
            # template and no dashboard says a word.
            llm_calls_total.labels(use_case=USE_CASE, model=model_name, outcome="error").inc()
            _logger.warning(
                "reply_composition_failed",
                session_id=str(session.id),
                model=model_name,
                error=type(exc).__name__,
            )
            return None, None
        sentence = response.content.strip()
        if not sentence:
            # Billed and unusable. The usage is still returned so the ledger and
            # the budget see what the call cost; dropping it made an empty-answer
            # regression indistinguishable from a deployment with no reply model.
            _logger.warning(
                "reply_composition_empty", session_id=str(session.id), model=model_name
            )
            return None, response.usage
        return sentence, response.usage
