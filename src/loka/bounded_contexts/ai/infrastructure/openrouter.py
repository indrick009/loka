"""OpenRouter adapter for intent understanding.

Everything vendor-shaped stops here: the domain asks for an ``IntentResult`` and
learns nothing about chat completions or OpenRouter's error shapes. The client
is injectable so tests drive it through ``httpx.MockTransport`` — no test in
this repository reaches the network.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import Any

import httpx

from loka.bounded_contexts.ai.domain.services.ai_provider import (
    IntentResult,
    LlmUsage,
    Prompt,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import AISettings

log = logging.getLogger(__name__)

RETRYABLE_STATUS = frozenset({408, 500, 502, 503, 504})
_MAX_ERROR_BODY = 400


class OpenRouterIntentModel:
    """Understands an intent through OpenRouter's chat completions API."""

    def __init__(
        self,
        settings: AISettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._default = settings.default_model
        self._fallback = settings.fallback_model

    @property
    def model(self) -> str:
        return self._default

    async def understand_intent(self, prompt: Prompt) -> IntentResult:
        api_key = self._settings.api_key.get_secret_value()
        if not api_key:
            # A missing key is a deployment mistake, not a transient failure:
            # raising it here avoids spending a retry budget on a doomed call.
            raise ExternalServiceUnavailable(
                "OpenRouter API key is not configured",
                context={"provider": self._settings.provider},
            )

        answer = await self._complete(prompt, model=self._default, api_key=api_key)
        if answer is None and self._fallback != self._default:
            log.warning("openrouter_primary_model_failed", extra={"model": self._default})
            answer = await self._complete(prompt, model=self._fallback, api_key=api_key)
        if answer is None:
            raise ExternalServiceUnavailable(
                "no OpenRouter model could answer the prompt",
                context={"models": [self._default, self._fallback]},
            )

        model, content, usage, latency_ms = answer
        return replace(
            IntentResult.from_structured(_parse_json_object(content)),
            usage=self._usage(model, usage, latency_ms),
        )

    async def _complete(
        self, prompt: Prompt, *, model: str, api_key: str
    ) -> tuple[str, str, dict[str, Any], int] | None:
        """One attempt. ``None`` means "retryable, try the next model"."""
        client, owns_client = self._client_for()
        started = time.monotonic()
        try:
            response = await client.post(
                "/chat/completions",
                json=self._body(prompt, model),
                headers=self._headers(api_key),
            )
        except httpx.HTTPError as exc:
            log.warning("openrouter_transport_error", extra={"error": str(exc)})
            return None
        finally:
            if owns_client:
                await client.aclose()

        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code == 429:
            # Raised, not swallowed: a rate limit means the caller must slow
            # down. Swallowing it would turn "we are being throttled" into a
            # confident "UNKNOWN" and then a wrong question to the landlord.
            raise RateLimited(
                "OpenRouter rate limit reached", context={"model": model}
            )
        if response.status_code in RETRYABLE_STATUS:
            log.warning(
                "openrouter_retryable_status",
                extra={"status": response.status_code, "model": model},
            )
            return None
        if response.is_error:
            raise ExternalServiceUnavailable(
                "OpenRouter rejected the request",
                context={
                    "status": response.status_code,
                    "model": model,
                    "body": response.text[:_MAX_ERROR_BODY],
                },
            )

        content, usage = self._read(response, model=model)
        return model, content, usage, latency_ms

    def _client_for(self) -> tuple[httpx.AsyncClient, bool]:
        if self._client is not None:
            return self._client, False
        return (
            httpx.AsyncClient(
                base_url=self._settings.base_url,
                timeout=self._settings.request_timeout_seconds,
            ),
            True,
        )

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # OpenRouter attributes usage by these headers; without them the
            # vendor bill cannot be reconciled against our own ledger.
            "HTTP-Referer": "https://loka.cm",
            "X-Title": "Loka",
        }

    @staticmethod
    def _read(response: httpx.Response, *, model: str) -> tuple[str, dict[str, Any]]:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceUnavailable(
                "OpenRouter returned an unreadable payload",
                context={"model": model, "error": str(exc)},
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ExternalServiceUnavailable(
                "OpenRouter returned an empty completion", context={"model": model}
            )
        usage = payload.get("usage")
        return content, usage if isinstance(usage, dict) else {}

    def _usage(self, model: str, usage: dict[str, Any], latency_ms: int) -> LlmUsage:
        input_tokens = int(usage.get("prompt_tokens") or 0)
        output_tokens = int(usage.get("completion_tokens") or 0)
        pricing = self._settings.pricing_for(model)
        if pricing is None:
            # In production composition refuses to enable the pipeline without
            # pricing, so this is unreachable outside tests. It records zero
            # rather than a guess: a fabricated price would silently corrupt
            # every budget that reads this back.
            log.error("openrouter_missing_pricing", extra={"model": model})
            cost = 0.0
        else:
            cost = (input_tokens * pricing.input + output_tokens * pricing.output) / 1_000_000
        return LlmUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_usd=cost,
            model=model,
        )

    def _body(self, prompt: Prompt, model: str) -> dict[str, Any]:
        return {
            "model": model,
            "temperature": prompt.temperature,
            "max_tokens": prompt.max_output_tokens,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            # Plain JSON mode rather than a vendor schema: the shape is already
            # enforced twice downstream (the prompt, then the value objects), so
            # a stricter contract here would only add a failure mode.
            "response_format": {"type": "json_object"},
            **prompt.extra,
        }


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        # Some models fence their JSON even when told not to.
        text = text.strip("`")
        _, _, text = text.partition("\n")
        text = text.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ExternalServiceUnavailable(
            "model completion was not valid JSON", context={"error": str(exc)}
        ) from exc
    if not isinstance(payload, dict):
        raise ExternalServiceUnavailable("model completion was not a JSON object")
    return payload
