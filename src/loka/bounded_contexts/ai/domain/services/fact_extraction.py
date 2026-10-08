"""Deterministic validation of extracted facts.

Whatever produced the candidates — regex rules or a language model — the
values must survive the *domain's* value objects before they are allowed
anywhere near a session. A model is a good guesser and a bad authority:
"150 000 000 000" or "-2 chambres" must never reach a listing, and the cheapest
place to refuse them is here, in code, with one rule for every source.

Refused fields are dropped, never repaired. The conversation asks again.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    BedroomCount,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.errors import DomainError, ValidationFailed

# A rent below this is a misparse, not a negotiation: "150" for "150 000" is the
# most common extraction error, and accepting it would create an unbelievable
# listing that then pollutes search results.
MIN_PLAUSIBLE_RENT_XAF = 10_000
MAX_PLAUSIBLE_RENT_XAF = 500_000_000

# The order is the conversation order: it decides what is asked next.
PROPERTY_REQUIREMENTS: tuple[tuple[str, FlowStep], ...] = (
    ("property_type", FlowStep.COLLECT_PROPERTY_TYPE),
    ("location", FlowStep.COLLECT_LOCATION),
    ("rent", FlowStep.COLLECT_PRICE),
    ("rooms", FlowStep.COLLECT_FEATURES),
)

# Intents that concern a listing or a search. Everything else — "merci", "c'est
# loué", "je vais réfléchir" — records its turn but must never move a listing
# form forward.
LISTING_INTENTS = frozenset(
    {
        "CREATE_PROPERTY",
        "PROPERTY_SEARCH",
        "COLLECT_PROPERTY_TYPE",
        "COLLECT_LOCATION",
        "COLLECT_PRICE",
        "COLLECT_FEATURES",
        "COLLECT_CHARGES",
        "COLLECT_MINIMUM_DURATION",
        "COLLECT_AVAILABILITY",
        "COLLECT_CONDITIONS",
        "COLLECT_SEARCH_CRITERIA",
    }
)

# The intents that *open* a flow, mapped to the flow they open. Recorded rather
# than inferred, because starting the wrong flow strands the user in a form for
# something they never asked for.
FLOW_BY_OPENING_INTENT: dict[str, FlowName] = {
    "CREATE_PROPERTY": FlowName.PROPERTY_CREATION,
    "PROPERTY_SEARCH": FlowName.PROPERTY_SEARCH,
}

LISTING_FLOWS = frozenset({FlowName.PROPERTY_CREATION, FlowName.PROPERTY_SEARCH})

# Intents the pipeline may act on. Anything else means the model invented a
# state the platform does not have, so the message is treated as untrusted.
#
# `test_fact_extraction.py` asserts that every intent `DeterministicClassifier`
# can emit appears here: this set is the trust boundary, so it has to be closed
# by a test rather than by memory.
SUPPORTED_INTENTS = frozenset(
    {
        # Deterministic classifier.
        "CREATE_PROPERTY",
        "COLLECT_PROPERTY_TYPE",
        "COLLECT_PRICE",
        "COLLECT_FEATURES",
        "COLLECT_ACQUISITION_SOURCE",
        "AVAILABILITY_STILL_AVAILABLE",
        "AVAILABILITY_RENTED",
        "AVAILABILITY_TEMPORARILY_UNAVAILABLE",
        "AFFIRMATIVE",
        "NEGATIVE",
        # Flow-level intents a model may propose.
        "COLLECT_LOCATION",
        "COLLECT_CHARGES",
        "COLLECT_MINIMUM_DURATION",
        "COLLECT_AVAILABILITY",
        "COLLECT_CONDITIONS",
        "COLLECT_SEARCH_CRITERIA",
        "PROPERTY_SEARCH",
        "AVAILABILITY_CHECK",
        "APPLICATION_FLOW",
        "TENANT_ONBOARDING",
        "VISIT_SCHEDULING",
        "REPORT_FLOW",
        "SUPPORT",
        "UNKNOWN",
    }
)

# Confidence below this never moves the conversation. The turn is still
# recorded, so "the model was unsure" is measurable instead of being silently
# converted into a wrong listing.
MIN_ACCEPTED_CONFIDENCE = 0.75

# A stable key, never a sentence: copy and translations belong to the outbound
# layer. This stage decides *what* is missing, not how to phrase it.
QUESTION_BY_STEP: dict[FlowStep, str] = {
    FlowStep.COLLECT_PROPERTY_TYPE: "ask.property_type",
    FlowStep.COLLECT_LOCATION: "ask.location",
    FlowStep.COLLECT_PRICE: "ask.rent",
    FlowStep.COLLECT_FEATURES: "ask.features",
    FlowStep.COLLECT_CHARGES: "ask.charges",
    FlowStep.COLLECT_MINIMUM_DURATION: "ask.minimum_duration",
    FlowStep.COLLECT_AVAILABILITY: "ask.availability",
    FlowStep.COLLECT_CONDITIONS: "ask.conditions",
    FlowStep.CONFIRM_PROPERTY: "ask.confirm_property",
    FlowStep.PUBLISHED: "confirm.published",
}


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


class FactExtractor:
    """Validates candidate entities through the domain value objects."""

    def validate(self, entities: dict[str, Any]) -> ExtractedFacts:
        facts: dict[str, Any] = {}
        rejected: dict[str, str] = {}

        # A model is told to answer null when unsure, so a null or empty value
        # is "the field was not extracted", not "extracted as nothing".
        present = {
            name: value
            for name, value in entities.items()
            if value is not None and str(value).strip() != ""
        }

        def keep(name: str, build: Any) -> None:
            try:
                facts[name] = build()
            except DomainError as exc:
                rejected[name] = exc.message
            except (TypeError, ValueError) as exc:
                # Value objects raise DomainError when they refuse a value, but
                # a StrEnum lookup ("property_type": "NONE") raises ValueError:
                # both are "this candidate is not a valid domain value" and the
                # field is dropped, never repaired.
                rejected[name] = str(exc)

        if "property_type" in present:
            keep(
                "property_type",
                lambda: PropertyType(str(present["property_type"]).upper()).value,
            )
        if "city" in present:
            keep("location", lambda: self._build_location(entities))
        elif "location_hint" in present:
            # A bare neighbourhood ("Bastos") stays a hint. Promoting it to a
            # city would put Douala's Bonapriso into the city column.
            keep("location_hint", lambda: _clean_hint(present["location_hint"]))
        if "price" in present:
            keep("rent", lambda: self._build_rent(present["price"]))
        if "bedrooms" in present or "bathrooms" in present:
            keep("rooms", lambda: self._build_rooms(entities))
        if "surface_area" in present:
            keep(
                "surface",
                lambda: SurfaceArea(square_metres=_as_int(present["surface_area"])).square_metres,
            )
        if "charges" in present:
            keep(
                "charges",
                lambda: Money(amount=_as_int(present["charges"]))
                .require_positive(reason="charges must be positive")
                .amount,
            )
        if "minimum_duration_months" in present:
            keep("minimum_duration_months", lambda: _as_int(present["minimum_duration_months"]))
        if "availability" in present:
            keep("availability", lambda: str(present["availability"]).upper())

        return ExtractedFacts(facts=facts, rejected=rejected)

    def next_step(self, intent: str, facts: ExtractedFacts, *, flow: FlowName) -> FlowStep | None:
        """Where the conversation goes next, decided here and nowhere else.

        A model may propose facts; the order in which they are asked is a
        business rule and is not negotiable by a suggestion engine. ``flow`` is
        required rather than optional on purpose: an amount dropped into a
        support conversation must be recorded, not treated as the first answer
        to a listing form the user never started.
        """
        if intent not in LISTING_INTENTS:
            return None
        if intent in FLOW_BY_OPENING_INTENT:
            # The caller starts the flow, which resets the state; it picks the
            # step afterwards from what is actually already known.
            return self._first_missing(facts)
        if flow not in LISTING_FLOWS:
            return None
        return self._first_missing(facts)

    @staticmethod
    def _first_missing(facts: ExtractedFacts) -> FlowStep:
        for name, step in PROPERTY_REQUIREMENTS:
            if not facts.has(name):
                return step
        return FlowStep.CONFIRM_PROPERTY

    @staticmethod
    def _build_rent(raw: Any) -> int:
        amount = Money(amount=_as_int(raw)).require_positive(reason="rent must be positive")
        if amount.amount > MAX_PLAUSIBLE_RENT_XAF:
            raise ValidationFailed(
                "rent is above the plausible range for this market",
                context={"amount": amount.amount, "ceiling": MAX_PLAUSIBLE_RENT_XAF},
            )
        return amount.amount

    @staticmethod
    def _build_rooms(entities: dict[str, Any]) -> dict[str, int]:
        rooms = BedroomCount(
            count=_as_int(entities.get("bedrooms", 0)),
            bathrooms=_as_int(entities.get("bathrooms", 1)),
        )
        return {"bedrooms": rooms.count, "bathrooms": rooms.bathrooms}

    @staticmethod
    def _build_location(entities: dict[str, Any]) -> dict[str, str]:
        city = str(entities.get("city") or "").strip()
        if not city:
            raise ValidationFailed("no city was extracted", context={"entities": sorted(entities)})
        neighbourhood = entities.get("neighbourhood")
        location = Location(
            city=city,
            neighbourhood=str(neighbourhood) if isinstance(neighbourhood, str) else None,
        )
        return {"city": location.city, "neighbourhood": location.neighbourhood or ""}


def is_supported_intent(intent: str) -> bool:
    return intent in SUPPORTED_INTENTS


def question_key_for(step: FlowStep | None) -> str | None:
    return None if step is None else QUESTION_BY_STEP.get(step)


def _as_int(raw: Any) -> int:
    """Coerce a candidate number, or refuse it as a validation failure.

    A model may answer ``{"price": "beaucoup"}``. ``int()`` raises ``ValueError``
    there, which is not a ``DomainError``: without this the exception escapes
    the extractor and crashes the consumer instead of dropping the field and
    asking the question again.
    """
    if isinstance(raw, bool) or raw is None:
        raise ValidationFailed("expected a number", context={"value": repr(raw)})
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(
            "expected a number", context={"value": repr(raw)}
        ) from exc


def _clean_hint(raw: Any) -> str:
    hint = str(raw).strip()
    if len(hint) < 2:
        raise ValidationFailed("location hint is too vague", context={"value": raw})
    return hint
