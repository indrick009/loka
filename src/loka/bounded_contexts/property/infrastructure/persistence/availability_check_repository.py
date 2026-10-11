"""SQLAlchemy adapter for availability checks.

The partial unique index on ``(property_id) WHERE status = 'PENDING'`` is the
real guard against asking a landlord twice: two requests racing on the same
listing both see no open check, and the second insert is the one that fails.
``add`` turns that failure into a domain error rather than a 500, so the caller
can fall back on the question already in flight.
"""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.property.domain.entities.availability_check import (
    AvailabilityCheck,
    CheckStatus,
)
from loka.bounded_contexts.property.domain.value_objects.enums import AvailabilityAnswer
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    AvailabilityCheckRow,
)
from loka.shared.domain.errors import ConcurrencyConflict


class SqlAlchemyAvailabilityCheckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, check_id: Any) -> AvailabilityCheck | None:
        row = await self._session.get(AvailabilityCheckRow, check_id)
        return _from_row(row) if row is not None else None

    async def get_open_for_listing(self, property_id: Any) -> AvailabilityCheck | None:
        return await self._one(
            AvailabilityCheckRow.property_id == property_id,
            AvailabilityCheckRow.status == CheckStatus.PENDING.value,
        )

    async def get_open_for_landlord(self, landlord_id: Any) -> AvailabilityCheck | None:
        """The listing he was last asked about.

        Newest first: a landlord with two open questions answers the one he just
        received, and the reply carries nothing else to go on.
        """
        result = await self._session.execute(
            select(AvailabilityCheckRow)
            .where(
                AvailabilityCheckRow.landlord_id == landlord_id,
                AvailabilityCheckRow.status == CheckStatus.PENDING.value,
            )
            .order_by(AvailabilityCheckRow.asked_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return _from_row(row) if row is not None else None

    async def latest_answered_for_listing(self, property_id: Any) -> AvailabilityCheck | None:
        result = await self._session.execute(
            select(AvailabilityCheckRow)
            .where(
                AvailabilityCheckRow.property_id == property_id,
                AvailabilityCheckRow.status == CheckStatus.ANSWERED.value,
            )
            .order_by(AvailabilityCheckRow.answered_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return _from_row(row) if row is not None else None

    async def add(self, check: AvailabilityCheck) -> None:
        self._session.add(AvailabilityCheckRow(**_to_row(check)))
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConcurrencyConflict(
                "this listing already has an availability question in flight",
                context={"property_id": str(check.property_id)},
            ) from exc
        check.mark_persisted(check.version)

    async def save(self, check: AvailabilityCheck) -> None:
        """Optimistic: an answer must not land on a check someone else answered."""
        expected = check.persisted_version
        result = cast(
            CursorResult[Any],
            await self._session.execute(
                update(AvailabilityCheckRow)
                .where(
                    AvailabilityCheckRow.id == check.id,
                    AvailabilityCheckRow.version == expected,
                )
                .values(**_to_row(check), version=expected + 1)
            ),
        )
        if result.rowcount == 0:
            raise ConcurrencyConflict(
                "this availability check changed while it was being answered",
                context={"check_id": str(check.id), "expected_version": expected},
            )
        check.mark_persisted(expected + 1)

    async def _one(self, *clauses: Any) -> AvailabilityCheck | None:
        result = await self._session.execute(
            select(AvailabilityCheckRow).where(*clauses).limit(1)
        )
        row = result.scalar_one_or_none()
        return _from_row(row) if row is not None else None


def _to_row(check: AvailabilityCheck) -> dict[str, Any]:
    return {
        "id": check.id,
        "property_id": check.property_id,
        "landlord_id": check.landlord_id,
        "tenant_id": check.tenant_id,
        "status": check.status.value,
        "asked_at": check.asked_at,
        "answered_at": check.answered_at,
        "answer": check.answer.value if check.answer else None,
    }


def _from_row(row: AvailabilityCheckRow) -> AvailabilityCheck:
    check = AvailabilityCheck(
        check_id=row.id,
        property_id=row.property_id,
        landlord_id=row.landlord_id,
        tenant_id=row.tenant_id,
        now=row.asked_at,
    )
    check.pull_events()
    check.status = CheckStatus(row.status)
    check.answered_at = row.answered_at
    check.answer = AvailabilityAnswer(row.answer) if row.answer else None
    check.mark_persisted(row.version)
    return check


__all__ = ["SqlAlchemyAvailabilityCheckRepository"]
