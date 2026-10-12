"""SQLAlchemy implementations of the Reviews & Complaints write and read models.

One class per aggregate, each behind exactly one protocol: ``add`` means a
different thing for a review, a complaint and a standing, so they must not
share a virtual method name.

The tallies are computed in SQL rather than by loading rows. At a few reviews
per offer either would work today, but the ranking read runs inside a WhatsApp
turn for a whole page of candidates, and loading them would be the one query
that grows with the catalogue.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.reviews_complaints.domain.entities.complaint import (
    Complaint,
    ComplaintStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    ProviderStanding,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.review import Review
from loka.bounded_contexts.reviews_complaints.domain.repositories import (
    ComplaintLoad,
    RatingTally,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import (
    IntroductionRef,
    ProviderKind,
    ProviderRef,
)
from loka.bounded_contexts.reviews_complaints.infrastructure.mappers.reviews_mapper import (
    complaint_from_row,
    complaint_to_row,
    review_to_row,
    standing_from_row,
    standing_to_row,
)
from loka.bounded_contexts.reviews_complaints.infrastructure.persistence.models import (
    ProviderComplaintRow,
    ProviderReviewRow,
    ProviderStandingRow,
)
from loka.shared.application.unit_of_work import SupportsAfterCommit
from loka.shared.domain.errors import ConcurrencyConflict

PENDING_STATUSES = (ComplaintStatus.OPEN.value, ComplaintStatus.UNDER_REVIEW.value)


class SqlAlchemyReviewRepository:
    """Insert-only. An avis is never edited, so there is no ``save``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, review: Review) -> None:
        self._session.add(ProviderReviewRow(**review_to_row(review)))

    async def exists_for_introduction(self, introduction: IntroductionRef) -> bool:
        result = await self._session.execute(
            select(ProviderReviewRow.id).where(
                ProviderReviewRow.domain == introduction.domain.value,
                ProviderReviewRow.introduction_id == introduction.introduction_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def rating_tallies(
        self, kind: ProviderKind, provider_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, RatingTally]:
        if not provider_ids:
            return {}
        result = await self._session.execute(
            select(
                ProviderReviewRow.provider_id,
                func.avg(ProviderReviewRow.rating),
                func.count(),
            )
            .where(
                ProviderReviewRow.provider_kind == kind.value,
                ProviderReviewRow.provider_id.in_(provider_ids),
            )
            .group_by(ProviderReviewRow.provider_id)
        )
        return {
            provider_id: RatingTally(
                provider_id=provider_id, average=float(average), count=int(count)
            )
            for provider_id, average, count in result.all()
        }


class SqlAlchemyComplaintRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def add(self, complaint: Complaint) -> None:
        self._session.add(ProviderComplaintRow(**complaint_to_row(complaint)))
        self._mark_persisted(complaint)

    async def get(self, complaint_id: uuid.UUID) -> Complaint | None:
        row = await self._session.get(ProviderComplaintRow, complaint_id)
        return complaint_from_row(row) if row is not None else None

    async def save(self, complaint: Complaint) -> None:
        """Optimistic locking: the write only lands if nobody moved the row.

        It matters here because two reviewers can open the same queue entry:
        without the guard the second decision would silently overwrite the
        first, and the audit trail would show one reviewer answering for a
        sanction someone else chose.
        """
        payload = complaint_to_row(complaint)
        payload.pop("id")
        payload.pop("filed_at")
        guard = complaint.persisted_version
        stmt = (
            update(ProviderComplaintRow)
            .where(ProviderComplaintRow.id == complaint.id)
            .where(ProviderComplaintRow.version == guard)
            .values(**payload)
            .returning(ProviderComplaintRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "complaint was modified concurrently",
                context={"complaint_id": str(complaint.id), "expected_version": guard},
            )
        self._mark_persisted(complaint)

    async def exists_for_introduction(self, introduction: IntroductionRef) -> bool:
        result = await self._session.execute(
            select(ProviderComplaintRow.id).where(
                ProviderComplaintRow.domain == introduction.domain.value,
                ProviderComplaintRow.introduction_id == introduction.introduction_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def load_for_provider(self, provider: ProviderRef) -> ComplaintLoad:
        """Count from the table, so the standing can never drift from the facts.

        Pending rows the current transaction has only staged are flushed first
        by SQLAlchemy's autoflush, which is what makes the complaint just filed
        part of the count that decides the hide.
        """
        result = await self._session.execute(
            select(ProviderComplaintRow.status, func.count())
            .where(
                ProviderComplaintRow.provider_kind == provider.kind.value,
                ProviderComplaintRow.provider_id == provider.provider_id,
            )
            .group_by(ProviderComplaintRow.status)
        )
        counts = {status: int(count) for status, count in result.all()}
        return ComplaintLoad(
            pending=sum(counts.get(status, 0) for status in PENDING_STATUSES),
            upheld=counts.get(ComplaintStatus.UPHELD.value, 0),
        )

    async def list_queue(
        self,
        *,
        status: ComplaintStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Complaint]:
        statement = select(ProviderComplaintRow)
        if status is not None:
            statement = statement.where(ProviderComplaintRow.status == status.value)
        else:
            # The default queue is what is still owed an answer; decided
            # complaints are history and would bury the work to be done.
            statement = statement.where(ProviderComplaintRow.status.in_(PENDING_STATUSES))
        result = await self._session.execute(
            statement.order_by(ProviderComplaintRow.filed_at.asc()).limit(limit).offset(offset)
        )
        return [complaint_from_row(row) for row in result.scalars().all()]

    def _mark_persisted(self, complaint: Complaint) -> None:
        """Record the new baseline version once the write is durable.

        Marking it eagerly would make the aggregate claim a version the database
        never committed, and a rollback would leave it permanently un-saveable.
        """
        version = complaint.version
        if self._unit_of_work is None:
            complaint.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: complaint.mark_persisted(version))


class SqlAlchemyProviderStandingRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        unit_of_work: SupportsAfterCommit | None = None,
    ) -> None:
        self._session = session
        self._unit_of_work = unit_of_work

    async def get(self, provider: ProviderRef) -> ProviderStanding | None:
        result = await self._session.execute(
            select(ProviderStandingRow).where(
                ProviderStandingRow.provider_kind == provider.kind.value,
                ProviderStandingRow.provider_id == provider.provider_id,
            )
        )
        row = result.scalar_one_or_none()
        return standing_from_row(row) if row is not None else None

    async def add(self, standing: ProviderStanding) -> None:
        self._session.add(ProviderStandingRow(**standing_to_row(standing)))
        self._mark_persisted(standing)

    async def save(self, standing: ProviderStanding) -> None:
        payload = standing_to_row(standing)
        payload.pop("id")
        payload.pop("created_at")
        guard = standing.persisted_version
        stmt = (
            update(ProviderStandingRow)
            .where(ProviderStandingRow.id == standing.id)
            .where(ProviderStandingRow.version == guard)
            .values(**payload)
            .returning(ProviderStandingRow.id)
        )
        result = await self._session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise ConcurrencyConflict(
                "provider standing was modified concurrently",
                context={"standing_id": str(standing.id), "expected_version": guard},
            )
        self._mark_persisted(standing)

    async def statuses_for(
        self, kind: ProviderKind, provider_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, str]:
        if not provider_ids:
            return {}
        result = await self._session.execute(
            select(ProviderStandingRow.provider_id, ProviderStandingRow.status).where(
                ProviderStandingRow.provider_kind == kind.value,
                ProviderStandingRow.provider_id.in_(provider_ids),
            )
        )
        return dict(result.all())

    def _mark_persisted(self, standing: ProviderStanding) -> None:
        version = standing.version
        if self._unit_of_work is None:
            standing.mark_persisted(version)
            return
        self._unit_of_work.after_commit(lambda: standing.mark_persisted(version))
