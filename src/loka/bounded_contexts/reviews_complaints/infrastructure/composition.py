"""Infrastructure wiring for the Reviews & Complaints context.

The application layer resolves repositories through the unit of work, so the
mapping from a repository name to its adapter lives here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.reviews_complaints.application.ports import IntroductionDirectory
from loka.bounded_contexts.reviews_complaints.infrastructure.adapters.introductions import (
    NoIntroductionsConfigured,
)
from loka.bounded_contexts.reviews_complaints.infrastructure.persistence.reviews_repository import (
    SqlAlchemyComplaintRepository,
    SqlAlchemyProviderStandingRepository,
    SqlAlchemyReviewRepository,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

REVIEW_REPOSITORY = "provider_review"
COMPLAINT_REPOSITORY = "provider_complaint"
STANDING_REPOSITORY = "provider_standing"


def _provider_review(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyReviewRepository(uow.session)


def _provider_complaint(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyComplaintRepository(uow.session, unit_of_work=uow)


def _provider_standing(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyProviderStandingRepository(uow.session, unit_of_work=uow)


REVIEWS_REPOSITORY_FACTORIES: dict[str, Any] = {
    REVIEW_REPOSITORY: _provider_review,
    COMPLAINT_REPOSITORY: _provider_complaint,
    STANDING_REPOSITORY: _provider_standing,
}


def introduction_directory() -> IntroductionDirectory:
    """Bind the adapter that confirms Kora really made the introduction.

    Only the refusing one exists today: each domain owns the records that prove
    an introduction, and none of them publishes that adapter yet. Swapping it in
    is a one-line change here, which is the point of resolving it in one place.
    """
    return NoIntroductionsConfigured()
