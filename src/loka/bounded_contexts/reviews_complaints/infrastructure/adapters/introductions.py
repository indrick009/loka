"""Introduction adapters.

Standing follows the introduction (§7), and only the owning domain can confirm
one: Housing from its settled unlock, Found from its, Dating from the contact
unlock on either side, Food from the reservation request sent to the restaurant.
Each of those adapters belongs in its own context, next to the tables it reads.

Until they exist, this is what is wired, and it refuses everything.
"""

from __future__ import annotations

import uuid

import structlog

from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    OfferRef,
    ProviderRef,
)


class NoIntroductionsConfigured:
    """Answers False to every introduction, loudly.

    Refusing is the safe default and the only honest one. An adapter that said
    True while no domain could confirm anything would accept reviews from
    people who were never introduced — a landlord rating his own listings to
    the top of search — and complaints from strangers, which is exactly what
    hides an honest provider.

    So a deployment with no adapter takes no reviews and no complaints, and says
    so on every attempt rather than failing silently.
    """

    def __init__(self, logger: structlog.stdlib.BoundLogger | None = None) -> None:
        self._logger = logger or structlog.get_logger("reviews.introductions")

    async def introduced(
        self,
        *,
        introduction: IntroductionRef,
        user_id: uuid.UUID,
        provider: ProviderRef,
        offer: OfferRef,
    ) -> bool:
        self._logger.warning(
            "introduction_directory_not_configured",
            domain=introduction.domain.value,
            introduction_id=str(introduction.introduction_id),
            provider_kind=provider.kind.value,
        )
        return False
