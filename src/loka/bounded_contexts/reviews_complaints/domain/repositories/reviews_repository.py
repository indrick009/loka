"""Persistence contracts for reviews, complaints and provider standing.

The application layer knows only these protocols; the SQLAlchemy adapters live
in the infrastructure layer and share the unit of work's session.

Three separate protocols rather than one: ``add`` means a different thing for a
review, a complaint and a standing, and the counting methods exist only so the
standing can be recomputed from the complaints table instead of being
incremented in place.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from loka.bounded_contexts.reviews_complaints.domain.entities.complaint import (
    Complaint,
    ComplaintStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    ProviderStanding,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.review import Review
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    ProviderKind,
    ProviderRef,
)


@dataclass(frozen=True, slots=True)
class RatingTally:
    """What a provider's reviews add up to, for ordering only.

    ``average`` is a float because ordering needs the fraction; it is never
    rounded into a star count for display, which §5 rules out. ``count`` rides
    along so the ranking can prefer a provider judged five times over one
    judged once with the same mean.
    """

    provider_id: uuid.UUID
    average: float
    count: int


@dataclass(frozen=True, slots=True)
class ComplaintLoad:
    """How many complaints a provider is carrying, split by what is owed.

    ``pending`` drives the hide threshold; ``upheld`` is history a human reads
    before banning.
    """

    pending: int
    upheld: int


class ReviewRepository(Protocol):
    async def add(self, review: Review) -> None: ...

    async def exists_for_introduction(self, introduction: IntroductionRef) -> bool: ...

    async def rating_tallies(
        self, kind: ProviderKind, provider_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, RatingTally]:
        """One grouped query for a whole page of candidates.

        Batched because ranking happens on a result set: twenty providers must
        cost one query, not twenty, or the ranking becomes the slowest part of a
        search that has to answer inside a WhatsApp turn.

        The kind is part of the lookup, not decoration: provider ids come from
        four different tables and nothing guarantees they never collide, so a
        restaurateur's marks must not be able to rank a landlord.
        """


class ComplaintRepository(Protocol):
    async def add(self, complaint: Complaint) -> None: ...

    async def get(self, complaint_id: uuid.UUID) -> Complaint | None: ...

    async def save(self, complaint: Complaint) -> None: ...

    async def exists_for_introduction(self, introduction: IntroductionRef) -> bool: ...

    async def load_for_provider(self, provider: ProviderRef) -> ComplaintLoad: ...

    async def list_queue(
        self,
        *,
        status: ComplaintStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Complaint]:
        """Oldest first: the queue is worked in the order people were wronged."""


class ProviderStandingRepository(Protocol):
    async def get(self, provider: ProviderRef) -> ProviderStanding | None: ...

    async def add(self, standing: ProviderStanding) -> None: ...

    async def save(self, standing: ProviderStanding) -> None: ...

    async def statuses_for(
        self, kind: ProviderKind, provider_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        """Standing status per provider, for the ranking's batch read.

        A provider with no row has never been complained about, so the caller
        reads a missing entry as ``VISIBLE`` rather than paying to insert a row
        for everyone.
        """
