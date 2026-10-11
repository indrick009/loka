"""The landlord's form, as the business spec actually describes it.

These tests exist because the code and the spec had quietly diverged. The form
asked nine questions; the spec (conception_metier/kora-housing-v1.md §4) asks
ten, and they were not the same ten. Three questions the business dropped were
still being put to landlords — the minimum duration, the availability date and
the free-text conditions — while four it had added were never asked at all: how
many identical lodgings, the caution in months, the advance in months, and the
guard and parking.

The expensive half of the divergence was the unit. The caution and the advance
are counted in **months of rent** here: "je paye 6 mois de loyer et j'ajoute
deux mois comme caution remboursable". Stored as a franc amount, the figure
decouples the day a landlord corrects his rent, and a tenant's "j'ai quatre
mois" has to be converted before it can be compared with what is being asked —
which is the axis §8 negotiates on.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.ai.domain.services.fact_extraction import (
    PROPERTY_REQUIREMENTS,
    question_key_for,
)
from loka.bounded_contexts.property.domain.entities.property import Amenities, Property
from loka.bounded_contexts.property.domain.value_objects.enums import (
    ChargingPolicy,
    DataQualityIssue,
    PropertyStanding,
    PropertyType,
    RentMonths,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.interfaces.worker.whatsapp_replies import QUESTION_TEXT, render_reply

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def a_listing() -> Property:
    return Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)


def a_conforming_listing() -> Property:
    """A listing answering exactly the ten steps of §4 — and nothing else."""
    prop = a_listing()
    prop.set_type(PropertyType.STUDIO, now=NOW)
    prop.set_standing(PropertyStanding.MODERN, now=NOW)
    prop.set_quantity(2, now=NOW)
    prop.set_location(Location(city="Douala", neighbourhood="Bonapriso"), now=NOW)
    prop.set_rent(Money(60_000), now=NOW)
    prop.set_charges(Money(0), ChargingPolicy.INCLUDED, now=NOW)
    prop.set_deposit_months(RentMonths(2), now=NOW)
    prop.set_advance_months(RentMonths(6), now=NOW)
    return prop


class TestTheFormAsksWhatTheSpecAsks:
    def test_every_step_the_spec_adds_is_now_asked(self) -> None:
        asked = {name for name, _ in PROPERTY_REQUIREMENTS}

        assert {"quantity", "deposit_months", "advance_months", "security"} <= asked

    @pytest.mark.parametrize(
        "dropped", ["minimum_duration_months", "availability", "conditions"]
    )
    def test_no_step_the_spec_dropped_is_asked(self, dropped: str) -> None:
        """Kora introduces a landlord to a tenant; it does not negotiate a lease."""
        assert dropped not in {name for name, _ in PROPERTY_REQUIREMENTS}

    @pytest.mark.parametrize("key", ["ask.quantity", "ask.deposit", "ask.advance", "ask.security"])
    def test_every_asked_step_has_a_sentence_to_send(self, key: str) -> None:
        """A key with no copy reaches the landlord as a variable name he cannot
        answer, so this is checked rather than assumed."""
        assert key in QUESTION_TEXT
        assert render_reply(reply_kind="QUESTION", question_key=key) == QUESTION_TEXT[key]

    def test_every_required_step_maps_to_a_question(self) -> None:
        for _, step in PROPERTY_REQUIREMENTS:
            assert question_key_for(step) is not None, step

    def test_the_caution_question_asks_for_months_not_francs(self) -> None:
        """The whole point of the unit change, visible in the sentence sent."""
        text = QUESTION_TEXT["ask.deposit"]

        assert "mois" in text
        assert "FCFA" not in text

    def test_the_water_question_names_water(self) -> None:
        """Step 6 must be unmistakable: some lodgings have no running water, and
        "les charges sont incluses ?" got answered "oui" by their landlords."""
        assert "eau" in QUESTION_TEXT["ask.charges"].lower()


class TestEntryMoneyInMonths:
    def test_the_caution_is_added_on_top_of_the_advance(self) -> None:
        """Six of advance plus two of caution is eight months handed over."""
        prop = a_conforming_listing()

        assert prop.entry_months == 8

    def test_the_francs_follow_the_rent(self) -> None:
        prop = a_conforming_listing()

        assert prop.entry_cost() == Money(480_000)

    def test_correcting_the_rent_corrects_the_entry_cost(self) -> None:
        """What a stored amount could not do, and the reason for the change."""
        prop = a_conforming_listing()

        prop.set_rent(Money(70_000), now=NOW)

        assert prop.entry_cost() == Money(560_000)

    def test_no_caution_is_zero_months_and_still_an_answer(self) -> None:
        prop = a_conforming_listing()

        prop.set_deposit_months(RentMonths(0), now=NOW)

        assert prop.entry_months == 6
        assert DataQualityIssue.MISSING_DEPOSIT not in prop.blocking_issues()

    def test_a_half_known_entry_cost_is_not_reported_as_a_total(self) -> None:
        """A partial total read as a total is worse than none: a tenant budgets
        against it."""
        prop = a_listing()
        prop.set_deposit_months(RentMonths(2), now=NOW)

        assert prop.entry_months is None
        assert prop.entry_cost() is None


class TestWhatPublicationRequires:
    def test_a_listing_answering_the_ten_steps_needs_only_its_photos(self) -> None:
        prop = a_conforming_listing()

        assert prop.blocking_issues() == frozenset({DataQualityIssue.MISSING_PHOTOS})

    def test_a_missing_advance_blocks(self) -> None:
        """Unlike the three dropped fields: the form really does ask for it, and
        a listing with no advance has nothing for a tenant's months to meet."""
        prop = a_conforming_listing()
        prop.advance_months = None

        assert DataQualityIssue.MISSING_ADVANCE in prop.blocking_issues()


class TestTheCompletenessReport:
    def test_nine_answered_steps_score_nine_out_of_ten(self) -> None:
        """It used to cap at seven, counting three questions the form no longer
        asks — so a landlord who answered everything he was asked was told his
        listing was incomplete. The tenth mark is the photos."""
        prop = a_conforming_listing()
        prop.update_amenities(Amenities(security=True, parking=False), now=NOW)

        assert prop.completeness_score() == 0.9

    def test_an_unanswered_step_costs_exactly_one_mark(self) -> None:
        prop = a_conforming_listing()
        prop.update_amenities(Amenities(security=True, parking=False), now=NOW)
        full = prop.completeness_score()

        prop.advance_months = None

        assert full - prop.completeness_score() == pytest.approx(0.1)


class TestQuantityMeansWhatWasDeclared:
    def test_an_unasked_listing_reads_as_one_lodging(self) -> None:
        prop = a_listing()

        assert prop.quantity is None
        assert prop.lodgings == 1

    def test_identical_lodgings_share_one_listing(self) -> None:
        prop = a_conforming_listing()

        assert prop.lodgings == 2

    def test_a_landlord_can_say_one_is_gone(self) -> None:
        prop = a_conforming_listing()

        prop.declare_one_taken(now=NOW)

        assert prop.lodgings == 1
        assert not prop.is_exhausted

    def test_the_last_one_leaving_exhausts_the_listing(self) -> None:
        prop = a_conforming_listing()

        prop.declare_one_taken(now=NOW)
        prop.declare_one_taken(now=NOW)

        assert prop.is_exhausted
