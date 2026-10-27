"""The values one analysed turn is made of, passed between the pipeline's parts."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from loka.bounded_contexts.ai.domain.services.fact_extraction import ExtractedFacts
from loka.bounded_contexts.messaging.domain.entities.conversation_session import FlowStep

USE_CASE = "analyse_conversation_message"


@dataclass(frozen=True, slots=True)
class AnalysisCommand:
    message_id: uuid.UUID
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """What the pipeline concluded, for logs, metrics and the caller."""

    session_id: uuid.UUID
    message_id: uuid.UUID
    intent: str
    confidence: float
    used_llm: bool
    accepted: bool
    reason: str | None
    facts: dict[str, Any] = field(default_factory=dict)
    rejected_facts: dict[str, str] = field(default_factory=dict)
    next_step: str | None = None
    question_key: str | None = None
    cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the domain accepted from one proposed intent."""

    intent: str
    facts: ExtractedFacts
    accepted: bool
    reason: str | None
    next_step: FlowStep | None


@dataclass(frozen=True, slots=True)
class TurnCost:
    """How a turn was read and what reading it cost, recorded whatever it decided."""

    message_id: uuid.UUID
    confidence: float
    used_llm: bool
    latency_ms: int
    cost_usd: Decimal
    now: datetime


@dataclass(frozen=True, slots=True)
class Reading:
    """A message read without a model: what to validate and how to answer it.

    ``reply_text`` is platform text when the turn has a fact of its own to
    report, such as a photo just received.
    """

    intent: str
    confidence: float
    entities: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    reply_text: str | None = None
    media_burst_of: uuid.UUID | None = None
    media_marker: str | None = None
    settles_on: FlowStep | None = None


@dataclass(frozen=True, slots=True)
class Settled:
    """A turn a rule answered on its own: where it lands and its one sentence."""

    next_step: FlowStep
    question_key: str | None
    reply: str
    intent: str
    clear_unlock: bool = False


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """Where an accepted turn moved the conversation, and what to answer."""

    question: str | None
    next_step: FlowStep | None
    reply_text: str | None
    platform_text: str | None
