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
from loka.bounded_contexts.payment.application.ports import PaymentGateway
from loka.bounded_contexts.payment.application.use_cases.get_service_access_status import (
    GetServiceAccessStatusUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.handle_payment_callback import (
    HandlePaymentCallbackUseCase,
)
from loka.bounded_contexts.payment.application.use_cases.initiate_service_fee import (
    InitiateServiceFeeUseCase,
)
from loka.bounded_contexts.payment.infrastructure.composition import (
    PAYMENT_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.payment.infrastructure.gateways.mock_gateway import (
    MockPaymentGateway,
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
from loka.bounded_contexts.rental.application.use_cases.confirm_rental import (
    ConfirmRentalUseCase,
)
from loka.bounded_contexts.rental.application.use_cases.decide_rental_application import (
    DecideRentalApplicationUseCase,
)
from loka.bounded_contexts.rental.application.use_cases.express_rental_interest import (
    ExpressRentalInterestUseCase,
)
from loka.bounded_contexts.rental.application.use_cases.get_rental_application import (
    GetRentalApplicationUseCase,
)
from loka.bounded_contexts.rental.application.use_cases.propose_rental_terms import (
    ProposeRentalTermsUseCase,
)
from loka.bounded_contexts.rental.application.use_cases.withdraw_rental_application import (
    WithdrawRentalApplicationUseCase,
)
from loka.bounded_contexts.rental.infrastructure.composition import (
    RENTAL_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.rental.infrastructure.composition import (
    property_directory as rental_property_directory,
)
from loka.bounded_contexts.visit.application.use_cases.cancel_visit import CancelVisitUseCase
from loka.bounded_contexts.visit.application.use_cases.complete_visit import (
    CompleteVisitUseCase,
)
from loka.bounded_contexts.visit.application.use_cases.get_visit import GetVisitUseCase
from loka.bounded_contexts.visit.application.use_cases.request_visit import (
    RequestVisitUseCase,
)
from loka.bounded_contexts.visit.application.use_cases.schedule_visit import (
    ScheduleVisitUseCase,
)
from loka.bounded_contexts.visit.infrastructure.composition import (
    VISIT_REPOSITORY_FACTORIES,
)
from loka.bounded_contexts.visit.infrastructure.composition import (
    property_directory as visit_property_directory,
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
    RENTAL_REPOSITORY_FACTORIES,
    VISIT_REPOSITORY_FACTORIES,
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
    """Bind the configured payment provider. Only the mock exists today."""
    if settings.payment.gateway == "mock":
        return MockPaymentGateway(
            webhook_secret=settings.payment.webhook_secret.get_secret_value()
        )
    raise ValueError(f"unsupported payment gateway: {settings.payment.gateway!r}")


def initiate_service_fee_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> InitiateServiceFeeUseCase:
    return InitiateServiceFeeUseCase(
        uow,
        gateway=payment_gateway(settings),
        service_fee_xaf=settings.payment.service_fee_xaf,
    )


def handle_payment_callback_use_case(
    uow: SqlAlchemyUnitOfWork, settings: Settings
) -> HandlePaymentCallbackUseCase:
    return HandlePaymentCallbackUseCase(uow, gateway=payment_gateway(settings))


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


def express_rental_interest_use_case(uow: SqlAlchemyUnitOfWork) -> ExpressRentalInterestUseCase:
    return ExpressRentalInterestUseCase(uow, properties=rental_property_directory(uow))


def propose_rental_terms_use_case(uow: SqlAlchemyUnitOfWork) -> ProposeRentalTermsUseCase:
    return ProposeRentalTermsUseCase(uow, landlords=uow.repository(LANDLORD_DIRECTORY))


def decide_rental_application_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> DecideRentalApplicationUseCase:
    return DecideRentalApplicationUseCase(
        uow,
        landlords=uow.repository(LANDLORD_DIRECTORY),
        properties=rental_property_directory(uow),
    )


def confirm_rental_use_case(uow: SqlAlchemyUnitOfWork) -> ConfirmRentalUseCase:
    return ConfirmRentalUseCase(
        uow,
        landlords=uow.repository(LANDLORD_DIRECTORY),
        properties=rental_property_directory(uow),
    )


def withdraw_rental_application_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> WithdrawRentalApplicationUseCase:
    return WithdrawRentalApplicationUseCase(
        uow, properties=rental_property_directory(uow)
    )


def get_rental_application_use_case(
    uow: SqlAlchemyUnitOfWork,
) -> GetRentalApplicationUseCase:
    return GetRentalApplicationUseCase(uow, landlords=uow.repository(LANDLORD_DIRECTORY))


def request_visit_use_case(uow: SqlAlchemyUnitOfWork) -> RequestVisitUseCase:
    return RequestVisitUseCase(uow, properties=visit_property_directory(uow))


def schedule_visit_use_case(uow: SqlAlchemyUnitOfWork) -> ScheduleVisitUseCase:
    return ScheduleVisitUseCase(uow, landlords=uow.repository(LANDLORD_DIRECTORY))


def cancel_visit_use_case(uow: SqlAlchemyUnitOfWork) -> CancelVisitUseCase:
    return CancelVisitUseCase(uow, landlords=uow.repository(LANDLORD_DIRECTORY))


def complete_visit_use_case(uow: SqlAlchemyUnitOfWork) -> CompleteVisitUseCase:
    return CompleteVisitUseCase(uow, landlords=uow.repository(LANDLORD_DIRECTORY))


def get_visit_use_case(uow: SqlAlchemyUnitOfWork) -> GetVisitUseCase:
    return GetVisitUseCase(uow, landlords=uow.repository(LANDLORD_DIRECTORY))