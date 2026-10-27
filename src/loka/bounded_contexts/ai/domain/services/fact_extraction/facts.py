"""The facts that survived validation, and the ones refused."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ExtractedFacts:
    """Facts the domain accepted, and why the others were refused."""

    facts: dict[str, Any] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)

    def has(self, name: str) -> bool:
        return name in self.facts

    def get(self, name: str, default: Any = None) -> Any:
        return self.facts.get(name, default)

    def as_context(self) -> dict[str, Any]:
        """Exactly what may be merged into a session's context.

        Built from ``facts`` alone, so a key invented upstream cannot reach the
        durable state even if the validator is later extended carelessly.
        """
        return dict(self.facts)
