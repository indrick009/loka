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
from datetime import date, datetime
from typing import Any

from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.messaging.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    BedroomCount,
    ChargingPolicy,
    PropertyStanding,
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

# The order is the conversation order: it decides what is asked next. It matches
# how people describe a home here: the type first ("appart", "studio", "chambre"),
# then modern or not, then where it is, then the rent. The bathroom count and the
# surface are deliberately *not* asked: they are rarely known and rarely used,
# and the answer that sells the place is the photos.
#
# The entry conditions come after the rent because in this market the monthly
# price is only half the story: the real move-in cost is an *avance* (several
# months up front), a *caution* (the deposit) and the monthly charges. The form
# therefore collects exactly what the Property quality gate needs to publish —
# charges, deposit, minimum duration, availability and the free-text conditions
# (where avance and agency fees are stated) — instead of reporting them missing
# only at the end.
PROPERTY_REQUIREMENTS: tuple[tuple[str, FlowStep], ...] = (
    ("property_type", FlowStep.COLLECT_PROPERTY_TYPE),
    ("standing", FlowStep.COLLECT_STANDING),
    ("location", FlowStep.COLLECT_LOCATION),
    ("rent", FlowStep.COLLECT_PRICE),
    ("charges", FlowStep.COLLECT_CHARGES),
    ("deposit", FlowStep.COLLECT_DEPOSIT),
    ("minimum_duration_months", FlowStep.COLLECT_MINIMUM_DURATION),
    ("availability", FlowStep.COLLECT_AVAILABILITY),
    ("conditions", FlowStep.COLLECT_CONDITIONS),
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
        "COLLECT_STANDING",
        "COLLECT_CHARGES",
        "COLLECT_DEPOSIT",
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
        "COLLECT_STANDING",
        "COLLECT_ACQUISITION_SOURCE",
        "COLLECT_DEPOSIT",
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
    FlowStep.COLLECT_STANDING: "ask.standing",
    FlowStep.COLLECT_LOCATION: "ask.location",
    FlowStep.COLLECT_PRICE: "ask.rent",
    FlowStep.COLLECT_FEATURES: "ask.features",
    FlowStep.COLLECT_CHARGES: "ask.charges",
    FlowStep.COLLECT_DEPOSIT: "ask.deposit",
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
        if "standing" in present or "modern" in present:
            keep("standing", lambda: _build_standing(entities))
        if "city" in present or "neighbourhood" in present:
            keep("location", lambda: self._build_location(entities))
        elif "location_hint" in present:
            resolved = _resolve_location_hint(present["location_hint"])
            if resolved is not None:
                facts["location"] = resolved
            else:
                # Only a *known* district resolves to its city above; an unknown
                # hint stays a hint, because promoting "Zzzville" to a city is
                # exactly how a wrong listing enters search.
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
        if (
            "charges" in present
            or "charges_included" in present
            or "charging_policy" in present
            or "water_charges" in present
            or "electricity_charges" in present
        ):
            try:
                amount, policy, breakdown = _build_charges(entities)
                facts["charges"] = amount
                facts["charging_policy"] = policy
                if breakdown is not None:
                    facts["charges_breakdown"] = breakdown
            except DomainError as exc:
                rejected["charges"] = exc.message
            except (TypeError, ValueError) as exc:
                rejected["charges"] = str(exc)
        if "amenities" in present:
            keep("amenities", lambda: _build_amenities(entities))
        if "amenities_hints" in present:
            keep("amenities_hints", lambda: _build_amenities(entities, hints=True))
        if "deposit" in present:
            keep(
                "deposit",
                lambda: Money(amount=_as_int(present["deposit"]))
                .require_non_negative(reason="deposit cannot be negative")
                .amount,
            )
        if "minimum_duration_months" in present:
            keep("minimum_duration_months", lambda: _as_int(present["minimum_duration_months"]))
        if "availability" in present:
            keep("availability", lambda: _build_availability(present["availability"]))
        if "conditions" in present:
            keep("conditions", lambda: _build_conditions(present["conditions"]))

        return ExtractedFacts(facts=facts, rejected=rejected)

    def next_step(
        self,
        intent: str,
        facts: ExtractedFacts,
        *,
        flow: FlowName,
        known: dict[str, Any] | None = None,
    ) -> FlowStep | None:
        """Where the conversation goes next, decided here and nowhere else.

        A model may propose facts; the order in which they are asked is a
        business rule and is not negotiable by a suggestion engine. ``flow`` is
        required rather than optional on purpose: an amount dropped into a
        support conversation must be recorded, not treated as the first answer
        to a listing form the user never started.

        ``known`` is what the session already holds. Without it the next step is
        computed from the current message alone, so a landlord who gives their
        city after already naming the property type gets asked for the type
        again — the form restarts on every turn instead of advancing.
        """
        if intent not in LISTING_INTENTS:
            return None
        if intent in FLOW_BY_OPENING_INTENT:
            return self._first_missing(facts, known)
        if flow not in LISTING_FLOWS:
            return None
        return self._first_missing(facts, known)

    @staticmethod
    def _first_missing(facts: ExtractedFacts, known: dict[str, Any] | None = None) -> FlowStep:
        already_known = known or {}
        for name, step in PROPERTY_REQUIREMENTS:
            if not facts.has(name) and name not in already_known:
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
        neighbourhood = entities.get("neighbourhood")
        neighbourhood_text = str(neighbourhood).strip() if isinstance(neighbourhood, str) else ""
        if not city and neighbourhood_text:
            # "Bastos" alone is a complete answer here: the district index knows
            # it is in Yaoundé. Without this the location question never closes.
            city = market.resolve_neighbourhood(neighbourhood_text) or ""
        if not city:
            raise ValidationFailed(
                "no city was extracted", context={"entities": sorted(entities)}
            )
        location = Location(
            city=city,
            neighbourhood=neighbourhood_text or None,
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


def _resolve_location_hint(raw: Any) -> dict[str, str] | None:
    """Turn a bare place name into a publishable location, or ``None``.

    ``None`` means "not in the district index": the caller then keeps the raw
    hint rather than inventing a city for it.
    """
    hint = str(raw).strip()
    if len(hint) < 2:
        return None
    city = market.resolve_city(hint)
    if city is not None:
        return {"city": city, "neighbourhood": ""}
    district_city = market.resolve_neighbourhood(hint)
    if district_city is not None:
        return {
            "city": district_city,
            "neighbourhood": market.canonical_neighbourhood(hint) or hint,
        }
    return None


def _build_charges(entities: dict[str, Any]) -> tuple[int, str, dict[str, int] | None]:
    """The monthly charges and whether they are included in the rent.

    A landlord who says "charges incluses" states a *policy*, not an amount; one
    who says "15 000 de charges" states an amount. Both must produce a value the
    Property quality gate accepts, otherwise it reports MISSING_CHARGES forever.

    When the landlord splits the breakdown ("eau 5 000, électricité 10 000"),
    the two amounts are summed into ``charges`` and the split is returned
    separately so the published listing can state it without a schema change.
    """
    included = entities.get("charges_included")
    policy_raw = entities.get("charging_policy")
    if policy_raw is not None and str(policy_raw).strip():
        policy = ChargingPolicy(str(policy_raw).strip().upper().replace(" ", "_"))
    elif isinstance(included, bool):
        policy = ChargingPolicy.INCLUDED if included else ChargingPolicy.EXTRA
    elif "charges" in entities or "water_charges" in entities or "electricity_charges" in entities:
        policy = ChargingPolicy.EXTRA
    else:
        policy = ChargingPolicy.INCLUDED

    breakdown: dict[str, int] | None = None
    water = entities.get("water_charges")
    electricity = entities.get("electricity_charges")
    if water is not None or electricity is not None:
        water_amount = _as_int(water) if water is not None else 0
        electricity_amount = _as_int(electricity) if electricity is not None else 0
        if min(water_amount, electricity_amount) < 0:
            raise ValidationFailed(
                "charges cannot be negative",
                context={"water": water_amount, "electricity": electricity_amount},
            )
        breakdown = {"water": water_amount, "electricity": electricity_amount}
        amount = water_amount + electricity_amount
    else:
        amount = _as_int(entities["charges"]) if "charges" in entities else 0
        if amount < 0:
            raise ValidationFailed("charges cannot be negative", context={"value": amount})
    return amount, policy.value, breakdown


_AMENITY_AXES = ("parking", "water", "electricity", "internet", "security")


def _build_amenities(entities: dict[str, Any], *, hints: bool = False) -> dict[str, object]:
    """Normalise amenity candidates to the five axes plus extras.

    Only recognised axes with a real boolean become facts; a possible negated
    or nonsense value is dropped, never repaired. ``hints=True`` reads the
    ``amenities_hints`` entity instead — the probable deductions that must NOT
    become facts on a listing but are kept so the conversation can offer to
    confirm them.
    """
    raw = entities.get("amenities_hints" if hints else "amenities")
    if not isinstance(raw, dict):
        raise ValidationFailed(
            "amenities must be an object",
            context={"entity": "amenities_hints" if hints else "amenities", "value": repr(raw)},
        )
    normalised: dict[str, object] = {}
    for axis in _AMENITY_AXES:
        value = raw.get(axis)
        if isinstance(value, bool):
            normalised[axis] = value
    extras = raw.get("extras")
    if not hints and isinstance(extras, list):
        clean_extras: list[str] = []
        for extra in extras:
            label = str(extra).strip()
            if label and len(label) <= 80:
                clean_extras.append(label)
        if clean_extras:
            normalised["extras"] = clean_extras
    return normalised


_IMMEDIATE_AVAILABILITY = (
    "immediat",
    "maintenant",
    "tout de suite",
    "disponible",
    "des que possible",
    "asap",
    "oui",
)


def _build_availability(raw: Any) -> str:
    """Normalise availability to ``IMMEDIATE`` or an ISO date, or refuse it.

    The domain needs a real date to publish; storing a phrase the Property
    cannot read would leave MISSING_AVAILABILITY_DATE on a listing the landlord
    believes they answered.
    """
    text = str(raw).strip()
    lowered = market.normalize(text)
    if not lowered:
        raise ValidationFailed("availability is empty", context={"value": raw})
    if any(word in lowered for word in _IMMEDIATE_AVAILABILITY):
        return "IMMEDIATE"
    parsed = _parse_date(text)
    if parsed is not None:
        return parsed.isoformat()
    raise ValidationFailed(
        "availability must be a date or 'immediate'", context={"value": raw}
    )


def _parse_date(text: str) -> date | None:
    candidate = text.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _build_conditions(raw: Any) -> str:
    text = str(raw).strip()
    if len(text) < 10:
        raise ValidationFailed("conditions are too vague", context={"value": raw})
    return text


_STANDING_ALIASES: dict[str, PropertyStanding] = {
    "MODERN": PropertyStanding.MODERN,
    "MODERNE": PropertyStanding.MODERN,
    "NON_MODERN": PropertyStanding.NON_MODERN,
    "NON_MODERNE": PropertyStanding.NON_MODERN,
    "ANCIEN": PropertyStanding.NON_MODERN,
    "ANCIENNE": PropertyStanding.NON_MODERN,
}


def _build_standing(entities: dict[str, Any]) -> str:
    """Read "moderne ou non" from either spelling the model may produce.

    A model handed a French message often answers ``"modern": true`` rather than
    the ``standing`` enum; both are accepted and normalised to one value, so the
    rest of the form never has to know which wording arrived.
    """
    raw = entities.get("standing")
    if raw is None and "modern" in entities:
        modern = entities["modern"]
        if isinstance(modern, bool):
            return (
                PropertyStanding.MODERN.value if modern else PropertyStanding.NON_MODERN.value
            )
        raw = modern
    text = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
    if text in _STANDING_ALIASES:
        return _STANDING_ALIASES[text].value
    return PropertyStanding(text).value
