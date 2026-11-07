"""The single Kora agent: one model for the thread, the services' rules as its tools.

Behind ``AI__KORA_AGENT_ENABLED``. Before any task, in a food search and in a
tenant search, the agent reads the message with the whole thread and the
user's memory, and decides: a food search, an order, a tenant's answers, or
only a reply. Each action then runs through the code that already owns it —
the ``Judge`` and the Food turns, the tenant search recording — so what is
searched, passed on or paid is unchanged. A message for any other service is
handed back to the pipeline.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from loka.bounded_contexts.conversation.application.ports import (
    AgentAnswer,
    Exchange,
    KoraAgent,
    KoraAnswer,
    KoraState,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.agent_lane import (  # noqa: E501
    MIN_WORDS_TO_SWITCH,
    SEARCH_FIELDS,
    AgentLane,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.flow_rules import (  # noqa: E501
    carried_question,
    facts_worth_carrying,
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
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.food.conversation.turns import HITS_KEY
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.logging import get_logger

# The lines of the thread the agent reads; the other lanes keep their six.
THREAD_LINES = 20
_BARE_NUMBER = re.compile(r"\s*\d\s*\.?\s*")
_OFFER_FIELDS = ("dish", "price", "restaurant")

_logger = get_logger(__name__)


class KoraLane:
    def __init__(
        self,
        agent: KoraAgent,
        *,
        judge: Judge,
        applier: TurnApplier,
        ledger: TurnLedger,
        search: AgentLane,
    ) -> None:
        self._agent = agent
        self._judge = judge
        self._applier = applier
        self._ledger = ledger
        self._search = search

    @staticmethod
    def leads(session: ConversationSession, text: str) -> bool:
        """Before any task, in a food search, in a tenant search; not a bare pick."""
        if session.step is FlowStep.CLARIFY_ROLE:
            return False
        if session.flow is FlowName.PROPERTY_SEARCH:
            return AgentLane.leads(session, text)
        if session.flow is FlowName.FOOD_SEARCH and session.step is FlowStep.COLLECT_CHOICE:
            # « 2 » is read exactly by the rules, without a model.
            return not _BARE_NUMBER.fullmatch(text)
        return session.flow in (FlowName.SUPPORT, FlowName.FOOD_SEARCH)

    async def answer(
        self,
        session: ConversationSession,
        *,
        text: str,
        thread: list[Exchange],
        message_id: uuid.UUID,
        now: datetime,
    ) -> AnalysisReport | None:
        """The agent's turn, or ``None`` to hand the message to the pipeline."""
        if not text.strip() or not (await self._ledger.budget(session, now=now)).allowed:
            return None
        try:
            reply = await self._agent.answer(text, state=state_of(session), thread=thread)
        except Exception as exc:  # the pipeline still answers
            _logger.warning(
                "kora_agent_failed", session_id=str(session.id), error=type(exc).__name__
            )
            return None
        journal(
            "reading", session.phone.e164, lane="kora", act=reply.act, action=reply.action,
            flow=session.flow.value, step=session.step.value, entities=reply.entities,
            model_reply=reply.reply,
        )
        if reply.act == "other_service" and reply.action is None:
            # A tool that recorded an action is kept whatever the act says:
            # « 6 » months of advance came back other_service once the search
            # was complete (journal of 7 Nov 2026, 10:29).
            return None
        if reply.language != session.language and len(text.split()) >= MIN_WORDS_TO_SWITCH:
            session.language = reply.language
        if reply.action == "lodging":
            return await self._search.apply(
                session,
                _as_search(reply),
                fresh=session.flow is not FlowName.PROPERTY_SEARCH,
                text=text,
                message_id=message_id,
                now=now,
            )
        cost, call = _cost(reply, message_id=message_id, now=now)
        if reply.action in ("food_search", "food_order"):
            intent = "FOOD_SEARCH" if reply.action == "food_search" else "COLLECT_CHOICE"
            verdict = self._judge(
                session, intent=intent, confidence=1.0, entities=reply.entities, reason=None
            )
            return await self._applier.apply(
                session,
                verdict,
                cost,
                call=call,
                reply_text=reply.reply or None,
                # Asked by the domain when the search has no place yet, in the
                # agent's words.
                asking="place" if "city" not in reply.entities else None,
                text=text,
            )
        if not reply.reply:
            return None
        # Only a reply: the task stays where it was, its question still open.
        verdict = self._judge(session, intent="SUPPORT", confidence=1.0, entities={}, reason=None)
        return await self._applier.aside(
            session, verdict, cost, call=call, reply_text=reply.reply, text=text
        )


def state_of(session: ConversationSession) -> KoraState:
    """What the agent is told before the message: the task, its list, the memory."""
    offered = session.context.get(HITS_KEY)
    offers = (
        [{k: o.get(k) for k in _OFFER_FIELDS} for o in offered if isinstance(o, dict)]
        if session.step is FlowStep.COLLECT_CHOICE and isinstance(offered, list)
        else []
    )
    return KoraState(
        flow=session.flow.value,
        step=session.step.value,
        language=session.language,
        facts=facts_worth_carrying(dict(session.context)),
        open_question=carried_question(session),
        offers=offers,
        memory=session.memory,
    )


def _as_search(reply: KoraAnswer) -> AgentAnswer:
    facts = {k: v for k, v in reply.entities.items() if k in SEARCH_FIELDS}
    return AgentAnswer(
        act="slot_answer",
        reply=reply.reply,
        facts=facts,
        language=reply.language,
        submitted=all(facts.get(k) not in (None, "", {}) for k in SEARCH_FIELDS),
        model=reply.model,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        latency_ms=reply.latency_ms,
        cost_usd=reply.cost_usd,
    )


def _cost(
    reply: KoraAnswer, *, message_id: uuid.UUID, now: datetime
) -> tuple[TurnCost, CallUsage]:
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
    return cost, call
