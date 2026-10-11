"""LLM provider abstraction.

The domain never learns which vendor is behind this protocol. Swapping
Gemini for another model is an infrastructure change only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from loka.shared.domain.errors import ExternalServiceUnavailable

# The languages a conversation may be held in. Defined next to ``IntentResult``
# because the model reports the conversation's language through that record, and
# the pipeline stores it only to fall back on when a later message is too short
# to tell ("150000" has no language of its own).
SUPPORTED_LANGUAGES = frozenset({"fr", "en"})


def normalise_language(value: Any, *, default: str | None = None) -> str | None:
    """Read the language tag the model reported, or ``default`` if unusable.

    The model answers with a bare code (``fr``), but a vendor sometimes adds a
    region (``en-US``); the first two letters are what a conversation keeps.
    """
    if isinstance(value, str):
        candidate = value.strip().casefold()[:2]
        if candidate in SUPPORTED_LANGUAGES:
            return candidate
    return default


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
    # The reply the model wrote *in the same call*, when the deployment asks for
    # it. One round-trip answers the message and phrases the answer; a provider
    # that does not inline it leaves this empty and the pipeline falls back to a
    # second call or a stable template.
    reply: str | None = None
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
        confidence = _confidence_of(payload.get("confidence", 0.0))
        entities = _entities_of(payload.get("entities"))
        reply = payload.get("reply")
        return cls(
            intent=str(payload.get("intent", "UNKNOWN")),
            confidence=confidence,
            entities=entities,
            # The model read the message, so it decides the language; the caller's
            # value is only the fallback for a completion that omitted it.
            language=normalise_language(payload.get("language")) or language,
            reply=reply.strip() if isinstance(reply, str) and reply.strip() else None,
        )


def _confidence_of(raw: Any) -> float:
    """How sure the model says it is, or one typed failure.

    ``float(None)`` and ``float([])`` raise ``TypeError``, not ``ValueError``,
    and neither is a ``DomainError``: they escaped the adapter untyped and
    killed the consumer on a path no handler was watching. Every bad answer a
    provider can give has to arrive as the same typed error, or the pipeline
    cannot decide whether to retry.
    """
    try:
        confidence = float(raw)
    except (TypeError, ValueError) as exc:
        raise ExternalServiceUnavailable(
            "AI returned a non-numeric confidence", context={"confidence": repr(raw)}
        ) from exc
    if not 0.0 <= confidence <= 1.0:
        raise ExternalServiceUnavailable(
            "AI returned an out-of-range confidence", context={"confidence": confidence}
        )
    return confidence


def _entities_of(raw: Any) -> dict[str, Any]:
    """The candidate facts, or an empty map when the model found none.

    A model asked about a greeting or a meta-question has nothing to extract,
    and roughly a third of the time it says so by writing ``null`` rather than
    ``{}``. Refusing that answer cost **28% of all turns**: the exception
    escaped the analysis, the consumer retried, and the user received no reply
    at all — see ``docs/conversation-defects-v1.md`` (D1).

    So "nothing extracted" is an ordinary outcome and passes through empty. A
    map of the wrong type is not: that is a broken contract, and keeping it an
    error is what buys one more sample from the provider and keeps the failure
    visible in the metrics instead of silently emptying the facts.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ExternalServiceUnavailable(
            "AI returned a malformed entity map", context={"entities": type(raw).__name__}
        )
    return raw


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