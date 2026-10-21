"""Come back to a tenant once something answers his demand.

The thread told him "si un logement correspond, je reviens vers vous". This is
the part that keeps that promise, from both directions:

- his demand is recorded or restated: the catalogue is searched for it;
- a lodging is published: the demands still waiting are held against it.

The second direction does not search the catalogue. The search reads a
projection fed asynchronously from the very ``PropertyPublished`` that triggers
it, so the lodging that just went live may not be there yet; the listing itself
is read instead, and :meth:`TenantDemand.accepts` holds it to the same bar.

A demand is answered once, with every lodging found at that moment, and then
closed: he is owed one message, not one per publication. A later search opens
a new demand.

Verification ranks a landlord, it does not gate him, so unverified listings are
proposed too.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog

from loka.bounded_contexts.property.application.queries.search_properties import (
    REPOSITORY_NAME as SEARCH_REPOSITORY,
)
from loka.bounded_contexts.property.application.queries.search_properties import (
    PropertySearch,
    PropertySearchCriteria,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
)
from loka.bounded_contexts.property.domain.entities.tenant_demand import TenantDemand
from loka.bounded_contexts.property.domain.repositories.property_repository import (
    TenantDemandRepository,
)
from loka.bounded_contexts.property.domain.value_objects.enums import PropertyStatus
from loka.shared.application.unit_of_work import UnitOfWork

# Enough to choose from, few enough to read on a phone and answer with a digit.
MAX_PROPOSALS = 5

# Open demands held against one new listing. Far above what 1 000 users a day
# leave open; past it the oldest are served first, as the repository orders them.
MAX_OPEN_DEMANDS = 500


class MatchTenantDemandsUseCase:
    name = "match_tenant_demands"

    def __init__(
        self, uow: UnitOfWork, *, logger: structlog.stdlib.BoundLogger | None = None
    ) -> None:
        self._uow = uow
        self._logger = logger or structlog.get_logger(self.name)

    async def for_demand(self, demand_id: uuid.UUID, *, now: datetime) -> int:
        """Search the catalogue for one demand. Returns how many were proposed."""
        demands: TenantDemandRepository = self._uow.repository("tenant_demand")
        demand = await demands.get(demand_id)
        if demand is None or not demand.is_answerable:
            return 0

        search: PropertySearch = self._uow.repository(SEARCH_REPOSITORY)
        result = await search.search(_criteria(demand), limit=MAX_PROPOSALS)
        proposals = [_from_search(item) for item in result.items]
        if not proposals:
            return 0

        demand.mark_answered(now=now, proposals=proposals)
        await demands.save(demand)
        self._uow.collect(demand)
        await self._uow.commit()
        self._logger.info(
            "tenant_demand_matched",
            demand_id=str(demand.id),
            proposals=len(proposals),
            trigger="demand",
        )
        return len(proposals)

    async def for_listing(self, property_id: uuid.UUID, *, now: datetime) -> int:
        """Hold one published listing against every open demand.

        Returns how many demands it answered.
        """
        prop: Property | None = await self._uow.repository("property").get(property_id)
        if prop is None or prop.status is not PropertyStatus.AVAILABLE:
            return 0
        proposal = _from_listing(prop)

        demands: TenantDemandRepository = self._uow.repository("tenant_demand")
        answered = 0
        for demand in await demands.open_demands(limit=MAX_OPEN_DEMANDS):
            if not demand.accepts(
                property_type=proposal["property_type"],
                city=proposal["city"],
                neighbourhood=proposal["neighbourhood"],
                rent_xaf=proposal["rent_xaf"],
                point=_point(prop),
            ):
                continue
            demand.mark_answered(now=now, proposals=[proposal])
            await demands.save(demand)
            self._uow.collect(demand)
            answered += 1

        if answered:
            await self._uow.commit()
            self._logger.info(
                "tenant_demand_matched",
                property_id=str(property_id),
                demands=answered,
                trigger="listing",
            )
        return answered


def _criteria(demand: TenantDemand) -> PropertySearchCriteria:
    band = demand.price_band()
    return PropertySearchCriteria(
        city=demand.city,
        neighbourhoods=demand.neighbourhoods,
        property_types=(demand.property_type,) if demand.property_type else (),
        min_price_xaf=band[0] if band else None,
        max_price_xaf=band[1] if band else None,
        near_price_xaf=demand.max_rent.amount if demand.max_rent else None,
        verified_only=False,
        sort="price_nearest" if band else "recent",
        near=demand.near,
    )


def _from_search(item: dict[str, Any]) -> dict[str, Any]:
    keys = item.get("media_object_keys")
    return {
        "property_id": str(item.get("property_id") or ""),
        "property_type": item.get("type"),
        "city": item.get("city"),
        "neighbourhood": item.get("neighbourhood") or None,
        "rent_xaf": item.get("price_xaf"),
        # The photos the listing was published with, in order, so the tenant is
        # shown the lodging and not just a line of text about it.
        "photo_object_keys": [str(key) for key in keys] if isinstance(keys, list) else [],
    }


def _from_listing(prop: Property) -> dict[str, Any]:
    location = prop.location
    price = prop.public_price
    return {
        "property_id": str(prop.id),
        "property_type": prop.property_type.value if prop.property_type else None,
        "city": location.city if location else None,
        "neighbourhood": (location.neighbourhood or None) if location else None,
        "rent_xaf": price.amount if price else None,
        "photo_object_keys": _photo_object_keys(prop),
    }


def _point(prop: Property) -> tuple[float, float] | None:
    """Where the landlord shared the lodging from, when he did."""
    coordinates = prop.location.coordinates if prop.location else None
    return (coordinates.latitude, coordinates.longitude) if coordinates else None


def _photo_object_keys(prop: Property) -> list[str]:
    """Uploaded photos, in the order the landlord attached them.

    Only photos: a floor plan or an identity document is not what a tenant is
    shown, and a quarantined or rejected item has no place in a message.
    """
    return [
        media.object_key
        for media in sorted(prop.media, key=lambda media: media.position)
        if media.kind is MediaKind.PHOTO and media.status is MediaStatus.UPLOADED
    ]
