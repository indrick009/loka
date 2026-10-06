"""Read side for property search.

Never touches the write model directly and always uses keyset pagination:
``OFFSET`` degrades linearly and is unusable at catalogue scale.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertyMediaRow,
    PropertyRow,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50


@dataclass(frozen=True, slots=True)
class PropertySearchCriteria:
    city: str | None = None
    neighbourhoods: tuple[str, ...] = ()
    property_types: tuple[str, ...] = ()
    min_price_xaf: int | None = None
    max_price_xaf: int | None = None
    min_bedrooms: int | None = None
    min_surface_m2: int | None = None
    available_before: date | None = None
    verified_only: bool = True
    sort: str = "recent"

    def to_filters(self) -> list[Any]:
        clauses: list[Any] = [PropertyRow.status == "AVAILABLE"]
        if self.city:
            clauses.append(PropertyRow.city == self.city)
        if self.neighbourhoods:
            clauses.append(PropertyRow.neighbourhood.in_(self.neighbourhoods))
        if self.property_types:
            clauses.append(PropertyRow.property_type.in_(self.property_types))
        if self.min_price_xaf is not None:
            clauses.append(PropertyRow.public_price_xaf >= self.min_price_xaf)
        if self.max_price_xaf is not None:
            clauses.append(PropertyRow.public_price_xaf <= self.max_price_xaf)
        if self.min_bedrooms is not None:
            clauses.append(PropertyRow.bedrooms >= self.min_bedrooms)
        if self.min_surface_m2 is not None:
            clauses.append(PropertyRow.surface_m2 >= self.min_surface_m2)
        if self.available_before is not None:
            clauses.append(
                or_(
                    PropertyRow.available_from.is_(None),
                    PropertyRow.available_from <= self.available_before,
                )
            )
        if self.verified_only:
            clauses.append(PropertyRow.is_verified.is_(True))
        return clauses


@dataclass(frozen=True, slots=True)
class PropertySearchResult:
    items: list[dict[str, Any]]
    next_cursor: str | None


def encode_cursor(sort: str, value: Any, tie_breaker: uuid.UUID) -> str:
    raw = f"{sort}|{value}|{tie_breaker}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str, uuid.UUID]:
    padded = cursor + "=" * (-len(cursor) % 4)
    sort, value, tie_breaker = base64.urlsafe_b64decode(padded.encode()).decode().split("|")
    return sort, value, uuid.UUID(tie_breaker)


class PropertySearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        criteria: PropertySearchCriteria,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> PropertySearchResult:
        size = max(1, min(limit, MAX_PAGE_SIZE))
        statement = select(PropertyRow).where(*criteria.to_filters())
        sort = criteria.sort if criteria.sort in ("recent", "price_asc", "price_desc") else "recent"
        statement = self._apply_sort(statement, sort, cursor)

        result = await self._session.execute(statement.limit(size + 1))
        rows = list(result.scalars().all())
        has_more = len(rows) > size
        rows = rows[:size]

        media_by_property = await self._load_media([row.id for row in rows])
        items = [self._to_item(row, media_by_property.get(row.id, [])) for row in rows]

        next_cursor = None
        if has_more and rows:
            last = rows[-1]
            next_cursor = encode_cursor(
                sort,
                getattr(last, self._sort_column(sort)),
                last.id,
            )
        return PropertySearchResult(items=items, next_cursor=next_cursor)

    async def count(self, criteria: PropertySearchCriteria) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(PropertyRow).where(*criteria.to_filters())
        )
        return int(result.scalar_one())

    def _apply_sort(self, statement: Select[Any], sort: str, cursor: str | None) -> Select[Any]:
        if sort == "recent":
            statement = statement.order_by(PropertyRow.created_at.desc(), PropertyRow.id.desc())
            if cursor:
                _, value, tie = decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        PropertyRow.created_at < datetime.fromisoformat(value),
                        and_(
                            PropertyRow.created_at == datetime.fromisoformat(value),
                            PropertyRow.id < tie,
                        ),
                    )
                )
        elif sort == "price_asc":
            statement = statement.order_by(PropertyRow.public_price_xaf.asc(), PropertyRow.id.asc())
            if cursor:
                _, value, tie = decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        PropertyRow.public_price_xaf > int(value),
                        and_(
                            PropertyRow.public_price_xaf == int(value),
                            PropertyRow.id > tie,
                        ),
                    )
                )
        else:
            statement = statement.order_by(
                PropertyRow.public_price_xaf.desc(), PropertyRow.id.desc()
            )
            if cursor:
                _, value, tie = decode_cursor(cursor)
                statement = statement.where(
                    or_(
                        PropertyRow.public_price_xaf < int(value),
                        and_(
                            PropertyRow.public_price_xaf == int(value),
                            PropertyRow.id < tie,
                        ),
                    )
                )
        return statement

    @staticmethod
    def _sort_column(sort: str) -> str:
        return {
            "recent": "created_at",
            "price_asc": "public_price_xaf",
            "price_desc": "public_price_xaf",
        }[sort]

    async def _load_media(self, property_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
        if not property_ids:
            return {}
        result = await self._session.execute(
            select(PropertyMediaRow.property_id, PropertyMediaRow.object_key)
            .where(
                PropertyMediaRow.property_id.in_(property_ids),
                PropertyMediaRow.status == "UPLOADED",
            )
            .order_by(PropertyMediaRow.property_id, PropertyMediaRow.position)
        )
        grouped: dict[uuid.UUID, list[str]] = {}
        for property_id, object_key in result.all():
            grouped.setdefault(property_id, []).append(object_key)
        return grouped

    @staticmethod
    def _to_item(row: PropertyRow, media_keys: list[str]) -> dict[str, Any]:
        return {
            "property_id": str(row.id),
            "landlord_id": str(row.landlord_id),
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
            "media_object_keys": media_keys,
            "published_at": row.published_at.isoformat() if row.published_at else None,
        }