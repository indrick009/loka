"""The model the agent talks to, reached through the OpenRouter key Kora already has."""

from __future__ import annotations

import os

# The import-time banner is for a terminal, not for the worker's logs.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from loka.shared.infrastructure.config.settings import AISettings


def openrouter_model(settings: AISettings, model_name: str | None = None) -> OpenAIChatModel:
    """An OpenRouter model, by its OpenRouter id (``anthropic/…``, ``google/…``)."""
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        raise RuntimeError("AI__API_KEY is empty: the agent needs an OpenRouter key")
    return OpenAIChatModel(
        model_name or settings.default_model,
        provider=OpenRouterProvider(api_key=api_key, app_title="Kora"),
    )
