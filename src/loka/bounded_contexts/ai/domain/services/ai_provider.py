"""LLM provider abstraction.

The domain never learns which vendor is behind this protocol. Swapping
Gemini for another model is an infrastructure change only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from loka.shared.domain.errors import ExternalServiceUnavailable


class AIProvider(Protocol):
    @property
    def model(self) -> str: ...

    async def understand_intent(self, prompt: Prompt) -> IntentResult: ...

    async def extract_entities(self, prompt: Prompt, schema: dict[str, Any]) -> dict[str, Any]:
        """Must return a dict validating against ``schema``."""

    async def generate_response(self, prompt: Prompt) -> str: ...

    async def classify(self, prompt: Prompt, labels: tuple[str, ...]) -> tuple[str, float]: ...

    async def transcribe(self, *, media_object_key: str, language: str | None = None) -> str:
        """Voice note to text; used for landlord voice descriptions."""


@dataclass(frozen=True, slots=True)
class Prompt:
    """Provider-neutral request. Vendor-specific fields live in ``extra``."""

    system: str
    user: str
    temperature: float = 0.2
    max_output_tokens: int = 1024
    response_schema: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntentResult:
    intent: str
    confidence: float
    entities: dict[str, Any]
    language: str = "fr"
    raw: str | None = None
    # What the call cost. Optional because a provider that cannot report usage
    # must leave it empty rather than invent a price: the ledger records zero,
    # and under-reporting stays visible in the metrics instead of being hidden.
    usage: LlmUsage | None = None

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.75

    @classmethod
    def from_structured(
        cls, payload: dict[str, Any], *, language: str = "fr"
    ) -> IntentResult:
        confidence = float(payload.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ExternalServiceUnavailable(
                "AI returned an out-of-range confidence", context={"confidence": confidence}
            )
        entities = payload.get("entities")
        if not isinstance(entities, dict):
            raise ExternalServiceUnavailable("AI returned a malformed entity map")
        return cls(
            intent=str(payload.get("intent", "UNKNOWN")),
            confidence=confidence,
            entities=entities,
            language=language,
        )


@dataclass(frozen=True, slots=True)
class LlmUsage:
    input_tokens: int
    output_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    model: str


@dataclass(frozen=True, slots=True)
class LlmResponse:
    content: str
    usage: LlmUsage
    structured: dict[str, Any] | None = None