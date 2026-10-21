"""One round of negotiation, carried by the availability question (§8).

When a tenant picks a lodging that asks more than he said he could give — a
little dearer, or more months up front — Kora may put his terms to the
landlord, **only if the lodging gets little demand**: people interested at the
asking price mean the price is right. The offer rides on the question the
landlord is asked anyway (§9), so negotiating costs him no extra message.

Then it stops. He accepts, keeps his price, or names his own once; that answer
goes to the tenant and nothing goes back. Kora is a matchmaker with one round
of negotiation, not a broker — and the same offer is never made twice about
the same lodging.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Fewer availability questions than this in a week is "peu de demandes".
LOW_DEMAND_PER_WEEK = 4


@dataclass(frozen=True, slots=True)
class Offer:
    """The tenant's terms, next to the landlord's. ``None`` where they agree."""

    rent_xaf: int | None
    asked_rent_xaf: int | None
    advance_months: int | None
    asked_advance_months: int | None

    def as_dict(self) -> dict[str, int | None]:
        return {
            "rent_xaf": self.rent_xaf,
            "asked_rent_xaf": self.asked_rent_xaf,
            "advance_months": self.advance_months,
            "asked_advance_months": self.asked_advance_months,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> Offer | None:
        if not raw:
            return None
        return cls(
            rent_xaf=raw.get("rent_xaf"),
            asked_rent_xaf=raw.get("asked_rent_xaf"),
            advance_months=raw.get("advance_months"),
            asked_advance_months=raw.get("asked_advance_months"),
        )


class OfferOutcome(StrEnum):
    ACCEPTED = "ACCEPTED"
    REFUSED = "REFUSED"
    COUNTERED = "COUNTERED"


@dataclass(frozen=True, slots=True)
class OfferReply:
    """What the landlord said to the offer. A counter names his own terms."""

    outcome: OfferOutcome
    rent_xaf: int | None = None
    advance_months: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "rent_xaf": self.rent_xaf,
            "advance_months": self.advance_months,
        }


def offer_for(
    *,
    budget_xaf: int | None,
    can_advance_months: int | None,
    rent_xaf: int | None,
    public_price_xaf: int | None,
    advance_months: int | None,
    asked_this_week: int,
    already_offered: Iterable[Offer] = (),
) -> Offer | None:
    """The offer to put to the landlord, or ``None`` to ask availability only.

    The rent offered keeps the charges the landlord bills on top: the tenant's
    budget is compared with the public price, so the rent comes down by the
    same amount the public price overshoots it.
    """
    if asked_this_week >= LOW_DEMAND_PER_WEEK:
        return None
    rent_offer = None
    if (
        budget_xaf is not None
        and rent_xaf is not None
        and public_price_xaf is not None
        and public_price_xaf > budget_xaf
    ):
        rent_offer = rent_xaf - (public_price_xaf - budget_xaf)
    advance_offer = None
    if (
        can_advance_months is not None
        and advance_months is not None
        and advance_months > can_advance_months
    ):
        advance_offer = can_advance_months
    if rent_offer is None and advance_offer is None:
        return None
    offer = Offer(
        rent_xaf=rent_offer,
        asked_rent_xaf=rent_xaf if rent_offer is not None else None,
        advance_months=advance_offer,
        asked_advance_months=advance_months if advance_offer is not None else None,
    )
    if offer in set(already_offered):
        return None
    return offer
