"""SQLAlchemy implementations of the payment write model.

One class per aggregate/record keeps each adapter behind exactly one protocol
contract: ``add`` means different things for a payment, a grant and a raw
callback, so they must not share a virtual method name.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.payment.domain.entities.service_fee_payment import (
    ServiceAccessGrant,
    ServiceFeePayment,
)
from loka.bounded_contexts.payment.domain.repositories import RawCallbackRecord
from loka.bounded_contexts.payment.infrastructure.mappers.payment_mapper import (
    callback_from_row,
    callback_to_row,
    grant_from_row,
    grant_to_row,
    payment_from_row,
    payment_to_row,
)
from loka.bounded_contexts.payment.infrastructure.persistence.models import (
    PaymentCallbackRow,
    ServiceAccessGrantRow,
    ServiceFeePaymentRow,
)
from loka.shared.application.unit_of_work import SupportsAfterCommit
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyPaymentRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, payment_id: UUID) -> ServiceFeePayment | None:
        row = await self._session.get(ServiceFeePaymentRow, payment_id)
        return payment_from_row(row) if row is not None else None

    async def get_open_for_application(self, application_id: Any) -> ServiceFeePayment | None:
        """The current attempt: FAILED rows are retryable and keep their id."""
        result = await self._session.execute(
            select(ServiceFeePaymentRow)
            .where(
                ServiceFeePaymentRow.application_id == application_id,
                ServiceFeePaymentRow.status.in_(
                    ["CREATED", "INITIATED", "PENDING_CONFIRMATION", "FAILED"]
                ),
            )
            .order_by(ServiceFeePaymentRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return payment_from_row(row) if row is not None else None

    async def get_for_application(self, application_id: Any) -> ServiceFeePayment | None:
        result = await self._session.execute(
            select(ServiceFeePaymentRow)
            .where(ServiceFeePaymentRow.application_id == application_id)
            .order_by(ServiceFeePaymentRow.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return payment_from_row(row) if row is not None else None

    async def get_by_provider_reference(
        self, provider: str, provider_reference: str
    ) -> ServiceFeePayment | None:
        result = await self._session.execute(
            select(ServiceFeePaymentRow).where(
                ServiceFeePaymentRow.provider == provider,
                ServiceFeePaymentRow.provider_reference == provider_reference,
            )
        )
        row = result.scalar_one_or_none()
        return payment_from_row(row) if row is not None else None

    async def add(self, payment: ServiceFeePayment) -> None:
        self._session.add(ServiceFeePaymentRow(**payment_to_row(payment)))
        self._mark_persisted(payment)

    async def save(
        self, payment: ServiceFeePayment, *, expected_version: int | None = None
    ) -> None:
        """Optimistic locking: the write only lands if nobody moved the row."""
        guard = expected_version if expected_version is not None else payment.persisted_version
        payload = payment_to_row(payment)
        payload.pop("id")
        payload.pop("created_at")
        payload["version"] = payment.version
        stmt = (
            update(ServiceFeePaymentRow)
            .where(ServiceFeePaymentRow.id == payment.id)
            .where(ServiceFeePaymentRow.version == guard)
            .values(**payload)
            .returning(ServiceFeePaymentRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "payment was modified concurrently",
                context={"payment_id": str(payment.id), "expected_version": guard},
            )
        self._mark_persisted(payment)

    def _mark_persisted(self, payment: ServiceFeePayment) -> None:
        """Record the new baseline version once the write is durable.

        Marking it eagerly would make the aggregate claim a version the database
        never committed, and a rollback would leave it permanently un-saveable.
        """
        version = payment.version
        if self._unit_of_work is None:
            payment.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: payment.mark_persisted(version))


class SqlAlchemyAccessGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_active_for_application(self, application_id: Any) -> ServiceAccessGrant | None:
        result = await self._session.execute(
            select(ServiceAccessGrantRow).where(
                ServiceAccessGrantRow.application_id == application_id,
                ServiceAccessGrantRow.status == "ACTIVE",
            )
        )
        row = result.scalar_one_or_none()
        return grant_from_row(row) if row is not None else None

    async def add(self, grant: ServiceAccessGrant) -> None:
        self._session.add(ServiceAccessGrantRow(**grant_to_row(grant)))


class SqlAlchemyPaymentCallbackRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, provider: str, provider_event_id: str) -> RawCallbackRecord | None:
        result = await self._session.execute(
            select(PaymentCallbackRow).where(
                PaymentCallbackRow.provider == provider,
                PaymentCallbackRow.provider_event_id == provider_event_id,
            )
        )
        row = result.scalar_one_or_none()
        return callback_from_row(row) if row is not None else None

    async def add(self, record: RawCallbackRecord) -> bool:
        """Insert the callback; False means it was already recorded.

        ``ON CONFLICT DO NOTHING`` resolves the two-worker race on the unique
        provider/event pair without aborting the surrounding transaction.
        """
        stmt = pg_insert(PaymentCallbackRow).values(**callback_to_row(record))
        stmt = stmt.on_conflict_do_nothing(
            constraint="uq_payment_callback_provider_event"
        )
        result = cast(CursorResult[Any], await self._session.execute(stmt))
        return result.rowcount > 0