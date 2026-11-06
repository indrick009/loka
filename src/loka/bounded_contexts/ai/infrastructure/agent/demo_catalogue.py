"""DEMO ONLY — an in-memory catalogue so the agent can be tried without a database.

Built from the demo Yaoundé of ``interfaces/cli/demo/catalogue.py``. The
matching is deliberately naive (same city, accepted districts, type, rent up to
10 % over budget): the point is the conversation, not the ranking.
"""

from __future__ import annotations

from loka.bounded_contexts.conversation.application.ports import (
    ConversationPropertyHit,
    ConversationSearchQuery,
)
from loka.shared.domain.knowledge import market

_TOLERANCE = 1.10


class DemoCatalogue:
    def __init__(self, hits: list[ConversationPropertyHit]) -> None:
        self._hits = hits

    @classmethod
    def yaounde(cls) -> DemoCatalogue:
        from loka.interfaces.cli.demo.catalogue import LISTINGS

        hits = [
            ConversationPropertyHit(
                property_id=f"demo-{index}",
                property_type=kind,
                city="Yaoundé",
                neighbourhood=district,
                price_xaf=rent,
                bedrooms=bedrooms,
            )
            for index, (district, kind, _standing, rent, bedrooms, _count, _words) in enumerate(
                LISTINGS, start=1
            )
        ]
        return cls(hits)

    async def search_for_conversation(
        self, query: ConversationSearchQuery, *, limit: int = 5
    ) -> list[ConversationPropertyHit]:
        districts = {market.normalize(name) for name in query.neighbourhoods}
        found = [
            hit
            for hit in self._hits
            if market.normalize(hit.city) == market.normalize(query.city)
            and (not districts or market.normalize(hit.neighbourhood or "") in districts)
            and (not query.property_types or hit.property_type in query.property_types)
            and (query.budget_xaf is None or hit.price_xaf <= query.budget_xaf * _TOLERANCE)
        ]
        return sorted(found, key=lambda hit: hit.price_xaf)[:limit]
