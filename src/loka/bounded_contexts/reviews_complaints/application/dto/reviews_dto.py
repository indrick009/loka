"""Reviews & Complaints response DTOs.

Thin immutable projections; they never leak ORM rows or aggregates upwards.

:class:`ProviderRankingSignal` is the one that crosses a context boundary. It
is the published contract the catalogue reads instead of these tables, and it
carries no text, no author and no complaint: a consumer that could see who said
what would be one refactor away from printing it, and §5 forbids that.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ProviderRankingSignal:
    """Everything the ranking is allowed to know about one provider.

    ``put_forward`` is separate from the mark on purpose. A hidden or banned
    provider is not a badly rated one to be shown lower down — he is not to be
    offered at all — and collapsing the two into a single score would quietly
    surface him again the day the formula changed.

    ``average_rating`` is None while nobody has rated him. The ranking must
    treat that as "unknown", never as zero: a new landlord with no reviews is
    not a bad landlord, and ranking him below a complained-about one would make
    the signal punish newcomers.
    """

    provider_id: uuid.UUID
    put_forward: bool
    average_rating: float | None
    review_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": str(self.provider_id),
            "put_forward": self.put_forward,
            "average_rating": self.average_rating,
            "review_count": self.review_count,
        }


class ReviewAcknowledgement:
    """Confirmation that a mark was recorded. Says nothing back about the provider."""

    __slots__ = ("provider_id", "rating", "review_id", "submitted_at")

    def __init__(
        self,
        *,
        review_id: str,
        provider_id: str,
        rating: int,
        submitted_at: datetime,
    ) -> None:
        self.review_id = review_id
        self.provider_id = provider_id
        self.rating = rating
        self.submitted_at = submitted_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "review_id": self.review_id,
            "provider_id": self.provider_id,
            "rating": self.rating,
            "submitted_at": self.submitted_at.isoformat(),
        }


class ComplaintView:
    """One complaint as the queue and the complainant see it.

    ``provider_hidden`` tells the caller the automatic guard fired. It is not a
    sanction and must not be phrased as one to the complainant: the decision is
    still owed.
    """

    __slots__ = (
        "account",
        "complaint_id",
        "decided_at",
        "domain",
        "filed_at",
        "overdue",
        "provider_hidden",
        "provider_id",
        "reason",
        "resolution",
        "status",
    )

    def __init__(
        self,
        *,
        complaint_id: str,
        domain: str,
        provider_id: str,
        reason: str,
        account: str,
        status: str,
        filed_at: datetime,
        decided_at: datetime | None,
        resolution: str | None,
        provider_hidden: bool,
        overdue: bool = False,
    ) -> None:
        self.complaint_id = complaint_id
        self.domain = domain
        self.provider_id = provider_id
        self.reason = reason
        self.account = account
        self.status = status
        self.filed_at = filed_at
        self.decided_at = decided_at
        self.resolution = resolution
        self.provider_hidden = provider_hidden
        self.overdue = overdue

    def to_dict(self) -> dict[str, Any]:
        return {
            "complaint_id": self.complaint_id,
            "domain": self.domain,
            "provider_id": self.provider_id,
            "reason": self.reason,
            "account": self.account,
            "status": self.status,
            "filed_at": self.filed_at.isoformat(),
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "resolution": self.resolution,
            "provider_hidden": self.provider_hidden,
            "overdue": self.overdue,
        }


class ComplaintQueueView:
    """The queue plus the one number that keeps it honest.

    ``overdue`` counts the complaints past the announced response time. A human
    queue with no announced delay is a queue that grows in silence (§6), so the
    delay is reported, not merely configured.
    """

    __slots__ = ("items", "overdue", "response_time_hours", "total")

    def __init__(
        self,
        *,
        items: list[ComplaintView],
        total: int,
        overdue: int,
        response_time_hours: int,
    ) -> None:
        self.items = items
        self.total = total
        self.overdue = overdue
        self.response_time_hours = response_time_hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "total": self.total,
            "overdue": self.overdue,
            "response_time_hours": self.response_time_hours,
        }
