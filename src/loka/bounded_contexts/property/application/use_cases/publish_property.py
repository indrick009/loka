"""Publish property use case.

Thin controller for the domain: it loads the aggregate, checks the external
gating rules (landlord verified, quota), calls the aggregate behaviour and
commits. The domain decides everything about the listing itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.landlord.application.ports import LandlordDirectory
from loka.bounded_contexts.property.application.dto.property_dto import PropertySummary
from loka.bounded_contexts.property.domain.exceptions import PublicationBlocked
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    LandlordPropertyQuota,
    PropertyRepository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import AuthorizationDenied, ResourceNotFound


@dataclass(frozen=True, slots=True)
class PublishPropertyCommand:
    property_id: uuid.UUID
    actor_id: uuid.UUID
    confirmed_by_landlord: bool


class PublishPropertyUseCase:
    name = "publish_property"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        landlords: LandlordDirectory,
        max_active_properties: int = 25,
    ) -> None:
        self._uow = uow
        self._landlords = landlords
        self._max_active_properties = max_active_properties

    async def execute(
        self, command: PublishPropertyCommand, *, now: datetime
    ) -> PropertySummary:
        repository: PropertyRepository = self._uow.repository("property")
        prop = await repository.get(command.property_id)
        if prop is None:
            raise ResourceNotFound(
                "property not found", context={"property_id": str(command.property_id)}
            )

        # The caller authenticates as a user, the listing belongs to a landlord
        # profile: the comparison has to go through the directory, never a raw
        # id equality check.
        caller_profile_id = await self._landlords.profile_id_for_user(command.actor_id)
        if caller_profile_id is None or caller_profile_id != prop.landlord_id:
            raise AuthorizationDenied(
                "only the landlord who owns the listing may publish it",
                context={
                    "property_id": str(command.property_id),
                    "actor_id": str(command.actor_id),
                },
            )

        if not command.confirmed_by_landlord:
            raise PublicationBlocked(
                "publication requires explicit landlord confirmation",
                context={"property_id": str(command.property_id)},
            )

        if not await self._landlords.is_verified(prop.landlord_id):
            raise PublicationBlocked(
                "landlord identity must be verified before publishing",
                context={"landlord_id": str(prop.landlord_id)},
            )

        quota: LandlordPropertyQuota = self._uow.repository("property_quota")
        # The listing being published counts towards the catalogue only while it
        # is still a draft, so it must not be counted against its own quota.
        active = await quota.active_count(
            prop.landlord_id, exclude_property_id=prop.id
        )
        if active >= self._max_active_properties:
            raise PublicationBlocked(
                "landlord reached the maximum number of active properties",
                context={
                    "landlord_id": str(prop.landlord_id),
                    "limit": self._max_active_properties,
                },
            )

        prop.publish(now=now, actor_id=command.actor_id)
        await repository.save(prop)
        self._uow.collect(prop)
        await self._uow.commit()
        return PropertySummary.from_aggregate(prop)