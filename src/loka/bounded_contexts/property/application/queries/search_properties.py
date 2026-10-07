"""Public property search.

A read query: it reads the ``property_search_documents`` projection instead of
the write model, so a search page never joins ``properties``/``property_media``
on a hot path. The projection is fed asynchronously from ``property.events``,
so a listing becomes searchable shortly after publication rather than in the
same transaction.

Keyset pagination throughout: ``OFFSET`` degrades linearly with the offset and
is unusable on a catalogue that grows.
"""

from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.application.use_case import UseCase

REPOSITORY_NAME = "property_search"

AVAILABLE = "AVAILABLE"
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


@dataclass(frozen=True, slots=True)
class PropertySearchQuery:
    criteria: PropertySearchCriteria
    cursor: str | None = None
    limit: int = DEFAULT_PAGE_SIZE


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


class PropertySearch(Protocol):
    """The read port the query depends on. Implemented by the projection adapter."""

    async def search(
        self,
        criteria: PropertySearchCriteria,
        *,
        cursor: str | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> PropertySearchResult: ...


class SearchPropertiesUseCase(UseCase[PropertySearchQuery, PropertySearchResult]):
    name = "property.search"

    def __init__(self, uow: UnitOfWork) -> None:
        super().__init__(uow)
        self._search: PropertySearch = uow.repository(REPOSITORY_NAME)

    async def execute(self, query: PropertySearchQuery) -> PropertySearchResult:
        return await self._search.search(
            query.criteria, cursor=query.cursor, limit=query.limit
        )