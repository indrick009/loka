"""The seam between a tenant's search and the catalogue's query.

The AI context states a search in its own terms; this is where those terms
become the Property context's criteria and where a projection row becomes the
slice a WhatsApp reply may show. Both directions are pinned here, because a
silent change on either side is a search that quietly stops matching.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

import pytest

from loka.bounded_contexts.ai.application.ports import (
    ConversationPropertyHit,
    ConversationSearchQuery,
)
from loka.bounded_contexts.ai.infrastructure.adapters.conversation_property_search import (
    ConversationPropertySearch,
)
from loka.bounded_contexts.property.application.queries.search_properties import (
    PropertySearchCriteria,
    PropertySearchResult,
)

pytestmark = pytest.mark.unit


@dataclass
class StubSearchReader:
    """The read model the property query would run against."""

    items: list[dict[str, Any]] = field(default_factory=list)
    criteria: PropertySearchCriteria | None = None
    limit: int = 0

    async def search(
        self,
        criteria: PropertySearchCriteria,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> PropertySearchResult:
        self.criteria = criteria
        self.limit = limit
        return PropertySearchResult(items=self.items, next_cursor=None)


@dataclass
class FakeUnitOfWork:
    reader: StubSearchReader

    def repository(self, name: str) -> Any:
        assert name == "property_search"
        return self.reader


def projection_item(**overrides: Any) -> dict[str, Any]:
    """A row as the projection returns it, internal columns included."""
    item: dict[str, Any] = {
        "property_id": "11111111-1111-1111-1111-111111111111",
        "landlord_id": "99999999-9999-9999-9999-999999999999",
        "type": "APARTMENT",
        "city": "Yaoundé",
        "neighbourhood": "Bastos",
        "price_xaf": 150_000,
        "rent_xaf": 140_000,
        "charges_xaf": 10_000,
        "charging_policy": "EXTRA",
        "bedrooms": 3,
        "bathrooms": 2,
        "surface_m2": 90,
        "amenities": {"parking": True},
        "is_verified": True,
        "media_object_keys": ["landlords/1/11111111/1.jpg"],
    }
    item.update(overrides)
    return item


class TestTranslation:
    async def test_the_conversation_terms_become_search_criteria(self) -> None:
        reader = StubSearchReader()
        port = ConversationPropertySearch(FakeUnitOfWork(reader))

        await port.search_for_conversation(
            ConversationSearchQuery(
                city="Yaoundé",
                neighbourhoods=("Bastos",),
                property_types=("APARTMENT", "STUDIO"),
                max_price_xaf=180_000,
                min_bedrooms=2,
            ),
            limit=5,
        )

        assert reader.criteria == PropertySearchCriteria(
            city="Yaoundé",
            neighbourhoods=("Bastos",),
            property_types=("APARTMENT", "STUDIO"),
            max_price_xaf=180_000,
            min_bedrooms=2,
        )
        assert reader.limit == 5

    async def test_a_row_becomes_the_slice_a_reply_may_show(self) -> None:
        reader = StubSearchReader(items=[projection_item()])
        port = ConversationPropertySearch(FakeUnitOfWork(reader))

        hits = await port.search_for_conversation(ConversationSearchQuery(city="Yaoundé"))

        assert hits == [
            ConversationPropertyHit(
                property_id="11111111-1111-1111-1111-111111111111",
                property_type="APARTMENT",
                city="Yaoundé",
                neighbourhood="Bastos",
                price_xaf=150_000,
                bedrooms=3,
                surface_m2=90,
            )
        ]
        # The landlord, the media keys and the verification flag are the
        # catalogue's business; a reply that quoted them would leak them.
        assert {field.name for field in dataclasses.fields(hits[0])} == {
            "property_id",
            "property_type",
            "city",
            "neighbourhood",
            "price_xaf",
            "bedrooms",
            "surface_m2",
        }

    async def test_an_absent_optional_column_stays_absent(self) -> None:
        reader = StubSearchReader(
            items=[projection_item(bedrooms=None, surface_m2=None, neighbourhood=None)]
        )
        port = ConversationPropertySearch(FakeUnitOfWork(reader))

        hits = await port.search_for_conversation(ConversationSearchQuery(city="Douala"))

        assert hits[0].bedrooms is None
        assert hits[0].surface_m2 is None
        assert hits[0].neighbourhood is None
