"""The OpenRouter adapter, driven through ``httpx.MockTransport``.

No test in this repository touches the network. The point of these cases is the
failure behaviour: a provider that lies, stalls, throttles or answers with prose
must produce a typed error, never a fabricated answer.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from loka.bounded_contexts.ai.domain.services.ai_provider import Prompt
from loka.bounded_contexts.ai.infrastructure.openrouter import OpenRouterIntentModel
from loka.shared.domain.errors import ExternalServiceUnavailable, RateLimited
from loka.shared.infrastructure.config.settings import AISettings, ModelPricing

pytestmark = pytest.mark.unit

FAST = "google/gemini-2.5-flash"
SLOW = "google/gemini-2.5-pro"
SCHEMA: dict[str, Any] = {"type": "object"}


def settings(**overrides: Any) -> AISettings:
    base: dict[str, Any] = {
        "api_key": "test-key",
        "default_model": FAST,
        "fallback_model": SLOW,
        "model_pricing_usd_per_million": {
            FAST: ModelPricing(input=0.30, output=2.50),
            SLOW: ModelPricing(input=1.25, output=10.00),
        },
    }
    base.update(overrides)
    return AISettings(**base)


def prompt() -> Prompt:
    return Prompt(system="s", user="u", response_schema=SCHEMA)


def completion(payload: dict[str, Any], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


def chat(
    content: str, *, prompt_tokens: int = 1000, completion_tokens: int = 500
) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
    }


def model_for(
    handler: Any, config: AISettings | None = None
) -> tuple[OpenRouterIntentModel, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    client = httpx.AsyncClient(
        base_url=(config or settings()).base_url, transport=transport
    )
    return OpenRouterIntentModel(config or settings(), client=client), seen


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


class TestSuccess:
    async def test_a_json_completion_is_parsed(self) -> None:
        payload = json.dumps(
            {
                "intent": "CREATE_PROPERTY",
                "confidence": 0.91,
                "entities": {"property_type": "APARTMENT"},
            }
        )
        model, seen = model_for(lambda _: completion(chat(payload)))

        result = await model.understand_intent(prompt())

        assert result.intent == "CREATE_PROPERTY"
        assert result.confidence == 0.91
        assert result.entities == {"property_type": "APARTMENT"}
        assert len(seen) == 1

    async def test_the_call_is_costed_from_configured_pricing(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.9, "entities": {}})
        model, _ = model_for(lambda _: completion(chat(payload)))

        result = await model.understand_intent(prompt())

        # 1000 input * 0.30 + 500 output * 2.50, per million.
        assert result.usage is not None
        assert result.usage.input_tokens == 1000
        assert result.usage.output_tokens == 500
        assert result.usage.estimated_cost_usd == pytest.approx(0.00155)
        assert result.usage.model == FAST

    async def test_latency_is_measured(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.9, "entities": {}})
        model, _ = model_for(lambda _: completion(chat(payload)))

        result = await model.understand_intent(prompt())

        assert result.usage is not None
        assert result.usage.latency_ms >= 0

    async def test_the_api_key_and_attribution_headers_are_sent(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.9, "entities": {}})
        model, seen = model_for(lambda _: completion(chat(payload)))

        await model.understand_intent(prompt())

        assert seen[0].headers["authorization"] == "Bearer test-key"
        assert seen[0].headers["x-title"] == "Loka"

    async def test_json_mode_is_requested(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.9, "entities": {}})
        model, seen = model_for(lambda _: completion(chat(payload)))

        await model.understand_intent(prompt())

        assert body_of(seen[0])["response_format"] == {"type": "json_object"}

    async def test_a_fenced_completion_is_still_read(self) -> None:
        fenced = '```json\n{"intent": "SUPPORT", "confidence": 0.8, "entities": {}}\n```'
        model, _ = model_for(lambda _: completion(chat(fenced)))

        result = await model.understand_intent(prompt())

        assert result.intent == "SUPPORT"

    async def test_a_missing_usage_block_costs_nothing_rather_than_failing(self) -> None:
        body = '{"intent":"SUPPORT","confidence":0.8,"entities":{}}'
        payload = {"choices": [{"message": {"content": body}}]}
        model, _ = model_for(lambda _: completion(payload))

        result = await model.understand_intent(prompt())

        assert result.usage is not None
        assert result.usage.estimated_cost_usd == 0.0


class TestFallback:
    async def test_a_retryable_status_falls_back_to_the_second_model(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.8, "entities": {}})

        def handler(request: httpx.Request) -> httpx.Response:
            if body_of(request)["model"] == FAST:
                return completion({}, status=503)
            return completion(chat(payload))

        model, seen = model_for(handler)

        result = await model.understand_intent(prompt())

        assert result.intent == "SUPPORT"
        assert [body_of(r)["model"] for r in seen] == [FAST, SLOW]
        assert result.usage is not None
        assert result.usage.model == SLOW

    async def test_the_fallback_is_priced_with_its_own_rates(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.8, "entities": {}})

        def handler(request: httpx.Request) -> httpx.Response:
            if body_of(request)["model"] == FAST:
                return completion({}, status=500)
            return completion(chat(payload))

        model, _ = model_for(handler)

        result = await model.understand_intent(prompt())

        # 1000 * 1.25 + 500 * 10.00, per million: pricier than the fast model.
        assert result.usage is not None
        assert result.usage.estimated_cost_usd == pytest.approx(0.00625)

    async def test_both_models_failing_raises(self) -> None:
        model, seen = model_for(lambda _: completion({}, status=502))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

        assert len(seen) == 2

    async def test_a_transport_error_is_retryable_across_models(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        model, seen = model_for(handler)

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

        assert len(seen) == 2


class TestTypedFailures:
    async def test_a_rate_limit_is_surfaced_not_swallowed(self) -> None:
        model, seen = model_for(lambda _: completion({}, status=429))

        with pytest.raises(RateLimited):
            await model.understand_intent(prompt())

        # Switching models would not help while the provider is throttling us,
        # and pretending otherwise turns throttling into a wrong answer.
        assert len(seen) == 1

    async def test_a_client_error_is_not_retried(self) -> None:
        model, seen = model_for(lambda _: completion({"error": "bad request"}, status=400))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

        assert len(seen) == 1

    async def test_prose_instead_of_json_is_refused(self) -> None:
        model, _ = model_for(lambda _: completion(chat("Je ne peux pas vous aider.")))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

    async def test_an_empty_completion_is_refused(self) -> None:
        model, _ = model_for(lambda _: completion(chat("   ")))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

    async def test_an_unreadable_payload_is_refused(self) -> None:
        model, _ = model_for(lambda _: completion({"choices": []}))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

    async def test_a_json_array_is_refused(self) -> None:
        model, _ = model_for(lambda _: completion(chat("[1, 2, 3]")))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

    async def test_an_out_of_range_confidence_is_refused(self) -> None:
        """A model claiming 130% confidence must not be trusted into the ledger."""
        payload = json.dumps({"intent": "SUPPORT", "confidence": 1.3, "entities": {}})
        model, _ = model_for(lambda _: completion(chat(payload)))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

    async def test_a_missing_entity_map_is_refused(self) -> None:
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.8, "entities": "beaucoup"})
        model, _ = model_for(lambda _: completion(chat(payload)))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

    async def test_a_missing_api_key_never_reaches_the_network(self) -> None:
        model, seen = model_for(lambda _: completion(chat("{}")), settings(api_key=""))

        with pytest.raises(ExternalServiceUnavailable):
            await model.understand_intent(prompt())

        assert seen == []


class TestUnpricedModel:
    async def test_an_unpriced_model_records_zero_rather_than_a_guess(self) -> None:
        """Composition refuses to enable the pipeline in this state, so this is
        only reachable from a test. It records 0 rather than an estimate: a
        plausible-looking guess would silently corrupt every budget that reads
        the ledger back.

        The loud half of the contract — refusing to boot — is asserted in
        ``test_ai_composition.py``.
        """
        payload = json.dumps({"intent": "SUPPORT", "confidence": 0.8, "entities": {}})
        config = settings(model_pricing_usd_per_million={})
        model, _ = model_for(lambda _: completion(chat(payload)), config)

        result = await model.understand_intent(prompt())

        assert result.usage is not None
        assert result.usage.estimated_cost_usd == 0.0
