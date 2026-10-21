"""What a tenant is looking for, kept so Kora can come back to him.

Kora does not answer a search in the thread. It records the demand and says so
— "votre demande est enregistrée, si un logement correspond je reviens vers
vous" — whether a lodging matches or not. That pause is the point: a direct
match, a neighbouring area or a price a little above all need preparing, and
§8's negotiation with a landlord cannot happen in the two seconds a reply takes.

Without this record the promise would be empty. A tenant told "je reviens vers
vous" by a system that kept nothing of his request is a tenant nobody can come
back to.

One open demand per tenant, not one per message. He refines as he talks —
another neighbourhood, a higher budget — and that is the same request being
sharpened, not five requests. Restating replaces the criteria; the demand keeps
its identity and the clock it has been waiting on.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.aggregate import AggregateRoot
from loka.shared.domain.clock import ensure_utc
from loka.shared.domain.errors import InvalidStateTransition

# How far around the tenant's budget a lodging is worth proposing. Below, a
# floor: far too cheap reads as an insult — a 90 000 tenant is not looking for a
# 50 000 room. Above, only a slight overshoot: a wide gap is a negotiation with
# the landlord (§8), not something to drop on the tenant's screen.
BUDGET_LOWER_TOLERANCE = 0.20
BUDGET_UPPER_TOLERANCE = 0.05

# How far from the point a tenant shared a lodging still counts as "there". A
# pin is where he wants to live — his workplace, a relative's street — and a
# district boundary means nothing to him: three kilometres is a short moto ride.
NEAR_RADIUS_KM = 3.0

# §8's "écart": when nothing matches exactly, a lodging a little dearer or a
# little further is still worth proposing — said as such, never passed off as a
# match, so the tenant decides whether he can consider it.
GAP_UPPER_TOLERANCE = 0.15
GAP_RADIUS_KM = 6.0
# The advance the landlord asks may exceed what the tenant said he can pay by
# this many months and still be put to him as a gap: two months is something a
# family can stretch to, a year against five months is not (§8).
GAP_ADVANCE_MONTHS = 2

_EARTH_RADIUS_KM = 6371.0

Point = tuple[float, float]


def distance_km(a: Point, b: Point) -> float:
    """Great-circle distance between two (latitude, longitude) points."""
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    inner = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(inner)))


class DemandStatus(StrEnum):
    """Owed an answer, or no longer owed one."""

    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    WITHDRAWN = "WITHDRAWN"


class TenantDemand(AggregateRoot):
    __slots__ = (
        "answered_at",
        "city",
        "demand_id",
        "max_advance_months",
        "max_rent",
        "near",
        "near_exact",
        "neighbourhoods",
        "property_type",
        "received_at",
        "status",
        "tenant_id",
        "updated_at",
    )

    def __init__(
        self,
        *,
        demand_id: uuid.UUID,
        tenant_id: uuid.UUID,
        property_type: str | None,
        city: str | None,
        neighbourhoods: tuple[str, ...],
        max_rent: Money | None,
        now: datetime,
        near: Point | None = None,
        near_exact: bool = True,
        max_advance_months: int | None = None,
    ) -> None:
        super().__init__(aggregate_type="TenantDemand")
        self._assign_id(demand_id)
        self.demand_id = demand_id
        self.tenant_id = tenant_id
        self.property_type = property_type
        self.city = city
        self.neighbourhoods = neighbourhoods
        self.max_rent = max_rent
        # The WhatsApp location he shared, or the centre of the district he
        # typed (``near_exact`` False). Only a real pin widens an exact match;
        # a district's centre only measures how far a gap is.
        self.near = near
        self.near_exact = near_exact
        # How many months of rent he can hand over up front (§3, question 3).
        self.max_advance_months = max_advance_months
        self.status = DemandStatus.OPEN
        self.received_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self.answered_at: datetime | None = None
        self.record(
            "TenantDemandRecorded",
            occurred_at=now,
            payload={
                "demand_id": str(demand_id),
                "tenant_id": str(tenant_id),
                "city": city,
                "property_type": property_type,
            },
        )

    def restate(
        self,
        *,
        property_type: str | None,
        city: str | None,
        neighbourhoods: tuple[str, ...],
        max_rent: Money | None,
        now: datetime,
        near: Point | None = None,
        near_exact: bool = True,
        max_advance_months: int | None = None,
    ) -> None:
        """He sharpened what he is after; it is still the same request.

        Refused once answered: the answer was prepared against the criteria as
        they stood, so a demand that has been replied to is closed and a new
        search opens a new one.
        """
        if self.status is not DemandStatus.OPEN:
            raise InvalidStateTransition(
                "a demand that is no longer open cannot be restated",
                context={"demand_id": str(self.id), "status": self.status.value},
            )
        self.property_type = property_type
        self.city = city
        self.neighbourhoods = neighbourhoods
        self.max_rent = max_rent
        self.near = near
        self.near_exact = near_exact
        self.max_advance_months = max_advance_months
        self.updated_at = ensure_utc(now)
        self.record(
            "TenantDemandRestated",
            occurred_at=now,
            payload={
                "demand_id": str(self.id),
                "city": city,
                "property_type": property_type,
            },
        )

    def mark_answered(
        self, *, now: datetime, proposals: Sequence[Mapping[str, Any]] = ()
    ) -> None:
        """Kora came back to him. Nothing more is owed on this demand.

        ``proposals`` are the lodgings he is being sent, in the order he will
        read them: the event carries them so whoever writes the message shows
        exactly what was matched, and his "2" later names the same lodging.
        """
        if self.status is not DemandStatus.OPEN:
            raise InvalidStateTransition(
                "only an open demand can be answered",
                context={"demand_id": str(self.id), "status": self.status.value},
            )
        self.status = DemandStatus.ANSWERED
        self.answered_at = ensure_utc(now)
        self.updated_at = ensure_utc(now)
        self.record(
            "TenantDemandAnswered",
            occurred_at=now,
            payload={
                "demand_id": str(self.id),
                "tenant_id": str(self.tenant_id),
                "proposals": [dict(proposal) for proposal in proposals],
            },
        )

    def price_band(self) -> tuple[int, int] | None:
        """The inclusive rent range his budget is matched against, if he gave one."""
        if self.max_rent is None:
            return None
        budget = self.max_rent.amount
        return (
            int(budget * (1 - BUDGET_LOWER_TOLERANCE)),
            int(budget * (1 + BUDGET_UPPER_TOLERANCE)),
        )

    def accepts(
        self,
        *,
        property_type: str | None,
        city: str | None,
        neighbourhood: str | None,
        rent_xaf: int | None,
        point: Point | None = None,
        advance_months: int | None = None,
    ) -> bool:
        """Whether one published lodging answers this demand.

        The same rule the catalogue search applies to a demand, stated for one
        listing: a lodging published after the demand never went through that
        search, and has to be held to the same bar.
        """
        if not self.is_answerable or city != self.city:
            return False
        if self.property_type and property_type != self.property_type:
            return False
        if not self.is_around(neighbourhood=neighbourhood, point=point):
            return False
        if not self.can_advance(advance_months):
            return False
        band = self.price_band()
        if band is None:
            return True
        return rent_xaf is not None and band[0] <= rent_xaf <= band[1]

    def is_around(self, *, neighbourhood: str | None, point: Point | None) -> bool:
        """Whether a lodging is where he wants to live.

        A district he named always counts. A shared location adds every lodging
        within ``NEAR_RADIUS_KM`` of it, whatever its district is called — and
        excludes those placed further away. A lodging with no position of its
        own cannot be measured, so a pin alone never shuts it out: it is
        ranked after the measured ones instead (see the search).
        """
        if neighbourhood is not None and neighbourhood in self.neighbourhoods:
            return True
        if self.near is not None and self.near_exact:
            if point is not None:
                return distance_km(self.near, point) <= NEAR_RADIUS_KM
            return not self.neighbourhoods
        return not self.neighbourhoods

    def tolerates(
        self,
        *,
        property_type: str | None,
        city: str | None,
        neighbourhood: str | None,
        rent_xaf: int | None,
        point: Point | None = None,
        advance_months: int | None = None,
    ) -> bool:
        """Whether a lodging is close enough to be proposed as a gap (§8).

        Same type and city, rent up to ``GAP_UPPER_TOLERANCE`` above his
        budget, and within ``GAP_RADIUS_KM`` of where he wants to live. A
        lodging with no position, or a demand with no point, is held to the
        districts he named — distance cannot be measured, so it is not guessed.
        """
        if not self.is_answerable or city != self.city:
            return False
        if self.property_type and property_type != self.property_type:
            return False
        band = self.gap_band()
        if band is not None and (rent_xaf is None or not band[0] <= rent_xaf <= band[1]):
            return False
        if not self.can_advance(advance_months, slack=GAP_ADVANCE_MONTHS):
            return False
        if neighbourhood is not None and neighbourhood in self.neighbourhoods:
            return True
        if self.near is not None and point is not None:
            return distance_km(self.near, point) <= GAP_RADIUS_KM
        return not self.neighbourhoods and self.near is None

    def can_advance(self, advance_months: int | None, *, slack: int = 0) -> bool:
        """Whether he can pay the advance a lodging asks, give or take ``slack``.

        A listing with no stated advance, or a tenant who gave none, is not
        held back: nothing can be compared, so nothing is assumed.
        """
        if advance_months is None or self.max_advance_months is None:
            return True
        return advance_months <= self.max_advance_months + slack

    def advance_ceiling(self, *, gap: bool = False) -> int | None:
        """The most months of advance a lodging may ask and still be proposed."""
        if self.max_advance_months is None:
            return None
        return self.max_advance_months + (GAP_ADVANCE_MONTHS if gap else 0)

    def gap_band(self) -> tuple[int, int] | None:
        """The rent range a gap may fall in: the usual floor, a wider ceiling."""
        band = self.price_band()
        if band is None or self.max_rent is None:
            return None
        return band[0], int(self.max_rent.amount * (1 + GAP_UPPER_TOLERANCE))

    def gap_with(
        self,
        *,
        rent_xaf: int | None,
        point: Point | None,
        advance_months: int | None = None,
    ) -> dict[str, Any]:
        """How a lodging differs from what he asked, in the words he will read."""
        gap: dict[str, Any] = {}
        if self.max_rent is not None and rent_xaf is not None and rent_xaf > self.max_rent.amount:
            gap["over_budget_xaf"] = rent_xaf - self.max_rent.amount
        if (
            self.max_advance_months is not None
            and advance_months is not None
            and advance_months > self.max_advance_months
        ):
            gap["over_advance_months"] = advance_months - self.max_advance_months
        if self.near is not None and point is not None:
            gap["distance_km"] = round(distance_km(self.near, point), 1)
        return gap

    def withdraw(self, *, reason: str, now: datetime) -> None:
        """He is no longer looking. Not an answer, and not a failure either."""
        if self.status is not DemandStatus.OPEN:
            raise InvalidStateTransition(
                "only an open demand can be withdrawn",
                context={"demand_id": str(self.id), "status": self.status.value},
            )
        self.status = DemandStatus.WITHDRAWN
        self.updated_at = ensure_utc(now)
        self.record(
            "TenantDemandWithdrawn",
            occurred_at=now,
            payload={"demand_id": str(self.id), "reason": reason[:200]},
        )

    @property
    def is_answerable(self) -> bool:
        """Whether a city is known — without one there is nothing to search."""
        return self.status is DemandStatus.OPEN and bool(self.city)
