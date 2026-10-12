"""Record one avis.

Two gates, both from §7, and they are the only reason this use case is not a
single insert.

The author must have been introduced to the provider by Kora, and one
introduction may leave one mark. Drop either gate and the ranking signal
becomes a lever: a landlord rates his own three lodgings five times each and
outranks everybody, at a cost of nothing, because nobody is ever shown the
reviews and so nobody can notice.

Nothing is returned about the provider. The caller learns that its mark landed
and no more — an avis is internal (§5), and a use case that answered with the
running average would be the first step towards printing it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import structlog

from loka.bounded_contexts.reviews_complaints.application.dto.reviews_dto import (
    ReviewAcknowledgement,
)
from loka.bounded_contexts.reviews_complaints.application.ports import IntroductionDirectory
from loka.bounded_contexts.reviews_complaints.domain.entities.review import Review
from loka.bounded_contexts.reviews_complaints.domain.exceptions import (
    AlreadySpoken,
    NoStandingToSpeak,
)
from loka.bounded_contexts.reviews_complaints.domain.repositories import ReviewRepository
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderRef,
    ServiceDomain,
    provider_kind_for,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.rating import Rating
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.identifiers import new_id

REVIEW_REPOSITORY = "provider_review"


@dataclass(frozen=True, slots=True)
class SubmitReviewCommand:
    domain: ServiceDomain
    author_id: uuid.UUID
    provider_id: uuid.UUID
    offer_id: uuid.UUID
    introduction_id: uuid.UUID
    rating: int


class SubmitReviewUseCase:
    name = "submit_review"

    def __init__(
        self,
        uow: UnitOfWork,
        *,
        introductions: IntroductionDirectory,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._uow = uow
        self._introductions = introductions
        self._logger = logger or structlog.get_logger(self.name)

    async def execute(
        self, command: SubmitReviewCommand, *, now: datetime
    ) -> ReviewAcknowledgement:
        reviews: ReviewRepository = self._uow.repository(REVIEW_REPOSITORY)

        provider = ProviderRef(
            kind=provider_kind_for(command.domain), provider_id=command.provider_id
        )
        offer = OfferRef(domain=command.domain, offer_id=command.offer_id)
        introduction = IntroductionRef(
            domain=command.domain, introduction_id=command.introduction_id
        )
        rating = Rating(command.rating)

        if command.author_id == command.provider_id:
            # The cheapest attack there is, and the one the introduction check
            # would miss in a domain where a provider can also be a client.
            raise ValidationFailed(
                "a provider cannot review his own offer",
                context={"provider_id": str(command.provider_id)},
            )

        if not await self._introductions.introduced(
            introduction=introduction,
            user_id=command.author_id,
            provider=provider,
            offer=offer,
        ):
            raise NoStandingToSpeak(
                "Kora did not introduce this user to this provider",
                context={
                    "domain": command.domain.value,
                    "introduction_id": str(command.introduction_id),
                },
            )

        if await reviews.exists_for_introduction(introduction):
            raise AlreadySpoken(
                "this introduction has already left a review",
                context={"introduction_id": str(command.introduction_id)},
            )

        review = Review(
            review_id=new_id(),
            provider=provider,
            offer=offer,
            introduction=introduction,
            author_id=command.author_id,
            rating=rating,
            now=now,
        )
        await reviews.add(review)
        self._uow.collect(review)
        await self._uow.commit()
        self._logger.info(
            "review_submitted",
            review_id=str(review.id),
            domain=command.domain.value,
            provider_id=str(command.provider_id),
            offer_id=str(command.offer_id),
            rating=rating.value,
        )
        return ReviewAcknowledgement(
            review_id=str(review.id),
            provider_id=str(provider.provider_id),
            rating=rating.value,
            submitted_at=review.submitted_at,
        )
