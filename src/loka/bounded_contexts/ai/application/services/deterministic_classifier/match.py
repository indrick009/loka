"""A candidate intent found by a rule."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DeterministicMatch:
    intent: str
    confidence: float
    entities: dict[str, object] = field(default_factory=dict)
