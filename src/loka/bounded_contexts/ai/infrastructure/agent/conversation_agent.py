"""The ``ConversationAgent`` port, answered by the Pydantic AI tenant search."""

from __future__ import annotations

import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from loka.bounded_contexts.ai.infrastructure.agent.models import openrouter_model
from loka.bounded_contexts.ai.infrastructure.agent.tenant_search import TenantSearchAgent
from loka.bounded_contexts.conversation.application.ports import AgentAnswer, Exchange
from loka.shared.infrastructure.config.settings import AISettings


def as_history(thread: list[Exchange]) -> list[ModelMessage]:
    """What was said on WhatsApp, as the turns the agent remembers."""
    # A list and its photos are several WhatsApp lines but one turn: lines of
    # one speaker are joined, and the history opens on the user's words.
    turns: list[tuple[str, str]] = []
    for line in thread:
        speaker = "kora" if line.speaker == "kora" else "user"
        if turns and turns[-1][0] == speaker:
            turns[-1] = (speaker, f"{turns[-1][1]}\n{line.text}")
        elif turns or speaker == "user":
            turns.append((speaker, line.text))
    return [
        ModelResponse(parts=[TextPart(content=said)])
        if speaker == "kora"
        else ModelRequest(parts=[UserPromptPart(content=said)])
        for speaker, said in turns
    ]


class PydanticTenantSearchAgent:
    def __init__(self, settings: AISettings) -> None:
        self._settings = settings
        self._model = settings.default_model
        self._agent = TenantSearchAgent(openrouter_model(settings, self._model))

    async def answer(
        self,
        text: str,
        *,
        facts: Mapping[str, Any],
        thread: list[Exchange],
        language: str,
    ) -> AgentAnswer:
        started = time.monotonic()
        turn = await self._agent.reply(
            text, facts=dict(facts), history=as_history(thread), language=language, submit=True
        )
        return AgentAnswer(
            act=turn.act,
            reply=turn.reply,
            facts=turn.facts,
            language=turn.language,
            submitted=turn.submitted,
            model=self._model,
            input_tokens=turn.input_tokens,
            output_tokens=turn.output_tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=self._cost(turn.input_tokens, turn.output_tokens),
        )

    def _cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        pricing = self._settings.pricing_for(self._model)
        if pricing is None:
            return Decimal("0")
        dollars = (input_tokens * pricing.input + output_tokens * pricing.output) / 1_000_000
        return Decimal(str(round(dollars, 6)))
