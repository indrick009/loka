"""Every domain the engine serves, and the lookups the engine needs from them.

Housing predates the plug contract and keeps its own path; Food, Dating and
Found are plugged in here. Adding a domain is one line in ``SPECS``.
"""

from __future__ import annotations

from loka.bounded_contexts.booking.conversation.spec import BOOKING
from loka.bounded_contexts.conversation.application.plugs.spec import DomainSpec
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowName
from loka.bounded_contexts.dating.conversation.spec import DATING
from loka.bounded_contexts.food.conversation.spec import FOOD
from loka.bounded_contexts.found.conversation.spec import FOUND
from loka.bounded_contexts.housing.conversation.facts import (
    FLOW_BY_OPENING_INTENT as HOUSING_OPENING_INTENTS,
)
from loka.bounded_contexts.housing.conversation.facts import (
    SUPPORTED_INTENTS as HOUSING_INTENTS,
)

SPECS: tuple[DomainSpec, ...] = (FOOD, DATING, FOUND, BOOKING)

_BY_FLOW = {flow: spec for spec in SPECS for flow in spec.flows}

PLUG_FLOWS = frozenset(_BY_FLOW)

HOLDING_QUESTIONS: frozenset[str] = frozenset().union(*(spec.holding for spec in SPECS))

OPENING_INTENTS: dict[str, FlowName] = {
    **HOUSING_OPENING_INTENTS,
    **{intent: flow for spec in SPECS for intent, flow in spec.opening_intents.items()},
}

SUPPORTED_INTENTS = HOUSING_INTENTS.union(
    *(spec.intents | frozenset(spec.opening_intents) for spec in SPECS)
)


def spec_for(flow: FlowName | None) -> DomainSpec | None:
    return _BY_FLOW.get(flow) if flow is not None else None


def is_supported_intent(intent: str) -> bool:
    return intent in SUPPORTED_INTENTS


def domain_of(flow: FlowName) -> str | None:
    """Which service a flow belongs to; ``None`` for the greeting before any task."""
    spec = spec_for(flow)
    if spec is not None:
        return spec.name
    return None if flow is FlowName.SUPPORT else "housing"
