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
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    PropertyRepository,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    BedroomCount,
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
