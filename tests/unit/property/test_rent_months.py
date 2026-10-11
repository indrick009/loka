"""Months of rent, and how many copies of a listing exist.

Two notions the Cameroonian market needs and the model had neither of.

**Months of rent.** Entry money is counted in months here, not in francs: "je
paye 6 mois de loyer et j'ajoute deux mois comme caution remboursable". The
advance and the caution are both months, and the caution is *added on top of*
the advance — eight months handed over, two of which come back. Storing francs
instead decouples the figure from the rent it is a multiple of, so correcting a
rent leaves a stale total behind.

Zero is a real answer: "aucune caution" is ordinary. That is why this is not a
``Duration`` — a lease of zero months is nonsense, a caution of zero is not.

**Quantity.** A landlord with two identical studios publishes one listing with
quantity two. It decides what "is it still available?" even means: "are any
left?" rather than "is it gone?". And it only ever decreases when the landlord
says so — Kora leaves the relationship at the introduction, so it cannot know
whether a lease was signed. Only he knows.

See ``conception_metier/kora-housing-v1.md`` §4, §5 and §9.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.value_objects.enums import RentMonths
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.errors import InvariantViolation

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def a_property() -> Property:
    return Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)


class TestRentMonths:
    def test_zero_is_a_legitimate_answer(self) -> None:
        """"Aucune caution" has to be expressible, or the question loops."""
        assert RentMonths(0).months == 0

    @pytest.mark.parametrize("months", [1, 2, 6, 12, 24])
    def test_ordinary_values_are_accepted(self, months: int) -> None:
        assert RentMonths(months).months == months

    @pytest.mark.parametrize("months", [-1, 25, 1000])
    def test_values_outside_the_market_are_refused(self, months: int) -> None:
        """A year of advance is the worst case the spec cites; past that it is a
        typo, and a typo stored is a listing nobody can match."""
        with pytest.raises(InvariantViolation):
            RentMonths(months)

    def test_the_francs_are_derived_from_the_rent(self) -> None:
        """Six months at 60 000 is 360 000 — and it stays correct when the
        landlord corrects his rent, which a stored total would not."""
        assert RentMonths(6).amount_at(Money(60_000)) == Money(360_000)

    def test_zero_months_costs_nothing(self) -> None:
        assert RentMonths(0).amount_at(Money(60_000)) == Money(0)


class TestQuantity:
    def test_a_listing_is_one_lodging_by_default(self) -> None:
        assert a_property().quantity == 1

    def test_identical_lodgings_share_one_listing(self) -> None:
        """Two identical studios: one listing, quantity two (§5)."""
        prop = a_property()

        prop.set_quantity(2, now=NOW)

        assert prop.quantity == 2

    def test_a_listing_of_nothing_is_refused(self) -> None:
        with pytest.raises(InvariantViolation):
            a_property().set_quantity(0, now=NOW)

    def test_the_landlord_declares_one_taken(self) -> None:
        """"Un studio a été pris" — the quantity drops, the listing remains."""
        prop = a_property()
        prop.set_quantity(2, now=NOW)

        prop.declare_one_taken(now=NOW)

        assert prop.quantity == 1
        assert prop.is_exhausted is False

    def test_the_last_one_taken_exhausts_the_listing(self) -> None:
        """What makes "are any left?" a different question from "is it gone?"."""
        prop = a_property()

        prop.declare_one_taken(now=NOW)

        assert prop.quantity == 0
        assert prop.is_exhausted is True

    def test_an_exhausted_listing_cannot_go_further(self) -> None:
        prop = a_property()
        prop.declare_one_taken(now=NOW)

        with pytest.raises(InvariantViolation):
            prop.declare_one_taken(now=NOW)
