"""Infrastructure wiring for the Fraud context.

The application layer resolves repositories through the unit of work, so the
mapping from a repository name to its adapter lives here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.fraud.infrastructure.adapters.sql_facts import (
    SqlAlchemyFraudFacts,
)
from loka.bounded_contexts.fraud.infrastructure.persistence.fraud_repository import (
    SqlAlchemyReportRepository,
    SqlAlchemyRiskProfileRepository,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

FRAUD_PROFILE_REPOSITORY = "fraud_profile"
FRAUD_REPORT_REPOSITORY = "fraud_report"
FRAUD_FACTS = "fraud_facts"


def _fraud_profile(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyRiskProfileRepository(uow.session)


def _fraud_report(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyReportRepository(uow.session)


def _fraud_facts(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyFraudFacts(uow.session)


FRAUD_REPOSITORY_FACTORIES: dict[str, Any] = {
    FRAUD_PROFILE_REPOSITORY: _fraud_profile,
    FRAUD_REPORT_REPOSITORY: _fraud_report,
    FRAUD_FACTS: _fraud_facts,
}