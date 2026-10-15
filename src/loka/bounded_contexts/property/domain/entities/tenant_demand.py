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
        "max_rent",
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
    ) -> None:
        super().__init__(aggregate_type="TenantDemand")
        self._assign_id(demand_id)
        self.demand_id = demand_id
        self.tenant_id = tenant_id
        self.property_type = property_type
        self.city = city
        self.neighbourhoods = neighbourhoods
        self.max_rent = max_rent
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
        if self.neighbourhoods and neighbourhood not in self.neighbourhoods:
            return False
        band = self.price_band()
        if band is None:
            return True
        return rent_xaf is not None and band[0] <= rent_xaf <= band[1]

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
