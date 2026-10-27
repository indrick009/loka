"""The conversation state as the prompts show it."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.application.ports import Exchange, TurnSummary
from loka.bounded_contexts.conversation.application.services.prompts.topics import (
    question_topic,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction import (
    PROPERTY_REQUIREMENTS,
    SEARCH_REQUIREMENTS,
    question_key_for,
)

# Keys the pipeline keeps in the session for its own use, never for the model:
# leaking "next_question" into known_facts invites the model to treat an internal
# variable as a fact about the property.
_PROMPT_INTERNAL_KEYS = frozenset({"next_question", "abort_reason"})


def public_known_facts(known_facts: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value
        for name, value in known_facts.items()
        if name not in _PROMPT_INTERNAL_KEYS and not str(name).startswith("_")
    }


def _missing_fields(flow: FlowName, known: dict[str, Any]) -> list[dict[str, str]]:
    """The still-missing form fields, in order, glossed for this flow.

    Making the model decide the next question is expensive in tokens *and*
    fragile; handing it only the remaining fields lets it do both jobs (read the
    message, ask the next question) while the code still owns the order. Each
    flow walks its own form: a tenant is never shown the landlord's charges and
    deposit as fields still to come.
    """
    requirements = (
        SEARCH_REQUIREMENTS if flow is FlowName.PROPERTY_SEARCH else PROPERTY_REQUIREMENTS
    )
    fields: list[dict[str, str]] = []
    for name, step in requirements:
        if name in known:
            continue
        fields.append(
            {"field": name, "ask": question_topic(question_key_for(step, flow), flow) or ""}
        )
    return fields


def _turns_as_json(history: list[TurnSummary]) -> list[dict[str, Any]]:
    return [
        {
            "direction": turn.direction,
            "intent": turn.intent,
            "confidence": round(turn.confidence, 2),
            "used_llm": turn.used_llm,
        }
        for turn in history
    ]


def exchanges_as_json(exchanges: list[Exchange]) -> list[dict[str, str]]:
    """The thread as the model reads it: who said what, oldest first."""
    return [{"from": line.speaker, "text": line.text} for line in exchanges]
