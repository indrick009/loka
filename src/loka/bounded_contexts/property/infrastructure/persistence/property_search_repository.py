"""Read side for property search.

Implements the ``property_search`` query port by reading the
``property_search_documents`` projection. Filtering, sorting and cursor logic
are defined once in the application query module; this adapter only translates
them into SQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.application.queries.search_properties import (
    AVAILABLE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    PropertySearchCriteria,
    PropertySearchResult,
    decode_cursor,
    encode_cursor,
)
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertySearchDocumentRow,
)

# Sorts the adapter knows how to translate. ``price_nearest`` needs an anchor
# price and has no stable keyset, so it is offered single-page only.
_SORTS = frozenset({"recent", "price_asc", "price_desc", "price_nearest"})
_CURSOR_SORTS = frozenset({"recent", "price_asc", "price_desc"})


class PropertySearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _filters(self, criteria: PropertySearchCriteria) -> list[Any]:
        clauses: list[Any] = [
            PropertySearchDocumentRow.status == AVAILABLE,
            # A listing whose last lodging is gone has nothing to offer. The
            # status filter normally covers it — the landlord's answer flips the
            # listing to RENTED — but the read model stays correct on its own
            # rather than depending on two writes landing in order.
            PropertySearchDocumentRow.lodgings > 0,
        ]
        if criteria.city:
            clauses.append(PropertySearchDocumentRow.city == criteria.city)
        if criteria.near is not None:
            clauses.append(self._around(criteria))
        elif criteria.neighbourhoods:
            clauses.append(PropertySearchDocumentRow.neighbourhood.in_(criteria.neighbourhoods))
        if criteria.property_types:
            clauses.append(PropertySearchDocumentRow.property_type.in_(criteria.property_types))
        if criteria.min_price_xaf is not None:
            clauses.append(PropertySearchDocumentRow.public_price_xaf >= criteria.min_price_xaf)
        if criteria.max_price_xaf is not None:
            clauses.append(PropertySearchDocumentRow.public_price_xaf <= criteria.max_price_xaf)
        if criteria.min_bedrooms is not None:
            clauses.append(PropertySearchDocumentRow.bedrooms >= criteria.min_bedrooms)
        if criteria.min_surface_m2 is not None:
            clauses.append(PropertySearchDocumentRow.surface_m2 >= criteria.min_surface_m2)
        if criteria.available_before is not None:
            clauses.append(
                or_(
                    PropertySearchDocumentRow.available_from.is_(None),
                    PropertySearchDocumentRow.available_from <= criteria.available_before,
                )
            )
        if criteria.verified_only:
            clauses.append(PropertySearchDocumentRow.is_verified.is_(True))
        return clauses

    async def search(
        self,
        criteria: PropertySearchCriteria,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> PropertySearchResult:
        size = max(1, min(limit, MAX_PAGE_SIZE))
        statement = select(PropertySearchDocumentRow).where(*self._filters(criteria))
        sort = (
            criteria.sort
            if criteria.sort in _SORTS and self._is_sortable(criteria, criteria.sort)
            else "recent"
        )
        statement = self._apply_sort(statement, criteria, sort, cursor)

        result = await self._session.execute(statement.limit(size + 1))
        rows = list(result.scalars().all())
        has_more = len(rows) > size
        rows = rows[:size]

        items = [self._to_item(row) for row in rows]

        # ``price_nearest`` orders by a distance, not by a column: there is no
        # value to encode a stable keyset cursor from, so it is single-page.
        next_cursor = None
        if has_more and rows and sort in _CURSOR_SORTS:
            last = rows[-1]
            next_cursor = encode_cursor(
                sort,
                getattr(last, self._sort_column(sort)),
                last.property_id,
            )
        return PropertySearchResult(items=items, next_cursor=next_cursor)

    @staticmethod
    def _around(criteria: PropertySearchCriteria) -> Any:
        """The same rule as ``TenantDemand.is_around``, in SQL."""
        assert criteria.near is not None
        distance = _distance_km(criteria.near)
        named = (
            PropertySearchDocumentRow.neighbourhood.in_(criteria.neighbourhoods)
            if criteria.neighbourhoods
            else None
        )
        unmeasured = PropertySearchDocumentRow.latitude.is_(None)
        if named is None:
            return or_(unmeasured, distance <= criteria.near_radius_km)
        return or_(named, and_(PropertySearchDocumentRow.latitude.is_not(None),
                               distance <= criteria.near_radius_km))

    @staticmethod
    def _is_sortable(criteria: PropertySearchCriteria, sort: str) -> bool:
        if sort == "price_nearest":
            return criteria.near_price_xaf is not None
        return True

    async def count(self, criteria: PropertySearchCriteria) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(PropertySearchDocumentRow)
            .where(*self._filters(criteria))
        )
        return int(result.scalar_one())

    def _apply_sort(
        self,
        statement: Select[Any],
        criteria: PropertySearchCriteria,
        sort: str,
        cursor: str | None,
    ) -> Select[Any]:
        if criteria.near is not None:
            # A shared location is the strongest wish he expressed: the nearest
            # lodgings first, the unmeasured last, the price breaking ties.
            statement = statement.order_by(_distance_km(criteria.near).asc().nulls_last())
        if sort == "price_nearest":
            anchor = int(criteria.near_price_xaf or 0)
            return statement.order_by(
                func.abs(PropertySearchDocumentRow.public_price_xaf - anchor).asc(),
                PropertySearchDocumentRow.public_price_xaf.asc(),
                PropertySearchDocumentRow.property_id.asc(),
            )
        if sort == "recent":
            statement = statement.order_by(
                PropertySearchDocumentRow.source_created_at.desc(),
                PropertySearchDocumentRow.property_id.desc(),
            )
            if cursor:
                _, value, tie = decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        PropertySearchDocumentRow.source_created_at
                        < datetime.fromisoformat(value),
                        and_(
                            PropertySearchDocumentRow.source_created_at
                            == datetime.fromisoformat(value),
                            PropertySearchDocumentRow.property_id < tie,
                        ),
                    )
                )
        elif sort == "price_asc":
            statement = statement.order_by(
                PropertySearchDocumentRow.public_price_xaf.asc(),
                PropertySearchDocumentRow.property_id.asc(),
            )
            if cursor:
                _, value, tie = decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        PropertySearchDocumentRow.public_price_xaf > int(value),
                        and_(
                            PropertySearchDocumentRow.public_price_xaf == int(value),
                            PropertySearchDocumentRow.property_id > tie,
                        ),
                    )
                )
        else:
            statement = statement.order_by(
                PropertySearchDocumentRow.public_price_xaf.desc(),
                PropertySearchDocumentRow.property_id.desc(),
            )
            if cursor:
                _, value, tie = decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        PropertySearchDocumentRow.public_price_xaf < int(value),
                        and_(
                            PropertySearchDocumentRow.public_price_xaf == int(value),
                            PropertySearchDocumentRow.property_id < tie,
                        ),
                    )
                )
        return statement

    @staticmethod
    def _sort_column(sort: str) -> str:
        return {
            "recent": "source_created_at",
            "price_asc": "public_price_xaf",
            "price_desc": "public_price_xaf",
        }[sort]

    @staticmethod
    def _to_item(row: PropertySearchDocumentRow) -> dict[str, Any]:
        return {
            "property_id": str(row.property_id),
            "landlord_id": str(row.landlord_id),
            # How many identical lodgings this one listing stands for (§5).
            # Exposed so a result can say "2 studios" instead of implying one.
            "lodgings": row.lodgings,
            "type": row.property_type,
            "city": row.city,
            "neighbourhood": row.neighbourhood,
            "price_xaf": row.public_price_xaf,
            "rent_xaf": row.rent_xaf,
            "charges_xaf": row.charges_xaf,
            "charging_policy": row.charging_policy,
            "bedrooms": row.bedrooms,
            "bathrooms": row.bathrooms,
            "surface_m2": row.surface_m2,
            "minimum_duration_months": row.minimum_duration_months,
            "available_from": row.available_from.isoformat() if row.available_from else None,
            "amenities": row.amenities,
            "is_verified": row.is_verified,
            "media_object_keys": row.media_object_keys,
            "published_at": row.published_at.isoformat() if row.published_at else None,
        }

def _distance_km(point: tuple[float, float]) -> Any:
    """Great-circle distance from ``point`` to a document, in SQL (haversine).

    Plain trigonometry rather than PostGIS: at a few thousand listings a
    sequential evaluation is microseconds, and an extension is one more thing
    to install, migrate and back up.
    """
    latitude, longitude = point
    doc_lat = func.radians(PropertySearchDocumentRow.latitude)
    inner = func.power(func.sin((doc_lat - func.radians(latitude)) / 2), 2) + func.cos(
        func.radians(latitude)
    ) * func.cos(doc_lat) * func.power(
        func.sin((func.radians(PropertySearchDocumentRow.longitude) - func.radians(longitude)) / 2),
        2,
    )
    return 2 * 6371.0 * func.asin(func.least(1.0, func.sqrt(inner)))
