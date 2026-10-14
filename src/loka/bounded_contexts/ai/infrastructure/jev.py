"""Jev, TypeSafe's decision model, reached through OpenRouter.

Everything vendor-shaped stops here: the application asks a ``DecisionModel``
for typed choices and learns nothing about ``/systemone`` or its answer shape.
OpenRouter serves Jev with the same key Kora already uses, at
``{base_url}/systemone``. The client is injectable so tests drive it through
``httpx.MockTransport`` — no test in this repository reaches the network.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import httpx

from loka.bounded_contexts.ai.application.ports import (
    ChoiceAnswer,
    ChoiceQuestion,
    Decisions,
)
from loka.shared.domain.errors import ExternalServiceUnavailable
from loka.shared.infrastructure.config.settings import AISettings

_MAX_ERROR_BODY = 300


@functools.lru_cache(maxsize=4)
def _pooled_client(base_url: str, timeout: float) -> httpx.AsyncClient:
    """One client per process, so each message does not pay a TLS handshake."""
    return httpx.AsyncClient(base_url=base_url, timeout=timeout)


class JevDecisionModel:
    def __init__(
        self, settings: AISettings, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._settings = settings
        self._client = client

    @property
    def model(self) -> str:
        return self._settings.decision_model

    async def decide(
        self, *, state: Mapping[str, Any], questions: Mapping[str, ChoiceQuestion]
    ) -> Decisions:
        api_key = self._settings.api_key.get_secret_value()
        if not api_key:
            raise ExternalServiceUnavailable(
                "no API key is configured for the decision model",
                context={"model": self.model},
            )
        body = {
            "model": self.model,
            "state": dict(state),
            "questions": {
                name: {
                    "type": "choice",
                    "instructions": question.instructions,
                    "criteria": dict(question.options),
                }
                for name, question in questions.items()
            },
        }
        started = time.monotonic()
        try:
            response = await self._http().post(
                "/systemone",
                json=body,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceUnavailable(
                "the decision model could not be reached",
                context={"model": self.model, "error_type": type(exc).__name__},
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code >= 300:
            raise ExternalServiceUnavailable(
                "the decision model refused the request",
                context={
                    "model": self.model,
                    "status": response.status_code,
                    "body": response.text[:_MAX_ERROR_BODY],
                },
            )
        return _decisions(response, latency_ms=latency_ms, fallback_model=self.model)

    def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return _pooled_client(
            self._settings.base_url.rstrip("/"), self._settings.decision_timeout_seconds
        )


def _decisions(response: httpx.Response, *, latency_ms: int, fallback_model: str) -> Decisions:
    try:
        payload = response.json()
        answers = {
            name: ChoiceAnswer(
                choice=str(answer["choice"]),
                confidence=float(answer.get("confidence") or 0.0),
                probabilities={
                    str(option): float(probability)
                    for option, probability in (answer.get("probabilities") or {}).items()
                },
            )
            for name, answer in payload["answers"].items()
        }
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ExternalServiceUnavailable(
            "the decision model answered in an unexpected shape",
            context={"body": response.text[:_MAX_ERROR_BODY]},
        ) from exc
    usage = payload.get("usage") or {}
    return Decisions(
        answers=answers,
        model=str(payload.get("model") or fallback_model),
        input_tokens=int(usage.get("input_tokens") or 0),
        latency_ms=latency_ms,
        # OpenRouter states the cost of the call; trusting it beats a price
        # table that would have to follow every Jev release.
        cost_usd=Decimal(str(usage.get("cost") or 0)),
    )


__all__ = ["JevDecisionModel"]
