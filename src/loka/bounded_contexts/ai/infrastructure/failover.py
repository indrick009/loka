"""OpenRouter first, OmniRoute's free models when OpenRouter cannot answer.

Observed live: an exhausted OpenRouter credit (HTTP 402) sent every turn to the
rules lane for the rest of the day. The backup is the same OpenAI-compatible
adapter aimed at a self-hosted OmniRoute, whose ``auto`` combo falls through
free providers. It is a spare: slower, rate-limited, unpriced — so it is asked
only after OpenRouter failed, and OpenRouter is skipped for a cooldown after a
failure instead of being retried, and waited on, for every message.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import SecretStr

from loka.bounded_contexts.ai.domain.ai_provider import IntentResult, LlmResponse, Prompt
from loka.bounded_contexts.ai.infrastructure.openrouter import OpenRouterIntentModel
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import AISettings, ModelPricing
from loka.shared.infrastructure.logging import get_logger

log = get_logger(__name__)

# Per process, not per message: the use case and its model are built per turn,
# but "OpenRouter is down" is true for every turn of the next two minutes.
_primary_down_until: dict[str, float] = {}


def backup_settings(ai: AISettings) -> AISettings:
    """The same adapter's settings, pointed at OmniRoute's free combo."""
    key = ai.backup_api_key.get_secret_value()
    return ai.model_copy(
        update={
            "provider": "omniroute",
            "base_url": ai.backup_base_url,
            # OmniRoute answers without a key unless one is required; the
            # adapter refuses an empty key, so a placeholder stands in.
            "api_key": SecretStr(key or "omniroute"),
            "default_model": ai.backup_model,
            "fallback_model": ai.backup_model,
            "request_timeout_seconds": ai.backup_timeout_seconds,
            # Free: recorded at zero rather than logged as unpriced each turn.
            "model_pricing_usd_per_million": {
                ai.backup_model: ModelPricing.model_construct(input=0.0, output=0.0)
            },
        }
    )


class FailoverIntentModel:
    """Both model ports, with a backup taking over when the primary fails."""

    def __init__(
        self, primary: OpenRouterIntentModel, backup: OpenRouterIntentModel, *, cooldown: float
    ) -> None:
        self._primary = primary
        self._backup = backup
        self._cooldown = cooldown

    @property
    def model(self) -> str:
        return self._primary.model

    async def understand_intent(self, prompt: Prompt) -> IntentResult:
        result: IntentResult = await self._ask("understand_intent", prompt)
        return result

    async def generate_response(self, prompt: Prompt) -> LlmResponse:
        result: LlmResponse = await self._ask("generate_response", prompt)
        return result

    async def _ask(self, method: str, prompt: Prompt) -> Any:
        key = self._primary.model
        if time.monotonic() >= _primary_down_until.get(key, 0.0):
            try:
                return await getattr(self._primary, method)(prompt)
            except (ExternalServiceUnavailable, RateLimited) as exc:
                _primary_down_until[key] = time.monotonic() + self._cooldown
                log.warning(
                    "ai_primary_down_using_backup",
                    extra={"error": str(exc), "cooldown_s": self._cooldown},
                )
        try:
            return await getattr(self._backup, method)(prompt)
        except RateLimited as exc:
            # The use case reads "no model" as ExternalServiceUnavailable and
            # falls to the rules lane; a free tier's 429 is exactly that.
            raise ExternalServiceUnavailable(
                "backup model is rate-limited", context={"error": str(exc)}
            ) from exc
