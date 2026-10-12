"""The avis: a mark one introduced user leaves on one offer.

An avis is **valorisant** — it decides which offers Kora puts forward (§2) —
and it is never shown to anyone (§5). At 1 000 users/day a listing collects two
or three of them: far too few for a displayed average to mean anything, and
plenty to sort with.

Nothing here can sanction a provider. That asymmetry is the point of §2: a bad
mark that could ban someone would turn a ranking signal into a punishment, and
a complaint that only ranked would let a scammer keep trading.

An avis is written once and never edited. A mutable mark would need a history to
stay auditable, and the ranking has no use for the second version — the first
one already described the stay.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderRef,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.rating import Rating
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc


class Review(AggregateRoot):
    __slots__ = (
        "author_id",
        "introduction",
        "offer",
        "provider",
        "rating",
        "review_id",
        "submitted_at",
    )

    def __init__(
        self,
        *,
        review_id: uuid.UUID,
        provider: ProviderRef,
        offer: OfferRef,
        introduction: IntroductionRef,
        author_id: uuid.UUID,
        rating: Rating,
        now: datetime,
    ) -> None:
        super().__init__(aggregate_type="Review")
        self._assign_id(review_id)
        self.review_id = review_id
        self.provider = provider
        self.offer = offer
        self.introduction = introduction
        self.author_id = author_id
        self.rating = rating
        self.submitted_at = ensure_utc(now)
        self.record(
            "ReviewSubmitted",
            occurred_at=now,
            actor_id=author_id,
            payload={
                "review_id": str(review_id),
                "domain": offer.domain.value,
                "offer_id": str(offer.offer_id),
                "provider_id": str(provider.provider_id),
                "rating": rating.value,
            },
        )
