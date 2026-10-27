"""The prompt that writes the reply from the turn's outcome."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from loka.bounded_contexts.ai.application.ports import Exchange, TurnSummary
from loka.bounded_contexts.ai.application.services.prompts.state import (
    _turns_as_json,
    exchanges_as_json,
    public_known_facts,
)
from loka.bounded_contexts.ai.application.services.prompts.system import (
    REPLY_SYSTEM_PROMPT,
)
from loka.bounded_contexts.ai.application.services.prompts.topics import (
    _FLOW_ROLES,
    question_topic,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import (
    SUPPORTED_LANGUAGES,
    Prompt,
)
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
)


def build_reply_prompt(
    *,
    text: str,
    language: str,
    intent: str,
    accepted: bool,
    accepted_facts: Mapping[str, Any],
    next_key: str | None,
    history: list[TurnSummary],
    flow: FlowName,
    known_facts: dict[str, Any],
    exchanges: list[Exchange] | None = None,
) -> Prompt:
    """Ask the model to write the sentence this turn sends back.

    Only accepted facts are shown, and the next question is described in plain
    words rather than by its internal key: the model tailors the phrasing, it
    does not get to decide what is asked — the code already did. The flow is
    passed too, so the wording matches the side of the conversation: a landlord
    is asked the rent they *want*, a tenant the rent they *seek*.

    The turn's outcome arrives as three plain values rather than as the use
    case's verdict object: taking the object would make this module import the
    state machine that imports it, and a prompt builder has no business
    knowing that type.
    """
    return Prompt(
        system=REPLY_SYSTEM_PROMPT,
        user=json.dumps(
            {
                "message": text,
                "language": language if language in SUPPORTED_LANGUAGES else "fr",
                "you_are_helping": _FLOW_ROLES.get(flow, _FLOW_ROLES[FlowName.SUPPORT]),
                "understood": {
                    "intent": intent,
                    "accepted": accepted,
                    # Everything the session holds, not only what this message
                    # produced. A writer given one turn's extraction truthfully
                    # reports having no memory, then invents a number to fill
                    # the gap — and it is also how it asks for a field that was
                    # answered three turns ago. This turn's facts win, because a
                    # correction is the one case where they disagree.
                    "known_facts": {
                        **public_known_facts(known_facts),
                        **(dict(accepted_facts) if accepted else {}),
                    },
                },
                # None means "the message was not understood": ask for a rephrase
                # rather than posing a question the platform never reached.
                "ask_about": question_topic(next_key, flow),
                "recent_turns": _turns_as_json(history),
                "recent_messages": exchanges_as_json(exchanges or []),
            },
            ensure_ascii=False,
        ),
        temperature=0.4,
        max_output_tokens=256,
    )
