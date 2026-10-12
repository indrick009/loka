"""The queue the team works from, with its promised delay attached.

§6 asks for two things: a human reads the complaints, and the queue has an
announced response time. The second is not decoration — "une file humaine sans
délai annoncé est une file qui grossit en silence" — so the delay is applied
here and reported as a count of overdue complaints, which is the number that
makes a growing queue visible.

Oldest first: the queue is worked in the order people were wronged.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from loka.bounded_contexts.reviews_complaints.application.dto.reviews_dto import (
    ComplaintQueueView,
    ComplaintView,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.complaint import (
    Complaint,
    ComplaintStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.entities.provider_standing import (
    StandingStatus,
)
from loka.bounded_contexts.reviews_complaints.domain.repositories import (
    ComplaintRepository,
    ProviderStandingRepository,
)
from loka.bounded_contexts.reviews_complaints.domain.value_objects.provider import ProviderKind
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.clock import ensure_utc

COMPLAINT_REPOSITORY = "provider_complaint"
STANDING_REPOSITORY = "provider_standing"

DEFAULT_QUEUE_PAGE = 50
MAX_QUEUE_PAGE = 200


@dataclass(frozen=True, slots=True)
class ComplaintQueueQuery:
    status: ComplaintStatus | None = None
    limit: int = DEFAULT_QUEUE_PAGE
    offset: int = 0


class GetComplaintQueueUseCase:
    name = "get_complaint_queue"

    def __init__(self, uow: UnitOfWork, *, response_time_hours: int) -> None:
        self._uow = uow
        self._response_time_hours = response_time_hours

    async def execute(
        self, query: ComplaintQueueQuery, *, now: datetime
    ) -> ComplaintQueueView:
        complaints: ComplaintRepository = self._uow.repository(COMPLAINT_REPOSITORY)
        standings: ProviderStandingRepository = self._uow.repository(STANDING_REPOSITORY)

        limit = max(1, min(query.limit, MAX_QUEUE_PAGE))
        pending = await complaints.list_queue(
            status=query.status, limit=limit, offset=max(query.offset, 0)
        )

        # One batch read per provider kind, not one per complaint: the reviewer
        # needs to know which providers are already hidden before deciding, and
        # a queue mixing all four domains must not cost a lookup per row. The
        # kind is part of the key because provider ids come from four tables.
        by_kind: dict[ProviderKind, list[uuid.UUID]] = defaultdict(list)
        for complaint in pending:
            by_kind[complaint.provider.kind].append(complaint.provider.provider_id)
        statuses: dict[tuple[ProviderKind, uuid.UUID], str] = {}
        for kind, provider_ids in by_kind.items():
            found = await standings.statuses_for(kind, provider_ids)
            statuses.update(
                {(kind, provider_id): status for provider_id, status in found.items()}
            )
        deadline = ensure_utc(now) - timedelta(hours=self._response_time_hours)

        items = [
            _view(
                complaint,
                standing_status=statuses.get(
                    (complaint.provider.kind, complaint.provider.provider_id),
                    StandingStatus.VISIBLE.value,
                ),
                overdue=complaint.is_pending and complaint.filed_at <= deadline,
            )
            for complaint in pending
        ]
        return ComplaintQueueView(
            items=items,
            total=len(items),
            overdue=sum(1 for item in items if item.overdue),
            response_time_hours=self._response_time_hours,
        )


def _view(complaint: Complaint, *, standing_status: str, overdue: bool) -> ComplaintView:
    return ComplaintView(
        complaint_id=str(complaint.id),
        domain=complaint.offer.domain.value,
        provider_id=str(complaint.provider.provider_id),
        reason=complaint.reason.value,
        account=complaint.account,
        status=complaint.status.value,
        filed_at=complaint.filed_at,
        decided_at=complaint.decided_at,
        resolution=complaint.resolution,
        provider_hidden=standing_status != StandingStatus.VISIBLE.value,
        overdue=overdue,
    )
