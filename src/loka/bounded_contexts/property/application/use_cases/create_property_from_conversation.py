"""Create a listing from a WhatsApp conversation.

The conversation collects the listing facts into the session context. When the
landlord confirms the summary, this use case turns those facts into a real
:class:`Property` draft — never a second time for the same session — and
publishes it only when the domain's own quality gate says the listing is
complete. Until then the draft is durable and the conversation keeps asking for
what is missing.

The landlord profile is provisioned on the fly: a WhatsApp user who describes a
property is, by definition, listing one, and publication in this MVP does not
wait for identity verification.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.landlord.domain.entities.landlord_profile import LandlordProfile
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
)
from loka.bounded_contexts.property.domain.entities.property import Amenities, Property
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    PropertyRepository,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    Duration,
    PropertyStanding,
    PropertyStatus,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError
from loka.shared.domain.identifiers import new_id


@dataclass(frozen=True, slots=True)
class ConversationListingResult:
    """What the confirmation produced, for the reply and the session step."""

    property_id: uuid.UUID
    published: bool
    missing: tuple[str, ...]


class CreatePropertyFromConversationUseCase:
    name = "create_property_from_conversation"

    def __init__(self, uow: UnitOfWork, *, landlords: LandlordDirectory) -> None:
        self._uow = uow
        self._landlords = landlords

    async def register_from_conversation(
        self,
        *,
        user_id: uuid.UUID | None,
        facts: Mapping[str, Any],
        existing_property_id: uuid.UUID | None = None,
        now: datetime,
    ) -> ConversationListingResult | None:
        """Create (or reload) the draft for this conversation's facts.

        ``None`` means the platform cannot act on the confirmation — there is no
        user, or no landlord profile could be resolved — and the caller should
        keep the conversation where it is instead of pretending it saved
        anything.
        """
        properties: PropertyRepository = self._uow.repository("property")
        if existing_property_id is not None:
            existing = await properties.get(existing_property_id)
            if existing is not None:
                return ConversationListingResult(
                    property_id=existing.id,
                    published=existing.status is PropertyStatus.AVAILABLE,
                    missing=tuple(existing.missing_summary()),
                )

        if user_id is None:
            return None

        landlord_id = await self._landlords.profile_id_for_user(user_id)
        if landlord_id is None:
            profile = LandlordProfile(
                profile_id=new_id(), user_id=user_id, display_name=None, now=now
            )
            profiles = self._uow.repository(LANDLORD_PROFILE_REPOSITORY)
            await profiles.add(profile)
            self._uow.collect(profile)
            landlord_id = profile.profile_id

        prop = Property(property_id=new_id(), landlord_id=landlord_id, now=now)
        _apply_facts(prop, facts, now=now)

        missing = tuple(prop.missing_summary())
        if not missing:
            prop.publish(now=now)
        await properties.add(prop)
        self._uow.collect(prop)
        return ConversationListingResult(
            property_id=prop.id, published=not missing, missing=missing
        )


def _apply_facts(prop: Property, facts: Mapping[str, Any], *, now: datetime) -> None:
    """Best-effort hydration: one bad field never aborts the draft.

    The facts were already validated by the AI context's extractor, so a refusal
    here is the exception (a value the domain tightened since). Dropping the
    field and letting the quality gate report it is safer than failing the whole
    confirmation.
    """

    def guard(build: Any) -> None:
        with contextlib.suppress(DomainError, TypeError, ValueError):
            build()

    property_type = facts.get("property_type")
    if property_type:
        guard(lambda: prop.set_type(PropertyType(str(property_type)), now=now))

    standing = facts.get("standing")
    if standing:
        guard(lambda: prop.set_standing(PropertyStanding(str(standing)), now=now))

    location = facts.get("location")
    if isinstance(location, Mapping) and location.get("city"):
        neighbourhood = location.get("neighbourhood")
        guard(
            lambda: prop.set_location(
                Location(
                    city=str(location["city"]),
                    neighbourhood=str(neighbourhood) if neighbourhood else None,
                ),
                now=now,
            )
        )

    rent = facts.get("rent")
    if isinstance(rent, int) and not isinstance(rent, bool):
        guard(lambda: prop.set_rent(Money(amount=rent), now=now))

    rooms = facts.get("rooms")
    surface = facts.get("surface")
    if isinstance(rooms, Mapping):
        surface_area = SurfaceArea(surface) if isinstance(surface, int) else None
        guard(
            lambda: prop.set_rooms(
                BedroomCount(
                    count=int(rooms.get("bedrooms", 0)),
                    bathrooms=int(rooms.get("bathrooms", 1)),
                ),
                surface_area,
                now=now,
            )
        )

    duration = facts.get("minimum_duration_months")
    if isinstance(duration, int) and not isinstance(duration, bool):
        guard(lambda: prop.set_minimum_duration(Duration(months=duration), now=now))

    charges = facts.get("charges")
    breakdown_note: str | None = None
    if isinstance(charges, int) and not isinstance(charges, bool):
        policy = _charging_policy(facts.get("charging_policy"))
        guard(lambda: prop.set_charges(Money(amount=charges), policy, now=now))
        breakdown = facts.get("charges_breakdown")
        if isinstance(breakdown, Mapping):
            parts = []
            water_amount = breakdown.get("water")
            electricity_amount = breakdown.get("electricity")
            if isinstance(water_amount, int) and not isinstance(water_amount, bool):
                parts.append(f"eau : {water_amount} FCFA/mois")
            if isinstance(electricity_amount, int) and not isinstance(electricity_amount, bool):
                parts.append(f"électricité : {electricity_amount} FCFA/mois")
            if parts:
                breakdown_note = "charges détaillées — " + ", ".join(parts)

    amenities = facts.get("amenities")
    if isinstance(amenities, Mapping):
        current = prop.amenities
        guard(
            lambda: prop.update_amenities(
                Amenities(
                    parking=amenities.get("parking", current.parking),
                    water=amenities.get("water", current.water),
                    electricity=amenities.get("electricity", current.electricity),
                    internet=amenities.get("internet", current.internet),
                    security=amenities.get("security", current.security),
                    extras=frozenset(str(extra) for extra in amenities.get("extras") or ()),
                ),
                now=now,
            )
        )

    deposit = facts.get("deposit")
    if isinstance(deposit, int) and not isinstance(deposit, bool):
        guard(lambda: prop.set_deposit(Money(amount=deposit), now=now))

    window = _availability_window(facts.get("availability"), now)
    if window is not None:
        guard(lambda: prop.set_availability(window, now=now))

    conditions = facts.get("conditions")
    merged_conditions = ""
    if isinstance(conditions, str) and conditions.strip():
        merged_conditions = conditions.strip()
        if breakdown_note:
            merged_conditions = f"{merged_conditions}\n{breakdown_note}"
    elif breakdown_note:
        merged_conditions = breakdown_note
    if merged_conditions:
        guard(lambda: prop.set_conditions(merged_conditions, now=now))


def _charging_policy(raw: object) -> ChargingPolicy:
    """Read the charges policy, defaulting to "extra" for an amount.

    A stored amount with no explicit policy is money asked *on top of* the rent,
    which is the safe default: showing too high a total never misleads a tenant.
    """
    try:
        return ChargingPolicy(str(raw).strip().upper())
    except ValueError:
        return ChargingPolicy.EXTRA


def _availability_window(raw: object, now: datetime) -> AvailabilityWindow | None:
    """Build the availability window from the normalised fact, or ``None``."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    if raw.strip().upper() == "IMMEDIATE":
        return AvailabilityWindow(now.date())
    try:
        return AvailabilityWindow(datetime.strptime(raw.strip(), "%Y-%m-%d").date())
    except ValueError:
        return None
