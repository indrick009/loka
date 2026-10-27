"""The prompt that reads one message."""

from __future__ import annotations

import json
from typing import Any

from loka.bounded_contexts.conversation.application.ports import Exchange, TurnSummary
from loka.bounded_contexts.conversation.application.services.prompts.persona import (
    PRICE_REFERENCES_XAF,
)
from loka.bounded_contexts.conversation.application.services.prompts.schema import (
    RESPONSE_SCHEMA,
)
from loka.bounded_contexts.conversation.application.services.prompts.state import (
    _missing_fields,
    _turns_as_json,
    exchanges_as_json,
    public_known_facts,
)
from loka.bounded_contexts.conversation.application.services.prompts.system import (
    SYSTEM_PROMPT,
)
from loka.bounded_contexts.conversation.application.services.prompts.topics import (
    _FIELD_FOR_QUESTION,
    _FLOW_ROLES,
    question_topic,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.conversation.domain.services.ai_provider import (
    SUPPORTED_LANGUAGES,
    Prompt,
)


def build_analysis_prompt(
    *,
    text: str | None,
    default_language: str,
    history: list[TurnSummary],
    flow: FlowName,
    step: FlowStep,
    pending_question: str | None,
    known_facts: dict[str, Any],
    exchanges: list[Exchange] | None = None,
) -> Prompt:
    """Assemble the request, with the state of the conversation as context.

    The context is what turns "Bastos" from noise into an answer: the model can
    see that it is replying to the location question of an open listing, and
    fill the matching entity instead of guessing an intent from three words.

    The phone number is never part of the prompt: the model has no business
    knowing who it is talking to, and prompts end up in vendor logs.
    """
    public_known = public_known_facts(known_facts)
    payload: dict[str, Any] = {
        "message": text or "",
        # The language of the *message* is the model's to read; this is only the
        # stand-in it keeps when the message is too short to tell.
        "default_language": (
            default_language if default_language in SUPPORTED_LANGUAGES else "fr"
        ),
        "you_are_helping": _FLOW_ROLES.get(flow, _FLOW_ROLES[FlowName.SUPPORT]),
        "conversation": {
            "flow": flow.value,
            "step": step.value,
            "awaiting_answer_to": pending_question,
            "awaiting_question": question_topic(pending_question, flow),
            "awaiting_field": _FIELD_FOR_QUESTION.get(pending_question or ""),
            "known_facts": public_known,
        },
        # Only what is still missing, in order, so the model asks the next real
        # question without re-reading fields already collected. The schema closes
        # the intent enum, so a second copy of the allowlist would be dead bytes.
        # No task yet, no form: shown the landlord's fields, a "bonjour"
        # was answered "quel type de propriété tu veux proposer ?".
        "required_fields": (
            [] if flow is FlowName.SUPPORT else _missing_fields(flow, public_known)
        ),
        "schema": RESPONSE_SCHEMA,
    }
    # The price band anchors the magnitude of a bare number; once the rent is
    # known it is context that can never be used again, so it is dropped.
    if not public_known.get("rent"):
        payload["price_reference_monthly_xaf"] = {
            name: {"min": low, "max": high}
            for name, (low, high) in PRICE_REFERENCES_XAF.items()
        }
    turns = _turns_as_json(history)
    if turns:
        payload["recent_turns"] = turns
    if exchanges:
        payload["recent_messages"] = exchanges_as_json(exchanges)
    return Prompt(
        system=SYSTEM_PROMPT,
        user=json.dumps(payload, ensure_ascii=False),
        response_schema=RESPONSE_SCHEMA,
    )
