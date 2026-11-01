"""What a domain plugged into the conversation engine declares about itself.

Food, Dating and Found each publish one ``DomainSpec``: the flows they run,
the form each flow fills, the intents that open them, what the model must
extract for them and how those facts are validated. The engine reads it; it
never knows what a restaurant or a profile is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.conversation.domain.services.extracted_facts import ExtractedFacts


@dataclass(frozen=True, slots=True)
class FormField:
    """One field a flow collects: what the model is told, and what the user reads."""

    name: str
    topic: str
    question: Mapping[str, str]

    def ask(self, language: str) -> str:
        return self.question.get(language) or self.question["fr"]


@dataclass(frozen=True, slots=True)
class DomainSpec:
    name: str
    roles: Mapping[FlowName, str]
    forms: Mapping[FlowName, tuple[FormField, ...]]
    opening_intents: Mapping[str, FlowName]
    intents: frozenset[str]
    rules: str
    entities: Mapping[str, Any]
    validate: Callable[[dict[str, Any], FlowName], ExtractedFacts]
    # Opening intent → a regex over the folded message (accents, case and
    # punctuation removed, apostrophes as spaces). Read only when no model is
    # available: the words that name this service without any doubt.
    cues: Mapping[str, str] = field(default_factory=dict)

    @property
    def flows(self) -> frozenset[FlowName]:
        return frozenset(self.roles)

    def question_key(self, name: str) -> str:
        return f"ask.{self.name}.{name}"

    def field_of(self, key: str | None) -> str | None:
        prefix = f"ask.{self.name}."
        return key[len(prefix):] if key and key.startswith(prefix) else None

    def form(self, flow: FlowName) -> tuple[FormField, ...]:
        return self.forms.get(flow, ())

    def missing(self, flow: FlowName, known: Mapping[str, Any]) -> list[FormField]:
        return [item for item in self.form(flow) if known.get(item.name) in (None, "", [])]
