"""Composition root.

Single place where adapters are bound to ports and use cases are assembled.
Nothing else in the codebase builds a repository by hand, so swapping an adapter
for a test double stays a local change.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.fraud.application.use_cases.evaluate_risk import (
    EvaluateRiskUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.get_escalation_queue import (
    GetEscalationQueueUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.get_fraud_summary import (
    GetFraudSummaryUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.get_risk_profile import (
    GetRiskProfileUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.list_reports import (
    ListReportsUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.review_report import (
    ReviewReportUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.review_risk import (
    ReviewRiskUseCase,
)
from loka.bounded_contexts.fraud.application.use_cases.submit_report import (
    SubmitReportUseCase,
)
from loka.bounded_contexts.fraud.infrastructure.composition import (
    FRAUD_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.landlord.application.use_cases.adjudicate_landlord_verification import (
    AdjudicateLandlordVerificationUseCase,
)
from loka.bounded_contexts.landlord.application.use_cases.get_landlord_verification_status import (
    GetLandlordVerificationStatusUseCase,
)
from loka.bounded_contexts.landlord.application.use_cases.request_landlord_status import (
    RequestLandlordStatusUseCase,
)
from loka.bounded_contexts.landlord.application.use_cases.review_landlord_verification import (
    ReviewLandlordVerificationUseCase,
)
from loka.bounded_contexts.landlord.application.use_cases.submit_landlord_verification import (
    SubmitLandlordVerificationUseCase,
)
from loka.bounded_contexts.landlord.infrastructure.composition import (
    LANDLORD_DIRECTORY,
    LANDLORD_REPOSITORY_FACTORIES,
    evidence_url_provider,
    verification_adjudicator,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    MESSAGING_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.payment.application.ports import (
    PaymentGateway,
    PaymentStatusSource,
)
from loka.bounded_contexts.payment.application.use_cases.get_service_access_status import (
    GetServiceAccessStatusUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.handle_payment_callback import (
    HandlePaymentCallbackUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.initiate_service_fee import (
    InitiateServiceFeeUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.reconcile_pending_payments import (
    ReconcilePendingPaymentsUseCase,
)
from loka.bounded_contexts.payment.infrastructure.composition import (
    PAYMENT_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.payment.infrastructure.gateways.fapshi_gateway import (
    FapshiPaymentGateway,
)
from loka.bounded_contexts.payment.infrastructure.gateways.mock_gateway import (
    MockPaymentGateway,
)
from loka.bounded_contexts.property.application.use_cases.check_availability import (
    AnswerAvailabilityCheckUseCase,
    ListingAvailabilityReader,
    RequestAvailabilityCheckUseCase,
)
from loka.bounded_contexts.property.application.use_cases.create_property_from_conversation import (
    CreatePropertyFromConversationUseCase,
)
from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyUseCase,
)
from loka.bounded_contexts.property.infrastructure.composition import (
    PROPERTY_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.reviews_complaints.application.use_cases.decide_complaint import (
    DecideComplaintUseCase,
    ReinstateProviderUseCase,
)
from loka.bounded_contexts.reviews_complaints.application.use_cases.file_complaint import (
    FileComplaintUseCase,
)
from loka.bounded_contexts.reviews_complaints.application.use_cases.get_complaint_queue import (
    GetComplaintQueueUseCase,
)
from loka.bounded_contexts.reviews_complaints.application.use_cases.read_provider_ranking import (
    ProviderRankingReader,
)
from loka.bounded_contexts.reviews_complaints.application.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    ProviderKind,
)
from loka.bounded_contexts.reviews_complaints.infrastructure.composition import (
    introduction_directory,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

_CONTEXT_FACTORIES: tuple[dict[str, Any], ...] = (
    LANDLORD_REPOSITORY_FACTORIES,
    PROPERTY_REPOSITORY_FACTORIES,
    MESSAGING_REPOSITORY_FACTORIES,
    PAYMENT_REPOSITORY_FACTORIES,
    FRAUD_REPOSITORY_FACTORIES,
)


def repository_factories() -> dict[str, Any]:
    """Every repository reachable through ``UnitOfWork.repository(name)``."""
    merged: dict[str, Any] = {}
    for factories in _CONTEXT_FACTORIES:
        merged.update(factories)
    return merged


def unit_of_work(database: Database) -> SqlAlchemyUnitOfWork:
    """A unit of work wired to the real SQLAlchemy adapters."""
    return SqlAlchemyUnitOfWork(database.sessions, repository_factories=repository_factories())


def publish_property_use_case(
    uow: SqlAlchemyUnitOfWork, *, max_active_properties: int = 25
) -> PublishPropertyUseCase:
    """Resolve the publication ports from the caller's unit of work.

    Taking the unit of work as an argument keeps the whole use case inside one
    transaction: the aggregate, the quota query and the verification lookup all
    read through the same session.
    """
    return PublishPropertyUseCase(
        uow,
        landlords=uow.repository(LANDLORD_DIRECTORY),
        max_active_properties=max_active_properties,
    )


def create_property_from_conversation_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> CreatePropertyFromConversationUseCase:
    """Turn a confirmed WhatsApp listing conversation into a property draft.

    Resolves the landlord directory from the caller's unit of work, so the
    profile lookup, the draft write and the session update share one transaction
    — the confirmation is atomic or it did not happen.
    """
    return CreatePropertyFromConversationUseCase(
        uow, landlords=uow.repository(LANDLORD_DIRECTORY)
    )


def payment_gateway(settings: Settings) -> PaymentGateway:
    """Bind the configured payment provider.

    An unknown name raises rather than quietly falling back to the mock: a
    production deploy with a typo in ``PAYMENT__GATEWAY`` would otherwise mint
    fake references and grant every unlock for free.
    """
    if settings.payment.gateway == "mock":
        return MockPaymentGateway(
            webhook_secret=settings.payment.webhook_secret.get_secret_value()
        )
    if settings.payment.gateway == "fapshi":
        return FapshiPaymentGateway(settings.payment.fapshi)
    raise ValueError(f"unsupported payment gateway: {settings.payment.gateway!r}")


def payment_status_source(settings: Settings) -> PaymentStatusSource | None:
    """The provider's own answer, where the provider offers one.

    ``None`` is a complete answer, not a missing feature: a gateway that signs
    its webhooks has already proved what it said, and there is nothing to go
    and confirm.
    """
    gateway = payment_gateway(settings)
    return gateway if isinstance(gateway, PaymentStatusSource) else None


def initiate_service_fee_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> InitiateServiceFeeUseCase:
    return InitiateServiceFeeUseCase(
        uow,
        gateway=payment_gateway(settings),
        # The catalogue's answer to Payment's own question, and the only thing
        # Payment learns about a listing.
        availability=ListingAvailabilityReader(uow),
        service_fee_xaf=settings.payment.service_fee_xaf,
    )


def request_availability_check_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> RequestAvailabilityCheckUseCase:
    return RequestAvailabilityCheckUseCase(uow)


def answer_availability_check_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> AnswerAvailabilityCheckUseCase:
    return AnswerAvailabilityCheckUseCase(uow)


def handle_payment_callback_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> HandlePaymentCallbackUseCase:
    return HandlePaymentCallbackUseCase(
        uow,
        gateway=payment_gateway(settings),
        status_source=payment_status_source(settings),
    )


def reconcile_pending_payments_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> ReconcilePendingPaymentsUseCase:
    source = payment_status_source(settings)
    if source is None:
        raise ValueError(
            f"{settings.payment.gateway!r} cannot be reconciled: it exposes no status endpoint"
        )
    return ReconcilePendingPaymentsUseCase(uow, status_source=source)


def get_service_access_status_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> GetServiceAccessStatusUseCase:
    return GetServiceAccessStatusUseCase(uow)


def evaluate_risk_use_case(uow: SqlAlchemyUnitOfWork) -> EvaluateRiskUseCase:
    return EvaluateRiskUseCase(uow)


def submit_report_use_case(uow: SqlAlchemyUnitOfWork) -> SubmitReportUseCase:
    return SubmitReportUseCase(uow)


def review_report_use_case(uow: SqlAlchemyUnitOfWork) -> ReviewReportUseCase:
    return ReviewReportUseCase(uow)


def review_risk_use_case(uow: SqlAlchemyUnitOfWork) -> ReviewRiskUseCase:
    return ReviewRiskUseCase(uow)


def get_risk_profile_use_case(uow: SqlAlchemyUnitOfWork) -> GetRiskProfileUseCase:
    return GetRiskProfileUseCase(uow)


def get_escalation_queue_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> GetEscalationQueueUseCase:
    return GetEscalationQueueUseCase(uow)


def list_reports_use_case(uow: SqlAlchemyUnitOfWork) -> ListReportsUseCase:
    return ListReportsUseCase(uow)


def get_fraud_summary_use_case(uow: SqlAlchemyUnitOfWork) -> GetFraudSummaryUseCase:
    return GetFraudSummaryUseCase(uow)


def request_landlord_status_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> RequestLandlordStatusUseCase:
    return RequestLandlordStatusUseCase(uow)


def submit_landlord_verification_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> SubmitLandlordVerificationUseCase:
    return SubmitLandlordVerificationUseCase(uow)


def review_landlord_verification_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> ReviewLandlordVerificationUseCase:
    return ReviewLandlordVerificationUseCase(uow)


def get_landlord_verification_status_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> GetLandlordVerificationStatusUseCase:
    return GetLandlordVerificationStatusUseCase(uow)


def adjudicate_landlord_verification_use_case(
    uow: SqlAlchemyUnitOfWork, *, settings: Settings
) -> AdjudicateLandlordVerificationUseCase:
    """Assemble the AI pre-decision from the configured model and object storage."""
    return AdjudicateLandlordVerificationUseCase(
        uow,
        adjudicator=verification_adjudicator(settings),
        urls=evidence_url_provider(settings),
    )


def submit_review_use_case(uow: SqlAlchemyUnitOfWork) -> SubmitReviewUseCase:
    """An avis, gated on the introduction Kora actually made."""
    return SubmitReviewUseCase(uow, introductions=introduction_directory())


def file_complaint_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> FileComplaintUseCase:
    return FileComplaintUseCase(
        uow,
        introductions=introduction_directory(),
        hide_threshold=settings.reviews.complaint_hide_threshold,
    )


def decide_complaint_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> DecideComplaintUseCase:
    """The human decision.

    The threshold is passed in so a dismissal can release a provider the
    automatic hide had taken out of search.
    """
    return DecideComplaintUseCase(
        uow, hide_threshold=settings.reviews.complaint_hide_threshold
    )


def reinstate_provider_use_case(uow: SqlAlchemyUnitOfWork) -> ReinstateProviderUseCase:
    return ReinstateProviderUseCase(uow)


def get_complaint_queue_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> GetComplaintQueueUseCase:
    return GetComplaintQueueUseCase(
        uow, response_time_hours=settings.reviews.complaint_response_time_hours
    )


def landlord_ranking_reader(uow: SqlAlchemyUnitOfWork) -> ProviderRankingReader:
    """The ranking signal for Housing, bound to the one provider kind it offers.

    Housing ranks landlords, so the kind is fixed here rather than taken as an
    argument: a catalogue that could pass a kind could pass the wrong one, and
    then a restaurateur's marks would order lodgings.
    """
    return ProviderRankingReader(uow, provider_kind=ProviderKind.LANDLORD)
