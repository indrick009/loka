"""The question that decides whether anybody pays.

One sentence of the business spec is the whole point (§9): **the tenant pays
only if the landlord confirms.** Everything here protects one of its corners.

The landlord decides availability, always. Kora never infers it — not from a
match, not from a payment, not from elapsed time — and a listing stays
available by default until he writes otherwise. So there is no timer, no
re-verification schedule and no expiry: a question he never answered simply
never confirms, and the tenant is offered other lodgings instead.

The four answers are not three. "Un studio a été pris" decrements and leaves
the listing published; "tout a été pris" takes it out of search. Conflating
them is how a landlord with two studios loses the second one by letting the
first go.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from loka.bounded_contexts.property.domain.entities.availability_check import (
    AvailabilityCheck,
    CheckStatus,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    ChargingPolicy,
    PropertyStanding,
    PropertyStatus,
    PropertyType,
    RentMonths,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.errors import InvalidStateTransition, InvariantViolation

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=3)


def a_published_listing(*, lodgings: int = 1) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)
    prop.set_type(PropertyType.STUDIO, now=NOW)
    prop.set_standing(PropertyStanding.MODERN, now=NOW)
    prop.set_quantity(lodgings, now=NOW)
    prop.set_location(Location(city="Douala", neighbourhood="Bonapriso"), now=NOW)
    prop.set_rent(Money(60_000), now=NOW)
    prop.set_charges(Money(0), ChargingPolicy.INCLUDED, now=NOW)
    prop.set_deposit_months(RentMonths(2), now=NOW)
    prop.set_advance_months(RentMonths(6), now=NOW)
    prop.status = PropertyStatus.AVAILABLE
    return prop


def a_check(*, tenant_id: uuid.UUID | None = None) -> AvailabilityCheck:
    return AvailabilityCheck(
        check_id=uuid.uuid4(),
        property_id=uuid.uuid4(),
        landlord_id=uuid.uuid4(),
        tenant_id=tenant_id,
        now=NOW,
    )


class TestAQuestionNobodyAnswered:
    def test_a_fresh_check_is_pending_and_confirms_nothing(self) -> None:
        check = a_check()

        assert check.status is CheckStatus.PENDING
        assert check.confirms_availability is False

    def test_time_passing_does_not_turn_silence_into_a_confirmation(self) -> None:
        """Nor into a refusal. "Pas de réponse" is the absence of a status, and
        the spec keeps no availability state over time."""
        check = a_check()

        assert check.confirms_availability is False
        assert check.answered_at is None


class TestTheFourAnswers:
    def test_yes_leaves_the_listing_available_and_releases_the_payment(self) -> None:
        listing = a_published_listing()
        check = a_check()

        listing.answer_availability(AvailabilityAnswer.STILL_AVAILABLE, now=LATER)
        check.record_answer(AvailabilityAnswer.STILL_AVAILABLE, now=LATER)

        assert listing.status is PropertyStatus.AVAILABLE
        assert listing.lodgings == 1
        assert check.confirms_availability is True

    def test_one_taken_decrements_and_keeps_the_listing_in_search(self) -> None:
        """The answer the old three-value enum could not express."""
        listing = a_published_listing(lodgings=2)
        check = a_check()

        listing.answer_availability(AvailabilityAnswer.ONE_TAKEN, now=LATER)
        check.record_answer(AvailabilityAnswer.ONE_TAKEN, now=LATER)

        assert listing.lodgings == 1
        assert listing.status is PropertyStatus.AVAILABLE
        assert check.confirms_availability is True

    def test_the_last_one_being_taken_ends_the_listing(self) -> None:
        listing = a_published_listing(lodgings=1)

        listing.answer_availability(AvailabilityAnswer.ONE_TAKEN, now=LATER)

        assert listing.is_exhausted
        assert listing.status is PropertyStatus.RENTED

    def test_all_taken_empties_the_listing_whatever_its_quantity(self) -> None:
        """Three studios and "tout a été pris" means none left, not two."""
        listing = a_published_listing(lodgings=3)
        check = a_check()

        listing.answer_availability(AvailabilityAnswer.RENTED, now=LATER)
        check.record_answer(AvailabilityAnswer.RENTED, now=LATER)

        assert listing.lodgings == 0
        assert listing.status is PropertyStatus.RENTED
        assert check.confirms_availability is False

    def test_temporarily_unavailable_is_recorded_and_pays_nobody(self) -> None:
        """Not one of the spec's four, but a landlord does say it."""
        listing = a_published_listing()
        check = a_check()

        listing.answer_availability(
            AvailabilityAnswer.TEMPORARILY_UNAVAILABLE, now=LATER
        )
        check.record_answer(AvailabilityAnswer.TEMPORARILY_UNAVAILABLE, now=LATER)

        assert listing.status is PropertyStatus.UNAVAILABLE
        assert check.confirms_availability is False


class TestAnAnswerIsAppliedOnce:
    def test_a_second_answer_to_the_same_question_is_refused(self) -> None:
        """The first one already moved the listing. Replaying it would cost a
        lodging the landlord never let go."""
        check = a_check()
        check.record_answer(AvailabilityAnswer.ONE_TAKEN, now=LATER)

        with pytest.raises(InvalidStateTransition):
            check.record_answer(AvailabilityAnswer.ONE_TAKEN, now=LATER)

    def test_taking_one_from_an_empty_listing_is_refused(self) -> None:
        listing = a_published_listing(lodgings=1)
        listing.answer_availability(AvailabilityAnswer.ONE_TAKEN, now=LATER)

        with pytest.raises(InvariantViolation):
            listing.declare_one_taken(now=LATER)


class TestTheConfirmationStampIsHonest:
    def test_an_applicable_answer_stamps_the_listing(self) -> None:
        listing = a_published_listing()

        listing.answer_availability(AvailabilityAnswer.STILL_AVAILABLE, now=LATER)

        assert listing.last_availability_confirmed_at == LATER

    def test_a_refused_answer_does_not_stamp_it(self) -> None:
        """It used to: a listing that refused the answer still came out looking
        freshly confirmed, which is exactly the lie the stamp must not tell."""
        listing = a_published_listing()
        listing.status = PropertyStatus.RENTED

        with pytest.raises(InvariantViolation):
            listing.answer_availability(AvailabilityAnswer.STILL_AVAILABLE, now=LATER)

        assert listing.last_availability_confirmed_at is None


class TestWhatTheAnswerTellsTheTenant:
    def test_a_confirmation_names_who_was_waiting(self) -> None:
        tenant_id = uuid.uuid4()
        check = a_check(tenant_id=tenant_id)

        check.record_answer(AvailabilityAnswer.STILL_AVAILABLE, now=LATER)

        assert check.tenant_id == tenant_id

    def test_an_unprompted_confirmation_has_nobody_waiting(self) -> None:
        """A landlord may confirm out of the blue; that still counts."""
        check = a_check(tenant_id=None)

        check.record_answer(AvailabilityAnswer.STILL_AVAILABLE, now=LATER)

        assert check.tenant_id is None
        assert check.confirms_availability is True
