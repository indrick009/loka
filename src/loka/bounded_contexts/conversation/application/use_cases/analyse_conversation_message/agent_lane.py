"""The tenant search led by an agent: the model talks, the rules are its tools.

Behind ``AI__AGENT_ENABLED``. The agent asks the four questions in its own
words and records the facts its tools accept; once they are all in, the
search is recorded here as the form would have, and the list with its photos
follows through the demand matching. What comes after the list — a listing
picked, a price offered, the landlord asked, the unlock paid — stays with the
pipeline, and so does a message for another domain.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from loka.bounded_contexts.conversation.application.ports import (
    AgentAnswer,
    ConversationAgent,
    Exchange,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.session_writer import (  # noqa: E501
    SessionWriter,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    AnalysisReport,
    TurnCost,
)
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn_ledger import (  # noqa: E501
    CallUsage,
    TurnLedger,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.housing.conversation.facts import SEARCH_REQUIREMENTS
from loka.bounded_contexts.housing.conversation.facts.forms import SEARCH_QUESTION_BY_STEP
from loka.bounded_contexts.housing.conversation.turns.turns import HousingTurns
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.conversation_log import journal
from loka.shared.infrastructure.logging import get_logger

SEARCH_FIELDS = tuple(name for name, _ in SEARCH_REQUIREMENTS)
STEP_OF = dict(SEARCH_REQUIREMENTS)
# Where the agent leads: the four questions, the wait for a match, and the
# list itself — « c'est les seuls ? » is answered there, a number is not.
AGENT_STEPS = frozenset(
    {
        FlowStep.START,
        FlowStep.COLLECT_SEARCH_CRITERIA,
        *STEP_OF.values(),
        FlowStep.PRESENT_RESULTS,
        FlowStep.AWAITING_MATCH,
        FlowStep.COLLECT_PROPERTY_CHOICE,
    }
)
HANDED_BACK = frozenset({"other_service", "listing_choice"})
# A message shorter than this does not change the thread's language.
MIN_WORDS_TO_SWITCH = 3
_DIGIT = re.compile(r"\d")

_logger = get_logger(__name__)


class AgentLane:
    def __init__(
        self,
        agent: ConversationAgent | None,
        *,
        uow: UnitOfWork,
        writer: SessionWriter,
        ledger: TurnLedger,
        housing: HousingTurns,
    ) -> None:
        self._agent = agent
        self._uow = uow
        self._writer = writer
        self._ledger = ledger
        self._housing = housing

    @staticmethod
    def leads(session: ConversationSession, text: str) -> bool:
        """Whether this message is the agent's: a tenant search under way."""
        if session.flow is not FlowName.PROPERTY_SEARCH or session.step not in AGENT_STEPS:
            return False
        # On the list, a number picks a listing or offers a price: the
        # pipeline's choice and negotiation handle it.
        return not (session.step is FlowStep.COLLECT_PROPERTY_CHOICE and _DIGIT.search(text))

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
        if self._agent is None or not text.strip():
            return None
        decision = await self._ledger.budget(session, now=now)
        if not decision.allowed:
            return None
        fresh = session.flow is not FlowName.PROPERTY_SEARCH
        known = (
            {}
            if fresh
            else {k: session.context[k] for k in SEARCH_FIELDS if k in session.context}
        )
        try:
            reply = await self._agent.answer(
                text,
                facts=known,
                thread=thread,
                language=session.language,
                known_place=known_place(session),
            )
        except Exception as exc:  # the pipeline still answers
            _logger.warning("agent_failed", session_id=str(session.id), error=type(exc).__name__)
            return None
        journal(
            "reading", session.phone.e164, lane="agent", act=reply.act, flow=session.flow.value,
            step=session.step.value, facts=reply.facts, submitted=reply.submitted or None,
            model_reply=reply.reply,
        )
        if reply.act in HANDED_BACK or not reply.reply.strip():
            return None
        return await self.apply(
            session, reply, fresh=fresh, text=text, message_id=message_id, now=now
        )

    async def apply(
        self,
        session: ConversationSession,
        reply: AgentAnswer,
        *,
        fresh: bool,
        text: str,
        message_id: uuid.UUID,
        now: datetime,
    ) -> AnalysisReport:
        """The tenant's answers kept, the next question asked or the search recorded."""
        observed = session.revision
        if fresh:
            session.start_flow(FlowName.PROPERTY_SEARCH, now=now, first_step=FlowStep.START)
        if reply.language != session.language and len(text.split()) >= MIN_WORDS_TO_SWITCH:
            session.language = reply.language
        facts = {k: v for k, v in reply.facts.items() if k in SEARCH_FIELDS}
        session.merge_context(facts, now=now)
        sentence = reply.reply
        missing = [name for name in SEARCH_FIELDS if session.context.get(name) in (None, "", {})]
        question: str | None = None
        if missing:
            step: FlowStep = STEP_OF[missing[0]]
            question = SEARCH_QUESTION_BY_STEP.get(step)
        elif reply.submitted or session.step not in (
            FlowStep.AWAITING_MATCH,
            FlowStep.COLLECT_PROPERTY_CHOICE,
        ):
            platform, recorded = await self._housing.record_search(
                session, dict(session.context), now=now
            )
            step = recorded or FlowStep.AWAITING_MATCH
            if step is not FlowStep.AWAITING_MATCH and platform:
                # Nowhere to keep it: the platform's sentence says so, not the
                # model's promise of a list.
                sentence = platform
        else:
            step = session.step
            carried = session.context.get("next_question")
            question = carried if isinstance(carried, str) else None
        session.transition(step, now=now)
        session.record_pending_question(question)
        await self._writer.save(session, observed=observed)
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
        await self._ledger.record(session, intent="PROPERTY_SEARCH", cost=cost, call=call)
        self._writer.stage_reply(
            session, intent="PROPERTY_SEARCH", reason=None, question_key=question,
            text=sentence, now=now,
        )
        await self._uow.commit()
        return AnalysisReport(
            session_id=session.id,
            message_id=message_id,
            intent="PROPERTY_SEARCH",
            confidence=1.0,
            used_llm=True,
            accepted=True,
            reason=None,
            facts=facts,
            next_step=step.value,
            question_key=question,
            cost_usd=reply.cost_usd,
        )


def known_place(session: ConversationSession) -> dict[str, object] | None:
    """The place the user gave for another task in this thread, if any."""
    for source in (session.context, session.memory):
        for name in ("place", "location"):
            value = source.get(name)
            if isinstance(value, dict) and value.get("city"):
                return dict(value)
    return None
