"""Infrastructure wiring for the Payment context.

The application layer resolves repositories through the unit of work, so the
mapping from a repository name to its adapter lives here and nowhere else.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.payment.infrastructure.persistence.payment_repository import (
    SqlAlchemyAccessGrantRepository,
    SqlAlchemyPaymentCallbackRepository,
    SqlAlchemyPaymentRepository,
)
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

PAYMENT_REPOSITORY = "payment"
PAYMENT_GRANT_REPOSITORY = "payment_grant"
PAYMENT_CALLBACK_REPOSITORY = "payment_callback"


def _payment(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPaymentRepository(uow.session, unit_of_work=uow)


def _payment_grant(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyAccessGrantRepository(uow.session)


def _payment_callback(uow: SqlAlchemyUnitOfWork) -> Any:
    return SqlAlchemyPaymentCallbackRepository(uow.session)


PAYMENT_REPOSITORY_FACTORIES: dict[str, Any] = {
    PAYMENT_REPOSITORY: _payment,
    PAYMENT_GRANT_REPOSITORY: _payment_grant,
    PAYMENT_CALLBACK_REPOSITORY: _payment_callback,
}