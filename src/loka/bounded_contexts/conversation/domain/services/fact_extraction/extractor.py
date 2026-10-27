"""Validate proposed facts, then decide the next step of the form."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction.facts import (
    ExtractedFacts,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction.forms import (
    NEXT_LODGINGS_KEY,
    PROPERTY_REQUIREMENTS,
    SEARCH_REQUIREMENTS,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction.intents import (
    FLOW_BY_OPENING_INTENT,
    LISTING_FLOWS,
    LISTING_INTENTS,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction.lodging_facts import (
    _build_amenities,
    _build_availability,
    _build_conditions,
    _build_security,
    _build_standing,
    _states_security,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction.number_facts import (
    _as_int,
    _build_charges,
    _build_choice,
    _build_quantity,
    _build_rent,
    _build_rooms,
)
from loka.bounded_contexts.conversation.domain.services.fact_extraction.place_facts import (
    _build_location,
    _clean_hint,
    _resolve_location_hint,
)
from loka.bounded_contexts.housing.domain.value_objects.enums import (
    PropertyType,
    RentMonths,
    SurfaceArea,
)
from loka.shared.domain.errors import DomainError, ValidationFailed


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
        if present.keys() & {"city", "neighbourhood", "neighbourhoods", "latitude"}:
            keep("location", lambda: _build_location(entities))
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
            keep("rent", lambda: _build_rent(present["price"]))
        if "bedrooms" in present or "bathrooms" in present:
            keep("rooms", lambda: _build_rooms(entities))
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
        if "property_choice" in present:
            keep("property_choice", lambda: _build_choice(present["property_choice"]))
        if "quantity" in present:
            keep("quantity", lambda: _build_quantity(present["quantity"]))
        if "deposit_months" in present:
            keep(
                "deposit_months",
                lambda: RentMonths(months=_as_int(present["deposit_months"])).months,
            )
        elif "deposit" in present:
            # The landlord answered the caution in francs. Refused rather than
            # converted: dividing by a rent that may not be known yet, or may
            # be corrected later, produces a number of months nobody stated —
            # and a wrong number of months is a listing the matching misplaces.
            # Rejected facts are what make the question get asked again.
            rejected["deposit_months"] = (
                "la caution se compte en mois de loyer, pas en francs"
            )
        if "advance_months" in present:
            keep(
                "advance_months",
                lambda: RentMonths(months=_as_int(present["advance_months"])).months,
            )
        if isinstance(present.get("lodgings"), list):
            keep(NEXT_LODGINGS_KEY, lambda: self._build_lodgings(present["lodgings"]))
            queued = facts.get(NEXT_LODGINGS_KEY)
            if queued:
                # The first lodging is the form under way: what the model did not
                # also put in the flat fields is taken from it.
                first = queued.pop(0)
                for name, value in first.items():
                    facts.setdefault(name, value)
                if not queued:
                    del facts[NEXT_LODGINGS_KEY]
        if "amenities" in present and _states_security(entities):
            # Step 9 is answered by either half of it: a landlord who says
            # "il y a un gardien" has answered, and so has one who says
            # "pas de parking". A listing that merely mentioned the internet
            # has not, which is why this is its own fact and not ``amenities``.
            keep("security", lambda: _build_security(entities))
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

        The requirements walked are the target flow's, not the session's: the
        first message of a search arrives in a session whose flow is still
        SUPPORT, and it must open on the tenant's form rather than on the
        landlord's.
        """
        if intent not in LISTING_INTENTS:
            return None
        if intent not in FLOW_BY_OPENING_INTENT and flow not in LISTING_FLOWS:
            # Only CREATE_PROPERTY and PROPERTY_SEARCH open a flow, and this
            # guard exists so an amount dropped into a support conversation
            # cannot drag the user into a form. But discarding the turn
            # outright was a dead end with no way out: a model that labelled
            # "je veux un studio" COLLECT_PROPERTY_TYPE — a perfectly fair
            # reading — had its verdict thrown away, and every later message
            # was thrown away too, because escaping required one of exactly
            # two intents the model never produced again. Observed live: seven
            # consecutive turns answered "c'est bien noté" while the model
            # understood each one.
            #
            # A turn that states something only a lodging conversation
            # produces is therefore answered with the one question that
            # resolves it. Kora does not guess the side: "je veux un studio"
            # is a landlord or a tenant, and reading it wrong puts someone
            # through the opposite form.
            if _states_a_lodging(facts) and not _already_asked_the_role(known):
                return FlowStep.CLARIFY_ROLE
            # The question is already open and this turn still did not answer
            # it. Asking a third time is the repetition defect; so the turn is
            # acknowledged and the question re-arms on his next sentence about
            # a lodging. Every realistic answer — "publier", "je cherche",
            # "je suis bailleur" — is read without a model call, so reaching
            # here means the user answered something else entirely.
            return None
        target_flow = FLOW_BY_OPENING_INTENT.get(intent, flow)
        if target_flow is FlowName.PROPERTY_SEARCH:
            return self._first_missing(facts, known, SEARCH_REQUIREMENTS, FlowStep.PRESENT_RESULTS)
        return self._first_missing(facts, known, PROPERTY_REQUIREMENTS, FlowStep.CONFIRM_PROPERTY)

    @staticmethod
    def _first_missing(
        facts: ExtractedFacts,
        known: dict[str, Any] | None,
        requirements: tuple[tuple[str, FlowStep], ...],
        complete: FlowStep,
    ) -> FlowStep:
        already_known = known or {}
        for name, step in requirements:
            if not facts.has(name) and name not in already_known:
                return step
            if (
                name == "location"
                and requirements is PROPERTY_REQUIREMENTS
                and not _places_the_lodging(facts, already_known)
            ):
                # Housing §4: the shared location places a lodging reliably;
                # without it Kora asks "la ville et le quartier", and the step
                # must be validated before moving on. "Douala" alone used to
                # close it, and a tenant then read "Studio - Douala".
                return step
        return complete

    def _build_lodgings(self, raw: list[Any]) -> list[dict[str, Any]]:
        """Each distinct lodging he described, validated like the form's fields.

        One that does not validate drops the whole list: publishing the others
        while silently forgetting one would leave a lodging nobody asked about.
        """
        lodgings: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ValidationFailed("a lodging must be an object", context={"value": repr(item)})
            lodging: dict[str, Any] = {
                "property_type": PropertyType(str(item.get("property_type")).upper()).value,
                "quantity": _build_quantity(item.get("quantity") or 1),
            }
            if item.get("price") is not None:
                lodging["rent"] = _build_rent(item["price"])
            lodgings.append(lodging)
        return lodgings



def _places_the_lodging(facts: ExtractedFacts, known: dict[str, Any]) -> bool:
    """A shared pin, or a city together with its neighbourhood."""
    location = facts.get("location") if facts.has("location") else known.get("location")
    if not isinstance(location, dict):
        return False
    return location.get("latitude") is not None or bool(location.get("neighbourhood"))


# Facts that only someone talking about a lodging produces. A rent is
# deliberately absent: a bare amount is exactly the stray value the guard above
# exists to ignore, and "50 000" alone says nothing about a lodging at all.
_LODGING_FACTS = frozenset(
    {
        "property_type",
        "location",
        "location_hint",
        "standing",
        "quantity",
        "charges",
        "charging_policy",
        "deposit_months",
        "advance_months",
        "security",
        "amenities",
        "rooms",
        "surface",
    }
)


def _already_asked_the_role(known: dict[str, Any] | None) -> bool:
    return (known or {}).get("next_question") == "ask.publish_or_search"


def _states_a_lodging(facts: ExtractedFacts) -> bool:
    """Whether this turn said something only a lodging conversation says."""
    return any(facts.has(name) for name in _LODGING_FACTS)
