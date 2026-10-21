"""The catalogue read behind a tenant's search.

The AI context states a search as its own narrow query — a city, a budget, a
type — and this adapter is the only place that translates it into the Property
context's read query. A change to the projection's shape therefore breaks here,
in one file, instead of in the conversation pipeline that formats its results.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from loka.bounded_contexts.ai.application.ports import (
    ConversationPropertyHit,
    ConversationSearchQuery,
)
from loka.bounded_contexts.property.application.queries.search_properties import (
    PropertySearchCriteria,
    PropertySearchQuery,
    SearchPropertiesUseCase,
)
from loka.shared.application.unit_of_work import UnitOfWork

# How far around the tenant's budget a place is worth showing. Below, a floor:
# shown far too cheap, a listing reads as an insult — a 90 000 tenant is not
# looking for a 50 000 room. Above, only a slight overshoot: a wide gap is a
# negotiation with the landlord, not something to drop on the tenant's screen.
BUDGET_LOWER_TOLERANCE = 0.20
BUDGET_UPPER_TOLERANCE = 0.05


class ConversationPropertySearch:
    """Implements the AI context's search port over the property read model."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._search = SearchPropertiesUseCase(uow)

    async def search_for_conversation(
        self, query: ConversationSearchQuery, *, limit: int = 5
    ) -> list[ConversationPropertyHit]:
        criteria = PropertySearchCriteria(
            city=query.city,
            neighbourhoods=query.neighbourhoods,
            property_types=query.property_types,
            min_bedrooms=query.min_bedrooms,
            near=query.near if query.near_exact else None,
        )
        if query.budget_xaf is not None:
            low, high = budget_band(query.budget_xaf)
            criteria = replace(
                criteria,
                min_price_xaf=low,
                max_price_xaf=high,
                near_price_xaf=query.budget_xaf,
                sort="price_nearest",
            )
        result = await self._search.execute(
            PropertySearchQuery(criteria=criteria, limit=limit)
        )
        return [_to_hit(item) for item in result.items]


def budget_band(budget: int) -> tuple[int, int]:
    """The inclusive price range a tenant's budget is matched against.

    The budget is a target, not a ceiling: the caller shows the closest prices
    first, and this bounds how far "close" may stray on either side.
    """
    return (
        int(budget * (1 - BUDGET_LOWER_TOLERANCE)),
        int(budget * (1 + BUDGET_UPPER_TOLERANCE)),
    )


def _to_hit(item: dict[str, Any]) -> ConversationPropertyHit:
    """Narrow the projection's dictionary to what a reply may show.

    Missing optional columns stay ``None`` rather than being defaulted: a
    listing without a bedroom count must not be shown as a studio with an
    unknown size, and the formatter already knows how to leave it out.
    """
    return ConversationPropertyHit(
        property_id=str(item.get("property_id") or ""),
        property_type=str(item.get("type") or ""),
        city=str(item.get("city") or ""),
        neighbourhood=item.get("neighbourhood") or None,
        price_xaf=int(item.get("price_xaf") or 0),
        bedrooms=_optional_int(item.get("bedrooms")),
        surface_m2=_optional_int(item.get("surface_m2")),
    )


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
