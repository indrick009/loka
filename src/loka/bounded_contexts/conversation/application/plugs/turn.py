"""One turn handed to a plugged domain, and what it may do with the conversation."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
    FlowStep,
)

if TYPE_CHECKING:
    from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
        TurnOutcome,
    )


@dataclass(frozen=True, slots=True)
class PlugTurn:
    """What the engine understood. ``known`` is the task's facts with this
    turn's merged on top; ``fresh`` says the turn opens a new task."""

    session: ConversationSession
    flow: FlowName
    intent: str
    facts: dict[str, Any]
    known: dict[str, Any]
    fresh: bool
    text: str | None
    now: datetime
    model_reply: str | None = None
    open_field: str | None = None
    rejected: dict[str, str] = field(default_factory=dict)

    @property
    def language(self) -> str:
        return self.session.language


@dataclass(frozen=True, slots=True)
class Steer:
    """Put another user's thread on a question, so their answer comes back here."""

    flow: FlowName
    step: FlowStep
    question_key: str | None
    context: dict[str, Any] = field(default_factory=dict)


class PlugDesk(Protocol):
    """What a domain may do to conversations: move this one, write to another."""

    async def settle(
        self,
        turn: PlugTurn,
        *,
        step: FlowStep,
        question_key: str | None,
        context: dict[str, Any] | None = None,
    ) -> None: ...

    async def notify(
        self,
        user_id: uuid.UUID,
        text: str,
        *,
        now: datetime,
        steer: Steer | None = None,
        media_object_key: str | None = None,
        delay_seconds: float = 0,
    ) -> bool: ...

    async def notify_phone(self, phone: str, text: str, *, now: datetime) -> bool: ...


class DomainTurns(Protocol):
    """A photo reaches ``apply`` too, as the facts ``media_key`` and ``media_kind``."""

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome: ...
