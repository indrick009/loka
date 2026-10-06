"""The fraud rule engine: a pure function from facts to signals.

The scoring contract that the rest of the context relies on:
- recomputation (same facts -> same score),
- a signal only fires on evidence the signalled context actually stores,
- nothing here bans anyone (the aggregate decides the band afterwards).
"""

from __future__ import annotations

import uuid

import pytest

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    RiskReason,
    RiskSubject,
)
from loka.bounded_contexts.fraud.domain.services.rule_engine import (
    landlord_signals,
    property_signals,
    signals_for,
    user_signals,
)
from loka.bounded_contexts.fraud.domain.value_objects.facts import (
    LandlordFacts,
    PropertyFacts,
    UserFacts,
)

pytestmark = pytest.mark.unit

ID = uuid.uuid4()
ANOTHER_ID = uuid.uuid4()


def _landlord(**overrides: object) -> LandlordFacts:
    values: dict[str, object] = {
        "landlord_id": ID,
        "verification_status": "VERIFIED",
        "active_property_count": 1,
        "open_report_count": 0,
    }
    values.update(overrides)
    return LandlordFacts(**values)  # type: ignore[arg-type]


def _user(**overrides: object) -> UserFacts:
    values: dict[str, object] = {
        "user_id": ID,
        "phone_e164_hash": "hash-a",
        "accounts_with_same_phone": 1,
        "failed_payments_14d": 0,
    }
    values.update(overrides)
    return UserFacts(**values)  # type: ignore[arg-type]


def _property(**overrides: object) -> PropertyFacts:
    values: dict[str, object] = {
        "property_id": ID,
        "landlord_id": ANOTHER_ID,
        "status": "AVAILABLE",
        "city": "Yaoundé",
        "rent_xaf": 100_000,
        "public_price_xaf": 100_000,
        "other_landlords_sharing_media": 0,
        "open_report_count": 0,
        "city_median_price_xaf": 100_000,
    }
    values.update(overrides)
    return PropertyFacts(**values)  # type: ignore[arg-type]


class TestLandlordSignals:
    def test_a_verified_landlord_with_no_reports_is_clean(self) -> None:
        assert landlord_signals(_landlord()) == []

    def test_an_unverified_landlord_is_signalled(self) -> None:
        signals = landlord_signals(_landlord(verification_status="PENDING"))

        assert len(signals) == 1
        assert signals[0].reason is RiskReason.IDENTITY_UNVERIFIED
        assert signals[0].weight == 40

    def test_open_reports_scale_with_cap(self) -> None:
        assert landlord_signals(_landlord(open_report_count=1))[0].weight == 30
        assert landlord_signals(_landlord(open_report_count=3))[0].weight == 50
        assert landlord_signals(_landlord(open_report_count=10))[0].weight == 70


class TestUserSignals:
    def test_a_clean_user_is_not_signalled(self) -> None:
        assert user_signals(_user()) == []

    def test_phone_reuse_only_counts_when_a_second_account_exists(self) -> None:
        signals = user_signals(_user(accounts_with_same_phone=2))

        assert len(signals) == 1
        assert signals[0].reason is RiskReason.PHONE_REUSE
        assert signals[0].weight == 50

    def test_five_failed_payments_is_severe_double_fail(self) -> None:
        signals = user_signals(_user(failed_payments_14d=5))
        assert signals[0].reason is RiskReason.MULTIPLE_REJECTIONS
        assert signals[0].weight == 60

    def test_two_failed_payments_is_mild_single_fail(self) -> None:
        signals = user_signals(_user(failed_payments_14d=2))
        assert signals[0].reason is RiskReason.MULTIPLE_REJECTIONS
        assert signals[0].weight == 30

    def test_a_lone_failure_is_ignored(self) -> None:
        assert user_signals(_user(failed_payments_14d=1)) == []


class TestPropertySignals:
    def test_a_normal_listing_is_clean(self) -> None:
        assert property_signals(_property()) == []

    def test_a_price_below_half_the_city_median_is_an_anomaly(self) -> None:
        signals = property_signals(_property(public_price_xaf=40_000))

        assert len(signals) == 1
        assert signals[0].reason is RiskReason.PRICE_ANOMALY
        assert signals[0].weight == 30

    def test_a_fair_price_is_not_an_anomaly(self) -> None:
        signals = property_signals(_property(public_price_xaf=60_000))
        assert all(s.reason is not RiskReason.PRICE_ANOMALY for s in signals)

    def test_shared_photos_scale_with_cap(self) -> None:
        assert property_signals(_property(other_landlords_sharing_media=1))[0].weight == 45
        assert property_signals(_property(other_landlords_sharing_media=4))[0].weight == 85

    def test_open_reports_are_signalled(self) -> None:
        signals = property_signals(_property(open_report_count=2))
        assert any(s.reason is RiskReason.LISTING_REPORTED for s in signals)


class TestDispatch:
    def test_signals_are_deterministic_across_calls(self) -> None:
        facts = _landlord(verification_status="PENDING")
        assert signals_for(RiskSubject.LANDLORD, facts) == signals_for(
            RiskSubject.LANDLORD, facts
        )

    def test_wrong_kind_of_facts_yields_no_signals(self) -> None:
        assert signals_for(RiskSubject.USER, _landlord()) == []
        assert signals_for(RiskSubject.CONVERSATION, _user()) == []
