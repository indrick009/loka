"""Infrastructure wiring for the Landlord context.

The application layer resolves repositories through the unit of work, so the
mapping from a repository name to its adapter lives here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.landlord.application.ports import (
    EvidenceUrlProvider,
    VerificationAdjudicator,
)
from loka.bounded_contexts.landlord.domain.repositories.verification_repository import (
    LANDLORD_PROFILE_REPOSITORY,
    VERIFICATION_REQUEST_REPOSITORY,
)
from loka.bounded_contexts.landlord.infrastructure.adjudication import (
    MediaEvidenceUrlProvider,
    OpenRouterVerificationAdjudicator,
)
from loka.bounded_contexts.landlord.infrastructure.directory import SqlAlchemyLandlordDirectory
from loka.bounded_contexts.landlord.infrastructure.persistence.verification_repository import (
    SqlAlchemyLandlordProfileRepository,
    SqlAlchemyVerificationRequestRepository,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from loka.shared.infrastructure.object_storage.media import S3MediaStorage

LANDLORD_DIRECTORY = "landlord_directory"


def evidence_url_provider(settings: Settings) -> EvidenceUrlProvider:
    """Pre-signs identity evidence; the short TTL keeps a URL from outliving the check."""
    storage = S3MediaStorage(settings.object_storage)
    return MediaEvidenceUrlProvider(
        storage, ttl_seconds=settings.object_storage.url_ttl_seconds
    )


def verification_adjudicator(settings: Settings) -> VerificationAdjudicator | None:
    """The model, or ``None`` when the AI pipeline is switched off.

    Returning ``None`` keeps the platform fully functional without a model: the
    use case simply leaves every submission for an operator.
    """
    if not settings.ai.pipeline_enabled:
        return None
    return OpenRouterVerificationAdjudicator(settings.ai)


def _landlord_directory(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyLandlordDirectory(uow.session)


def _landlord_profile(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyLandlordProfileRepository(uow.session, unit_of_work=uow)


def _verification_request(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyVerificationRequestRepository(uow.session, unit_of_work=uow)


LANDLORD_REPOSITORY_FACTORIES: dict[str, Any] = {
    LANDLORD_DIRECTORY: _landlord_directory,
    LANDLORD_PROFILE_REPOSITORY: _landlord_profile,
    VERIFICATION_REQUEST_REPOSITORY: _verification_request,
}