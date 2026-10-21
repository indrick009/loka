"""Rewrite one platform sentence in the user's voice, just before it is sent.

Run by the outbound layer, after the turn has committed: the decision is
already recorded and staged, so the model call here holds no transaction and
cannot cost the user his reply. Any failure — no model, a provider down, a
rewrite that drops a number — sends the platform's own sentence instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import structlog

from loka.bounded_contexts.ai.application.ports import (
    AiUsageLedger,
    ConversationExchanges,
    ResponseModel,
    UsageEntry,
)
from loka.bounded_contexts.ai.application.services.conversation_voice import (
    build_voice_prompt,
    keeps_the_facts,
    numbers_in,
)
from loka.bounded_contexts.ai.domain.services.ai_provider import Prompt
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError

USE_CASE = "voice_platform_reply"


class VoicePlatformReplyUseCase:
    name = USE_CASE

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        model: ResponseModel,
        exchanges: ConversationExchanges,
        usage: AiUsageLedger,
        provider: str,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._model = model
        self._exchanges = exchanges
        self._usage = usage
        self._provider = provider
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
        message: str,
        platform_text: str,
        now: datetime,
    ) -> str:
        """The sentence to send: the rewrite when it holds, else the original."""
        exchanges = await self._exchanges.recent(session_id)
        # Released before the network call, as in the analysis.
        await self._uow.commit()
        already_said = (message, *(line.text for line in exchanges))
        prompt = build_voice_prompt(
            message=message, platform_text=platform_text, exchanges=exchanges
        )
        rewritten = await self._write(prompt, session_id=session_id, user_id=user_id, now=now)
        if rewritten is not None and not keeps_the_facts(
            platform_text, rewritten, already_said=already_said
        ):
            # Observed live: "ta photo" for "2 photos". One more try, told which
            # numbers to keep, before falling back to the stock sentence.
            self._logger.info("voice_retried", session_id=str(session_id))
            prompt = build_voice_prompt(
                message=message,
                platform_text=platform_text,
                exchanges=exchanges,
                keep_numbers=numbers_in(platform_text),
            )
            rewritten = await self._write(
                prompt, session_id=session_id, user_id=user_id, now=now
            )
        if rewritten is None or not keeps_the_facts(
            platform_text, rewritten, already_said=already_said
        ):
            self._logger.warning(
                "voice_rejected",
                session_id=str(session_id),
                rewritten=(rewritten or "")[:300],
            )
            return platform_text
        return rewritten

    async def _write(
        self,
        prompt: Prompt,
        *,
        session_id: uuid.UUID,
        user_id: uuid.UUID | None,
        now: datetime,
    ) -> str | None:
        """One model call, paid for in the ledger; ``None`` when it failed."""
        try:
            response = await self._model.generate_response(prompt)
        except DomainError as exc:
            self._logger.warning(
                "voice_failed", session_id=str(session_id), error=type(exc).__name__
            )
            return None
        usage = response.usage
        await self._usage.append(
            UsageEntry(
                use_case=USE_CASE,
                provider=self._provider,
                model=usage.model if usage is not None else "unknown",
                input_tokens=usage.input_tokens if usage is not None else 0,
                output_tokens=usage.output_tokens if usage is not None else 0,
                latency_ms=usage.latency_ms if usage is not None else 0,
                cost_usd=(
                    Decimal(str(usage.estimated_cost_usd)) if usage is not None else Decimal("0")
                ),
                user_id=user_id,
                conversation_id=session_id,
                correlation_id=None,
                occurred_at=now,
            )
        )
        await self._uow.commit()
        return response.content.strip()
