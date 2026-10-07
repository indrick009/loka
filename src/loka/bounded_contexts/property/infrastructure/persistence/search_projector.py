"""Rebuilds a search document from the write model.

The projection is driven by events, but the projector reads the *current*
aggregate row rather than trusting the event payload. That keeps it idempotent
and convergent: the same event applied twice writes the same document, and an
event that arrives late still reflects the latest committed state.

``source_version`` carries the aggregate version at projection time so a
redelivered older event cannot overwrite a fresher document. Projecting a
property that no longer exists is a no-op, not an error: deletion is a
convergence signal, not a failure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertyMediaRow,
    PropertyRow,
    PropertySearchDocumentRow,
)
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


class SqlAlchemySearchProjector:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def project(self, property_id: uuid.UUID) -> bool:
        """Upsert one document. Returns whether the read model changed."""
        source = await self._session.get(PropertyRow, property_id)
        if source is None:
            _logger.info("search_projection_source_missing", property_id=str(property_id))
            return False

        document = await self._session.get(PropertySearchDocumentRow, property_id)
        if document is not None and document.source_version > source.version:
            _logger.info(
                "search_projection_stale_skipped",
                property_id=str(property_id),
                document_version=document.source_version,
                source_version=source.version,
            )
            return False

        keys = await self._media_object_keys(property_id)
        if document is None:
            document = PropertySearchDocumentRow(property_id=property_id)
            self._session.add(document)

        document.landlord_id = source.landlord_id
        document.status = source.status
        document.property_type = source.property_type
        document.city = source.city
        document.neighbourhood = source.neighbourhood
        document.public_price_xaf = source.public_price_xaf
        document.rent_xaf = source.rent_xaf
        document.charges_xaf = source.charges_xaf
        document.charging_policy = source.charging_policy
        document.bedrooms = source.bedrooms
        document.bathrooms = source.bathrooms
        document.surface_m2 = source.surface_m2
        document.minimum_duration_months = source.minimum_duration_months
        document.available_from = source.available_from
        document.amenities = source.amenities
        document.media_object_keys = keys
        document.is_verified = source.is_verified
        document.published_at = source.published_at
        document.source_created_at = source.created_at
        document.source_version = source.version
        document.projected_at = datetime.now(UTC)
        await self._session.flush()
        return True

    async def _media_object_keys(self, property_id: uuid.UUID) -> list[str]:
        result = await self._session.execute(
            select(PropertyMediaRow.object_key)
            .where(
                PropertyMediaRow.property_id == property_id,
                PropertyMediaRow.status == "UPLOADED",
            )
            .order_by(PropertyMediaRow.position, PropertyMediaRow.id)
        )
        return list(result.scalars())