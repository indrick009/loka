"""The pipeline refuses to boot in a state it cannot run safely.

These are boot-time guarantees: a misconfigured deployment must fail when it
starts, not quietly answer every landlord badly and surface the problem in an
invoice.
"""

from __future__ import annotations

import pytest

from loka.bounded_contexts.ai.infrastructure.composition import (
    PipelineMisconfigured,
    budget_policy,
    intent_model,
    validate_ai_settings,
)
from loka.shared.infrastructure.config.settings import (
    AISettings,
    ModelPricing,
    Settings,
)

pytestmark = pytest.mark.unit

FAST = "google/gemini-2.5-flash"
SLOW = "google/gemini-2.5-pro"

PRICED = {
    FAST: ModelPricing(input=0.30, output=2.50),
    SLOW: ModelPricing(input=1.25, output=10.00),
}


def settings(ai: AISettings) -> Settings:
    return Settings(
        environment="test",
        encryption_key="test-key-material-for-field-encryption",
        ai=ai,
    )


def enabled(**overrides: object) -> AISettings:
    base: dict[str, object] = {
        "pipeline_enabled": True,
        "api_key": "test-key",
        "model_pricing_usd_per_million": dict(PRICED),
    }
    base.update(overrides)
    return AISettings(**base)  # type: ignore[arg-type]


class TestValidation:
    def test_a_disabled_pipeline_is_never_inspected(self) -> None:
        """Disabled is the default posture: it must not require a key."""
        validate_ai_settings(settings(AISettings()))

    def test_a_fully_configured_pipeline_validates(self) -> None:
        validate_ai_settings(settings(enabled()))

    def test_an_enabled_pipeline_without_a_key_is_refused(self) -> None:
        with pytest.raises(PipelineMisconfigured, match="API key"):
            validate_ai_settings(settings(enabled(api_key="")))

    def test_an_enabled_pipeline_without_pricing_is_refused(self) -> None:
        """A budget computed from an unpriced model stays at zero while the bill
        grows, which is the exact failure a cost guard must not have."""
        with pytest.raises(PipelineMisconfigured, match="model_pricing"):
            validate_ai_settings(
                settings(enabled(model_pricing_usd_per_million={}))
            )

    def test_the_fallback_model_must_be_priced_too(self) -> None:
        with pytest.raises(PipelineMisconfigured, match=SLOW):
            validate_ai_settings(
                settings(
                    enabled(model_pricing_usd_per_million={FAST: PRICED[FAST]})
                )
            )


class TestWiring:
    def test_a_disabled_pipeline_builds_no_model(self) -> None:
        assert intent_model(settings(AISettings())) is None

    def test_an_enabled_pipeline_builds_the_model(self) -> None:
        assert intent_model(settings(enabled())) is not None

    def test_building_a_model_validates_the_configuration(self) -> None:
        with pytest.raises(PipelineMisconfigured):
            intent_model(settings(enabled(model_pricing_usd_per_million={})))

    def test_the_budget_comes_from_configuration(self) -> None:
        policy = budget_policy(
            settings(enabled(daily_budget_usd=10.0, per_user_daily_budget_usd=0.5))
        )

        assert float(policy.daily_usd) == 10.0
        assert float(policy.per_user_daily_usd) == 0.5


class TestPricingLookup:
    def test_a_priced_model_is_found(self) -> None:
        assert enabled().pricing_for(FAST) == PRICED[FAST]

    def test_an_unpriced_model_is_reported(self) -> None:
        assert enabled().pricing_for("some/new-model") is None

    def test_unpriced_models_lists_every_configured_one(self) -> None:
        assert enabled(model_pricing_usd_per_million={FAST: PRICED[FAST]}).unpriced_models() == {
            SLOW
        }
