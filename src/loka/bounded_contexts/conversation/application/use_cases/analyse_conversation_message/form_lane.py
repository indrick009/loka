"""The form of a plugged domain, filled by an agent: the model talks, the domain validates.

Behind ``AI__AGENT_ENABLED``, at the one step every plugged domain shares,
``COLLECT_FIELD``. The agent records what it understood through the domain's
own validation and asks the next field in its own words; the facts then go
through the ``Judge`` and the domain's turns exactly as a model reading would,
so what the domain does with them — search, propose, book, pay — is unchanged.
Choices, proposals, relays and payments stay with the pipeline.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.conversation.application.plugs.registry import PLUG_FLOWS, spec_for
from loka.bounded_contexts.conversation.application.ports import Exchange, FormFiller
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.agent_lane import (  # noqa: E501
    MIN_WORDS_TO_SWITCH,
    known_place,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    AnalysisReport,
    TurnCost,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_applier import (  # noqa: E501
    TurnApplier,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    CallUsage,
    TurnLedger,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.verdicts import (  # noqa: E501
    Judge,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowStep,
)
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


class FormLane:
    def __init__(
        self, filler: FormFiller, *, judge: Judge, applier: TurnApplier, ledger: TurnLedger
    ) -> None:
        self._filler = filler
        self._judge = judge
        self._applier = applier
        self._ledger = ledger

    @staticmethod
    def leads(session: ConversationSession) -> bool:
        return session.flow in PLUG_FLOWS and session.step is FlowStep.COLLECT_FIELD

    async def answer(
        self,
        session: ConversationSession,
        *,
        text: str,
        thread: list[Exchange],
        pending_key: str | None,
        message_id: uuid.UUID,
        now: datetime,
    ) -> AnalysisReport | None:
        """The agent's turn, or ``None`` to hand the message to the pipeline."""
        spec = spec_for(session.flow)
        if spec is None or not text.strip():
            return None
        if not (await self._ledger.budget(session, now=now)).allowed:
            return None
        facts = {
            name: value
            for name, value in session.context.items()
            if not name.startswith("_") and name != "next_question"
        }
        try:
            reply = await self._filler.answer(
                text,
                flow=session.flow,
                facts=facts,
                open_field=spec.field_of(pending_key),
                thread=thread,
                language=session.language,
                known_place=known_place(session),
            )
        except Exception as exc:  # the pipeline still answers
            _logger.warning(
                "form_agent_failed", session_id=str(session.id), error=type(exc).__name__
            )
            return None
        journal(
            "reading", session.phone.e164, lane="form_agent", act=reply.act,
            flow=session.flow.value, step=session.step.value, entities=reply.entities,
            asking=reply.asking, model_reply=reply.reply,
        )
        if reply.act == "other_service" or not reply.reply:
            return None
        if reply.language != session.language and len(text.split()) >= MIN_WORDS_TO_SWITCH:
            session.language = reply.language
        intent = next(name for name, flow in spec.opening_intents.items() if flow is session.flow)
        aside = reply.act in ("question", "social") and not reply.entities
        if aside:
            intent = "SUPPORT"
        verdict = self._judge(
            session, intent=intent, confidence=1.0, entities=reply.entities, reason=None
        )
        cost = TurnCost(
            message_id=message_id,
            confidence=1.0,
            used_llm=True,
            latency_ms=reply.latency_ms,
            cost_usd=reply.cost_usd,
            now=now,
        )
        call = CallUsage(
            model=reply.model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            latency_ms=reply.latency_ms,
            cost_usd=reply.cost_usd,
        )
        call.observe()
        if aside:
            # A remark or a question with no fact is answered, not a reason to
            # run the task again: « je reste sur ce que j'ai commandé » got the
            # « toujours rien de jus » of the last search twice (7 Nov 2026).
            return await self._applier.aside(
                session, verdict, cost, call=call, reply_text=reply.reply, text=text
            )
        return await self._applier.apply(
            session,
            verdict,
            cost,
            call=call,
            reply_text=reply.reply,
            asking=reply.asking,
            text=text,
        )
