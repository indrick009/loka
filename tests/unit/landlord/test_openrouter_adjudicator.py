"""The OpenRouter adjudicator: one request shape in, one verdict out."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from loka.bounded_contexts.landlord.application.ports import VerificationEvidence
from loka.bounded_contexts.landlord.domain.entities.verification_request import DocumentKind
from loka.bounded_contexts.landlord.domain.services.verification_adjudication import (
    AdjudicationDecision,
)
from loka.bounded_contexts.landlord.infrastructure.adjudication import (
    OpenRouterVerificationAdjudicator,
)
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import AISettings

pytestmark = pytest.mark.unit

EVIDENCE = VerificationEvidence(
    request_id=uuid.uuid4(),
    landlord_id=uuid.uuid4(),
    document_kind=DocumentKind.NATIONAL_ID_CARD,
    document_url="https://signed.example/doc",
    selfie_url="https://signed.example/selfie",
    risk_score=5,
)


def _settings() -> AISettings:
    return AISettings(api_key="test-key", default_model="google/gemini-2.5-flash")


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1"
    )


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}], "usage": {}},
    )


async def test_a_json_verdict_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # The two images must travel as image_url parts, not as prose.
        parts = body["messages"][1]["content"]
        assert [part["type"] for part in parts] == ["text", "image_url", "image_url"]
        return _completion(
            json.dumps({"decision": "APPROVE", "confidence": 0.94, "reason": "same face"})
        )

    adjudicator = OpenRouterVerificationAdjudicator(_settings(), client=_client(handler))
    result = await adjudicator.adjudicate(EVIDENCE)

    assert result.decision is AdjudicationDecision.APPROVE
    assert result.confidence == 0.94


async def test_an_unknown_decision_is_uncertain_not_approved() -> None:
    adjudicator = OpenRouterVerificationAdjudicator(
        _settings(),
        client=_client(lambda _: _completion('{"decision": "MAYBE", "confidence": 0.9}')),
    )

    result = await adjudicator.adjudicate(EVIDENCE)

    assert result.decision is AdjudicationDecision.UNCERTAIN


async def test_a_rate_limit_is_raised_for_the_caller_to_back_off() -> None:
    adjudicator = OpenRouterVerificationAdjudicator(
        _settings(), client=_client(lambda _: httpx.Response(429))
    )

    with pytest.raises(RateLimited):
        await adjudicator.adjudicate(EVIDENCE)


async def test_unparseable_output_is_a_service_failure() -> None:
    adjudicator = OpenRouterVerificationAdjudicator(
        _settings(), client=_client(lambda _: _completion("not json at all"))
    )

    with pytest.raises(ExternalServiceUnavailable):
        await adjudicator.adjudicate(EVIDENCE)


async def test_a_missing_api_key_fails_fast() -> None:
    adjudicator = OpenRouterVerificationAdjudicator(
        AISettings(api_key=""), client=_client(lambda _: _completion("{}"))
    )

    with pytest.raises(ExternalServiceUnavailable):
        await adjudicator.adjudicate(EVIDENCE)