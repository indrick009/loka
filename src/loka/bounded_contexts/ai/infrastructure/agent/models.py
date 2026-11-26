"""The model the agent talks to, at the endpoint idia is configured with.

OpenRouter directly, or any OpenAI-compatible endpoint — the shared
CLIProxyAPI since 26 Nov 2026, whose model names (« idia-main ») carry no
provider prefix. The OpenRouter provider ignored ``AI__BASE_URL`` and refused
such names: every turn failed once idia was pointed at the proxy.
"""

from __future__ import annotations

import os

# The import-time banner is for a terminal, not for the worker's logs.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from loka.shared.infrastructure.config.settings import AISettings


def openrouter_model(settings: AISettings, model_name: str | None = None) -> OpenAIChatModel:
    """The configured model, through OpenRouter or the endpoint in ``AI__BASE_URL``."""
    api_key = settings.api_key.get_secret_value()
    if not api_key:
        raise RuntimeError("AI__API_KEY is empty: the agent needs a model key")
    if "openrouter.ai" in settings.base_url:
        return OpenAIChatModel(
            model_name or settings.default_model,
            provider=OpenRouterProvider(api_key=api_key, app_title="idia"),
        )
    return OpenAIChatModel(
        model_name or settings.default_model,
        provider=OpenAIProvider(base_url=settings.base_url, api_key=api_key),
    )
