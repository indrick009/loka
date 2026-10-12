"""The read the ranking needs, and the adapter the catalogue plugs into.

One batch call for a whole page of candidates: two grouped queries for twenty
providers, not forty round trips, because this runs inside a WhatsApp turn
where the user is waiting.

Every requested provider comes back, including the ones nobody has rated or
complained about. A missing entry would force each consumer to invent its own
default, and the first one to default a silent provider to zero would bury every
new landlord under the ones who have been around long enough to collect three
good marks.

Read-only: nothing here creates a standing row. A provider with no row has
never been complained about, which is the common case and must not cost a write
on a search.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from loka.bounded_contexts.reviews_complaints.application.dto.reviews_dto import (
    ProviderRankingSignal,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    StandingStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.repositories import (
    ProviderStandingRepository,
    ReviewRepository,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import ProviderKind
from loka.shared.application.unit_of_work import UnitOfWork

REVIEW_REPOSITORY = "provider_review"
STANDING_REPOSITORY = "provider_standing"


@dataclass(frozen=True, slots=True)
class ProviderRankingQuery:
    provider_kind: ProviderKind
    provider_ids: Sequence[uuid.UUID]


class ReadProviderRankingUseCase:
    name = "read_provider_ranking"

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def execute(
        self, query: ProviderRankingQuery, *, now: datetime | None = None
    ) -> dict[uuid.UUID, ProviderRankingSignal]:
        unique = list(dict.fromkeys(query.provider_ids))
        if not unique:
            return {}

        reviews: ReviewRepository = self._uow.repository(REVIEW_REPOSITORY)
        standings: ProviderStandingRepository = self._uow.repository(STANDING_REPOSITORY)
        tallies = await reviews.rating_tallies(query.provider_kind, unique)
        statuses = await standings.statuses_for(query.provider_kind, unique)

        signals: dict[uuid.UUID, ProviderRankingSignal] = {}
        for provider_id in unique:
            tally = tallies.get(provider_id)
            status = statuses.get(provider_id, StandingStatus.VISIBLE.value)
            signals[provider_id] = ProviderRankingSignal(
                provider_id=provider_id,
                put_forward=status == StandingStatus.VISIBLE.value,
                average_rating=tally.average if tally else None,
                review_count=tally.count if tally else 0,
            )
        return signals


class ProviderRankingReader:
    """The adapter behind the catalogue's own port.

    It answers in marks and a boolean and exposes no review: a consumer that
    could reach the authors or the complaint texts would be one refactor away
    from displaying them, and §5 is explicit that none of this is public.
    """

    def __init__(self, uow: UnitOfWork, *, provider_kind: ProviderKind) -> None:
        self._use_case = ReadProviderRankingUseCase(uow)
        self._provider_kind = provider_kind

    async def ranking_signals(
        self, provider_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, ProviderRankingSignal]:
        return await self._use_case.execute(
            ProviderRankingQuery(provider_kind=self._provider_kind, provider_ids=provider_ids)
        )
