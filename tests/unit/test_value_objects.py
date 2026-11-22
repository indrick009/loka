"""Value object invariants."""

from __future__ import annotations

from datetime import date

import pytest

from loka.bounded_contexts.auth.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.housing.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    Duration,
    SurfaceArea,
)
from loka.bounded_contexts.housing.domain.value_objects.location import (
    Coordinates,
    Location,
)
from loka.shared.domain.errors import InvariantViolation
from loka.shared.domain.money import Money


class TestMoney:
    def test_addition_requires_same_currency(self) -> None:
        assert (Money(1000) + Money(500)).amount == 1500
        with pytest.raises(InvariantViolation):
            Money(1000) + Money(500, currency="EUR")

    def test_rejects_float_amount(self) -> None:
        with pytest.raises(InvariantViolation):
            Money(1000.5)  # type: ignore[arg-type]

    def test_positive_requirement(self) -> None:
        with pytest.raises(InvariantViolation):
            Money(0).require_positive(reason="rent must be strictly positive")

    def test_equality_is_structural(self) -> None:
        assert Money(200_000, "XAF") == Money(200_000, "XAF")
        assert Money(200_000, "XAF") != Money(200_001, "XAF")

    def test_percentage_of_never_exceeds_whole(self) -> None:
        assert Money(333).percentage_of(0.4).amount == 133


class TestPhoneNumber:
    @pytest.mark.parametrize(
        "raw",
        ["699 12 34 56", "+237699123456", "00237699123456", "699123456", "(+237) 699-123-456"],
    )
    def test_normalisation_is_stable(self, raw: str) -> None:
        assert PhoneNumber.normalize(raw).e164 == "+237699123456"

    def test_rejects_non_e164(self) -> None:
        with pytest.raises(InvariantViolation):
            PhoneNumber(e164="699123456")

    @pytest.mark.parametrize(
        ("raw", "e164"),
        [("+33612345678", "+33612345678"), ("00447712345636", "+447712345636"),
         ("+1 202 555 0143", "+12025550143")],
    )
    def test_a_foreign_number_keeps_its_own_country_code(self, raw: str, e164: str) -> None:
        """Every country is served; rewriting "+33612345678" would store an unreachable contact."""
        assert PhoneNumber.normalize(raw).e164 == e164

    def test_an_implausibly_long_number_is_refused(self) -> None:
        with pytest.raises(InvariantViolation):
            PhoneNumber.normalize("+1234567890123456")

    def test_empty_input_is_an_invariant_violation_not_a_type_error(self) -> None:
        with pytest.raises(InvariantViolation):
            PhoneNumber.normalize("   ")

    def test_wa_jid_and_masking(self) -> None:
        phone = PhoneNumber.normalize("+237699123456")
        assert phone.whatsapp_jid() == "237699123456@s.whatsapp.net"
        assert phone.masked().endswith("***3456")

    def test_distinct_numbers_are_not_equal(self) -> None:
        assert PhoneNumber.normalize("699123456") != PhoneNumber.normalize("677889900")


class TestLocation:
    def test_city_only_is_not_publishable(self) -> None:
        assert not Location(city="Douala").is_publishable()

    def test_neighbourhood_is_publishable(self) -> None:
        assert Location(city="Douala", neighbourhood="Bastos").is_publishable()

    def test_vague_neighbourhood_rejected(self) -> None:
        with pytest.raises(InvariantViolation):
            Location(city="Douala", neighbourhood="x")

    def test_coordinates_out_of_range_rejected(self) -> None:
        with pytest.raises(InvariantViolation):
            Coordinates(latitude=95.0, longitude=0.0)


class TestDomainRanges:
    def test_duration_bounds(self) -> None:
        with pytest.raises(InvariantViolation):
            Duration(months=0)
        with pytest.raises(InvariantViolation):
            Duration(months=200)

    def test_duration_rolls_over_year(self) -> None:
        assert Duration(months=12).months_from(date(2026, 5, 31)) == date(2027, 5, 31)
        assert Duration(months=18).months_from(date(2026, 1, 15)) == date(2027, 7, 15)

    def test_surface_area_bounds(self) -> None:
        assert SurfaceArea(120).square_metres == 120
        with pytest.raises(InvariantViolation):
            SurfaceArea(0)

    def test_bedroom_counts(self) -> None:
        assert BedroomCount(count=0, bathrooms=1).count == 0
        with pytest.raises(InvariantViolation):
            BedroomCount(count=-1, bathrooms=1)

    def test_availability_window_label(self) -> None:
        assert AvailabilityWindow(date.today()).is_immediate()