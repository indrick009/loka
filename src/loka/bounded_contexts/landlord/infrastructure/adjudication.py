"""Adjudication adapters: a vision model and the evidence URLs it reads.

The model is asked for a single structured verdict over the two images. It never
receives object keys or bytes — only short-lived pre-signed URLs, so a leaked
prompt leaks nothing durable. Any transport or parse failure surfaces as
``ExternalServiceUnavailable`` and the use case falls back to manual review.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from loka.bounded_contexts.landlord.application.ports import (
    VerificationEvidence,
)
from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    Adjudication,
    AdjudicationDecision,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import AISettings
from loka.shared.infrastructure.logging import get_logger
from loka.shared.infrastructure.object_storage.media import MediaStorage

log = get_logger(__name__)

RETRYABLE_STATUS = frozenset({408, 500, 502, 503, 504})
_MAX_ERROR_BODY = 400

_SYSTEM_PROMPT = (
    "You are an identity reviewer for a Cameroonian rental marketplace. "
    "You are shown a government identity document and a selfie. Decide whether "
    "they clearly belong to the same person and whether the document looks "
    "genuine and unexpired. Answer ONLY with a JSON object: "
    '{"decision": "APPROVE" | "REJECT" | "UNCERTAIN", "confidence": a number '
    'between 0 and 1, "reason": a short explanation}. '
    "Use UNCERTAIN whenever you cannot be sure; a human will review those."
)


class MediaEvidenceUrlProvider:
    """Pre-signs identity evidence with the short TTL identity documents use."""

    def __init__(self, storage: MediaStorage, *, ttl_seconds: int) -> None:
        self._storage = storage
        self._ttl_seconds = ttl_seconds

    async def url_for(self, object_key: str) -> str:
        return await self._storage.presigned_download_url(
            object_key, ttl_seconds=self._ttl_seconds
        )


class OpenRouterVerificationAdjudicator:
    """Vision-model adjudicator over OpenRouter's chat completions API."""

    def __init__(
        self,
        settings: AISettings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._client = client

    async def adjudicate(self, evidence: VerificationEvidence) -> Adjudication:
        api_key = self._settings.api_key.get_secret_value()
        if not api_key:
            raise ExternalServiceUnavailable(
                "OpenRouter API key is not configured",
                context={"provider": self._settings.provider},
            )
        client, owns_client = self._client_for()
        started = time.monotonic()
        try:
            response = await client.post(
                "/chat/completions",
                json=self._body(evidence),
                headers=self._headers(api_key),
            )
        except httpx.HTTPError as exc:
            raise ExternalServiceUnavailable(
                "OpenRouter transport error", context={"error": str(exc)}
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code == 429:
            raise RateLimited(
                "OpenRouter rate limit reached",
                context={"model": self._settings.default_model},
            )
        if response.status_code in RETRYABLE_STATUS:
            raise ExternalServiceUnavailable(
                "OpenRouter returned a retryable status",
                context={"status": response.status_code},
            )
        if response.is_error:
            raise ExternalServiceUnavailable(
                "OpenRouter rejected the request",
                context={
                    "status": response.status_code,
                    "body": response.text[:_MAX_ERROR_BODY],
                },
            )
        adjudication = _parse_adjudication(self._read(response))
        log.info(
            "landlord_adjudication_produced",
            request_id=str(evidence.request_id),
            decision=adjudication.decision.value,
            confidence=adjudication.confidence,
            latency_ms=latency_ms,
        )
        return adjudication

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

    def _headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://loka.cm",
            "X-Title": "Loka",
        }

    def _body(self, evidence: VerificationEvidence) -> dict[str, Any]:
        return {
            "model": self._settings.default_model,
            "temperature": 0.0,
            "max_tokens": self._settings.max_output_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Document type: {evidence.document_kind.value}. "
                                "Compare the document photo with the selfie."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": evidence.document_url},
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": evidence.selfie_url},
                        },
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
        }

    def _read(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ExternalServiceUnavailable(
                "OpenRouter returned an unreadable payload", context={"error": str(exc)}
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ExternalServiceUnavailable("OpenRouter returned an empty completion")
        return content


def _parse_adjudication(content: str) -> Adjudication:
    payload = _parse_json_object(content)
    raw_decision = str(payload.get("decision", "UNCERTAIN")).upper()
    try:
        decision = AdjudicationDecision(raw_decision)
    except ValueError:
        decision = AdjudicationDecision.UNCERTAIN
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError) as exc:
        raise ExternalServiceUnavailable(
            "adjudicator returned a non-numeric confidence"
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise ExternalServiceUnavailable(
            "adjudicator returned an out-of-range confidence",
            context={"confidence": confidence},
        )
    reason = str(payload.get("reason", "")).strip() or "no reason given"
    return Adjudication(decision=decision, confidence=confidence, reason=reason)


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        _, _, text = text.partition("\n")
        text = text.rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise ExternalServiceUnavailable(
            "adjudicator completion was not valid JSON", context={"error": str(exc)}
        ) from exc
    if not isinstance(payload, dict):
        raise ExternalServiceUnavailable("adjudicator completion was not a JSON object")
    return payload


__all__ = ["MediaEvidenceUrlProvider", "OpenRouterVerificationAdjudicator"]