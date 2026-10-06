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
from loka.bounded_contexts.landlord.infrastructure.directory import (
    SqlAlchemyLandlordDirectory,
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
from loka.bounded_contexts.property.application.use_cases.publish_property import (
    PublishPropertyUseCase,
)
from loka.bounded_contexts.property.infrastructure.composition import (
    PROPERTY_REPOSITORY_FACTORIES,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

LANDLORD_DIRECTORY = "landlord_directory"

_CONTEXT_FACTORIES: tuple[dict[str, Any], ...] = (
    {LANDLORD_DIRECTORY: lambda uow: SqlAlchemyLandlordDirectory(uow.session)},
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