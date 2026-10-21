"""Shadow decisions: a decision model reads every turn, and changes nothing.

The pilot behind the question "can a System One model take Kora's small
decisions?" — which kind of message this is, which side of the market the user
is on, which service it concerns, whether a place is added or replaces the
others. Those are the decisions behind most defects found in live tests: a
landlord's question put to a tenant, "mon frère" read as an acquisition source,
"Logbessou aussi" replacing two neighbourhoods.

Shadow, not switch: the model is called alongside the turn and its answer is
stored next to what the pipeline actually did. Nobody reads it in the thread.
Its failures are recorded too, and never reach the user — a pilot that could
break a conversation is not a pilot.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    AiUsageLedger,
    ChoiceQuestion,
    DecisionModel,
    Decisions,
    DecisionShadowLog,
    ShadowDecision,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.prompts import question_topic
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)

USE_CASE = "decision_shadow"

# The questions, written once. Options are Kora's own vocabulary so a stored
# answer can be compared with the pipeline's without translation.
TURN_QUESTIONS: dict[str, ChoiceQuestion] = {
    "act": ChoiceQuestion(
        instructions="What is the user doing with this WhatsApp message?",
        options={
            "business_intent": "says what he wants: to rent, to find or to publish a lodging",
            "slot_answer": "answers the question Kora is waiting for",
            "correction": "changes or adds to something he already said",
            "follow_up": "asks about progress, a delay, availability or a visit",
            "question": (
                "asks Kora something: a price, a condition, how it works, what "
                "he said earlier, whether Kora understood"
            ),
            "confirmation": "agrees to what Kora proposed",
            "refusal": "declines or cancels what Kora proposed",
            "social": "greeting, thanks or small talk only",
            "topic_switch": "now wants something else than the active task",
            "out_of_scope": (
                "talks about something Kora does not handle: football, news, a "
                "joke, politics"
            ),
        },
    ),
    "role": ChoiceQuestion(
        instructions="On the housing market, which side is the user on?",
        options={
            "tenant": "he is looking for a lodging to live in",
            "landlord": "he owns or offers a lodging to rent out",
            "unknown": "the message and the active task do not say",
        },
    ),
    "domain": ChoiceQuestion(
        instructions="Which service is the message about?",
        options={
            "housing": "renting or listing a lodging",
            "food": "eating, a restaurant, a dish, a reservation",
            "dating": "meeting someone, a relationship",
            "found": "a lost or found object or document",
            "none": "none of these",
        },
    ),
    "neighbourhood_change": ChoiceQuestion(
        instructions=(
            "Does the message add neighbourhoods to those already accepted, "
            "replace them, or not touch them?"
        ),
        options={
            "adds": "a place on top of the known ones",
            "replaces": "places instead of the known ones",
            "none": "no neighbourhood change",
        },
    ),
}

# Without the active task a bare "60 mille" names no service at all: the first
# trial misfiled four such answers until the state said a search was running.
_ACTIVE_TASK: dict[FlowName, str] = {
    FlowName.PROPERTY_SEARCH: "housing: tenant search",
    FlowName.PROPERTY_CREATION: "housing: landlord listing",
}


def turn_state(session: ConversationSession, text: str) -> dict[str, Any]:
    """What the decision model reads: the task, the open question, the facts."""
    pending = session.context.get("next_question")
    pending_key = pending if isinstance(pending, str) else None
    return {
        "active_task": _ACTIVE_TASK.get(session.flow, "none"),
        "step": session.step.value,
        "kora_is_waiting_for": question_topic(pending_key, session.flow),
        "known_facts": {
            name: value
            for name, value in session.context.items()
            if name != "next_question" and not str(name).startswith("_")
        },
        "user_message": text,
    }


@dataclass(slots=True)
class PendingShadow:
    """A decision under way, and the state it was asked about."""

    task: asyncio.Task[Decisions]
    message_id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID | None
    flow_before: str
    step_before: str
    pending_question: str | None
    deadline: float


class DecisionShadow:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        model: DecisionModel,
        log: DecisionShadowLog,
        usage: AiUsageLedger,
        provider: str,
        timeout_seconds: float,
    ) -> None:
        self._uow = uow
        self._model = model
        self._log = log
        self._usage = usage
        self._provider = provider
        self._timeout = timeout_seconds

    def start(
        self, session: ConversationSession, *, message_id: uuid.UUID, text: str
    ) -> PendingShadow | None:
        """Ask now, so the answer is ready by the time the turn is done."""
        if not text.strip():
            return None
        pending = session.context.get("next_question")
        task = asyncio.create_task(
            self._model.decide(state=turn_state(session, text), questions=TURN_QUESTIONS)
        )
        return PendingShadow(
            task=task,
            message_id=message_id,
            session_id=session.id,
            user_id=session.user_id,
            flow_before=session.flow.value,
            step_before=session.step.value,
            pending_question=pending if isinstance(pending, str) else None,
            deadline=time.monotonic() + self._timeout,
        )

    async def finish(
        self,
        shadow: PendingShadow,
        *,
        intent: str,
        accepted: bool,
        used_llm: bool,
        next_step: str | None,
        now: datetime,
    ) -> None:
        """Store the model's answer next to the pipeline's. Never raises.

        Called after the turn is committed, so the user's reply is already on
        its way whatever happens here.
        """
        decisions, error = await self._await(shadow)
        try:
            if decisions is not None:
                # Budgeted like every other call: an unledgered model is a
                # bill the daily cap cannot see.
                await self._usage.append(
                    UsageEntry(
                        use_case=USE_CASE,
                        provider=self._provider,
                        model=decisions.model,
                        input_tokens=decisions.input_tokens,
                        output_tokens=0,
                        latency_ms=decisions.latency_ms,
                        cost_usd=decisions.cost_usd,
                        user_id=shadow.user_id,
                        conversation_id=shadow.session_id,
                        correlation_id=None,
                        occurred_at=now,
                    )
                )
            await self._log.record(
                ShadowDecision(
                    message_id=shadow.message_id,
                    session_id=shadow.session_id,
                    flow_before=shadow.flow_before,
                    step_before=shadow.step_before,
                    pending_question=shadow.pending_question,
                    decisions=decisions,
                    error=error,
                    pipeline_intent=intent,
                    pipeline_accepted=accepted,
                    pipeline_used_llm=used_llm,
                    pipeline_next_step=next_step,
                    occurred_at=now,
                )
            )
            await self._uow.commit()
        except Exception as exc:
            # The pilot's own bookkeeping failed. The turn is already
            # committed; losing one comparison row must not fail it.
            await self._uow.rollback()
            _logger.warning(
                "decision_shadow_not_recorded",
                message_id=str(shadow.message_id),
                error=type(exc).__name__,
            )

    @staticmethod
    def cancel(shadow: PendingShadow) -> None:
        """The turn itself failed: nothing to compare the answer with."""
        shadow.task.cancel()

    @staticmethod
    async def _await(shadow: PendingShadow) -> tuple[Decisions | None, str | None]:
        remaining = max(0.0, shadow.deadline - time.monotonic())
        try:
            return await asyncio.wait_for(shadow.task, timeout=remaining), None
        except TimeoutError:
            return None, "timeout"
        except DomainError as exc:
            return None, exc.message[:200]
        except Exception as exc:
            # A shadow must never take the worker down, whatever the adapter
            # raised; the type is enough to see it in the pilot's results.
            return None, type(exc).__name__
