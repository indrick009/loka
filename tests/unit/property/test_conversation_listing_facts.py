"""The confirmed conversation facts must hydrate the Property aggregate.

The quality gate lives on the aggregate, so the only way to know the flow
collects *enough* to publish is to push a full fact set through ``_apply_facts``
and assert nothing but the photos remain missing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from loka.bounded_contexts.property.application.use_cases.create_property_from_conversation import (
    _apply_facts,
)
from loka.bounded_contexts.property.domain.entities.property import Property
from loka.bounded_contexts.property.domain.value_objects.enums import (
    ChargingPolicy,
    DataQualityIssue,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

FULL_FACTS: dict[str, Any] = {
    "property_type": "APARTMENT",
    "standing": "MODERN",
    "location": {"city": "Yaoundé", "neighbourhood": "Bastos"},
    "rent": 250_000,
    "charges": 0,
    "charging_policy": "INCLUDED",
    # Entry money in months of rent, as the form now asks for it, plus the two
    # steps that used to be missing from this set entirely.
    "deposit_months": 2,
    "advance_months": 6,
    "quantity": 2,
    "security": {"security": True, "parking": False},
    "minimum_duration_months": 12,
    "availability": "IMMEDIATE",
    "conditions": "Caution deux mois, avance de trois mois.",
}


def _property() -> Property:
    return Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=NOW)


class TestApplyConversationFacts:
    def test_the_entry_conditions_reach_the_aggregate(self) -> None:
        prop = _property()

        _apply_facts(prop, FULL_FACTS, now=NOW)

        assert prop.charges is not None and prop.charges.amount == 0
        assert prop.charging_policy is ChargingPolicy.INCLUDED
        assert prop.deposit_months is not None and prop.deposit_months.months == 2
        assert prop.advance_months is not None and prop.advance_months.months == 6
        assert prop.quantity == 2
        assert prop.amenities.security is True
        assert prop.amenities.parking is False
        assert prop.minimum_duration is not None and prop.minimum_duration.months == 12
        assert prop.availability is not None
        assert prop.availability.available_from == NOW.date()
        assert prop.conditions == "Caution deux mois, avance de trois mois."

    def test_a_full_fact_set_leaves_only_the_photos_missing(self) -> None:
        """If the form ever stops collecting one of these, this test fails here
        rather than in production as a listing stuck in DRAFT."""
        prop = _property()

        _apply_facts(prop, FULL_FACTS, now=NOW)

        assert prop.blocking_issues() == frozenset({DataQualityIssue.MISSING_PHOTOS})

    def test_a_bad_field_never_aborts_the_rest(self) -> None:
        prop = _property()
        facts = dict(FULL_FACTS, rent=-5)

        _apply_facts(prop, facts, now=NOW)

        assert prop.rent is None
        assert prop.deposit_months is not None
