"""Property aggregate behaviour: invariants and state transitions."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest

from loka.bounded_contexts.property.domain.entities.property import (
    ChargingPolicy,
    DataQualityIssue,
    Property,
)
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.exceptions.property_errors import (
    PublicationBlocked,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityAnswer,
    AvailabilityWindow,
    BedroomCount,
    Duration,
    PropertyStatus,
    PropertyType,
)
from loka.bounded_contexts.property.domain.value_objects.location import Location
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.shared.domain.errors import InvariantViolation


def build_media(count: int = 2) -> list[PropertyMedia]:
    return [
        PropertyMedia(
            media_id=uuid.uuid4(),
            object_key=f"properties/{uuid.uuid4()}/{index}.jpg",
            kind=MediaKind.PHOTO,
            status=MediaStatus.UPLOADED,
            checksum=f"sha{index}",
            position=index,
            perceptual_hash=f"phash{index}",
        )
        for index in range(count)
    ]


def complete_property(now: datetime, **overrides: object) -> Property:
    prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
    prop.set_type(PropertyType.APARTMENT, now=now)
    prop.set_location(Location(city="Douala", neighbourhood="Bastos"), now=now)
    prop.set_rent(Money(300_000), now=now)
    prop.set_charges(Money(25_000), ChargingPolicy.EXTRA, now=now)
    prop.set_deposit(Money(500_000), now=now)
    prop.set_rooms(BedroomCount(2, 2), None, now=now)
    prop.set_minimum_duration(Duration(12), now=now)
    prop.set_availability(AvailabilityWindow(date.today()), now=now)
    prop.set_conditions("Pas de sous-location, animaux non acceptes.", now=now)
    for media in build_media():
        prop.add_media(media, now=now)
    return prop


class TestDraftConstruction:
    def test_new_property_starts_as_draft_with_all_steps_pending(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        assert prop.status is PropertyStatus.DRAFT
        assert not prop.is_publicly_visible
        assert prop.blocking_issues()
        assert prop.completeness_score() < 0.5

    def test_rent_must_be_positive(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        with pytest.raises(InvariantViolation):
            prop.set_rent(Money(0), now=now)

    def test_vague_location_rejected_at_write_time(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        with pytest.raises(InvariantViolation):
            prop.set_location(Location(city="Douala"), now=now)

    def test_media_count_is_capped(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        for media in build_media(30):
            prop.add_media(media, now=now)
        with pytest.raises(InvariantViolation):
            prop.add_media(build_media(1)[0], now=now)

    def test_duplicate_object_key_rejected(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        media = build_media(1)[0]
        prop.add_media(media, now=now)
        with pytest.raises(InvariantViolation):
            prop.add_media(media, now=now)


class TestPublication:
    def test_incomplete_listing_cannot_be_published(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        prop.set_type(PropertyType.APARTMENT, now=now)
        prop.set_rent(Money(300_000), now=now)
        with pytest.raises(PublicationBlocked) as excinfo:
            prop.publish(now=now)
        assert "listing is incomplete" in str(excinfo.value)
        assert DataQualityIssue.MISSING_PHOTOS.value in str(excinfo.value.context["missing"])

    def test_complete_listing_publishes_and_emits_event(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)

        assert prop.status is PropertyStatus.AVAILABLE
        assert prop.is_publicly_visible
        assert [event.event_type for event in prop.pull_events()] == ["PropertyPublished"]
        assert prop.last_availability_confirmed_at == now

    def test_public_price_includes_extra_charges(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        assert prop.public_price == Money(325_000)

    def test_public_price_ignores_included_charges(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.set_charges(Money(25_000), ChargingPolicy.INCLUDED, now=now)
        prop.publish(now=now)
        assert prop.public_price == Money(300_000)


class TestAvailabilityLifecycle:
    def test_still_available_keeps_status_and_stamps_confirmation(
        self, now: datetime
    ) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        later = now + timedelta(days=30)

        prop.answer_availability(AvailabilityAnswer.STILL_AVAILABLE, now=later)

        assert prop.status is PropertyStatus.AVAILABLE
        assert prop.last_availability_confirmed_at == later

    def test_rented_answer_disappears_from_public_view(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)

        prop.answer_availability(AvailabilityAnswer.RENTED, now=now + timedelta(days=14))

        assert prop.status is PropertyStatus.RENTED
        assert not prop.is_publicly_visible
        assert "PropertyRented" in [event.event_type for event in prop.pull_events()]

    def test_temporarily_unavailable_marks_unavailable_and_records_window(
        self, now: datetime
    ) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        reopen = date.today() + timedelta(days=60)

        prop.answer_availability(
            AvailabilityAnswer.TEMPORARILY_UNAVAILABLE,
            now=now + timedelta(days=7),
            window=AvailabilityWindow(reopen),
        )

        assert prop.status is PropertyStatus.UNAVAILABLE
        assert prop.availability == AvailabilityWindow(reopen)
        assert not prop.is_publicly_visible

    def test_rented_property_cannot_be_edited(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        prop.mark_as_rented(now=now)
        with pytest.raises(InvariantViolation):
            prop.set_rent(Money(400_000), now=now)

    def test_relist_from_rent_requires_new_window(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        prop.mark_as_rented(now=now)
        window = AvailabilityWindow(date.today() + timedelta(days=180))

        prop.relist(now=now, window=window)

        assert prop.status is PropertyStatus.AVAILABLE
        assert prop.availability == window

    def test_available_property_cannot_be_relisted(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        with pytest.raises(InvariantViolation):
            prop.relist(now=now, window=AvailabilityWindow(date.today()))


class TestQualityScanning:
    def test_market_comparison_flags_implausibly_low_price(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.set_rent(Money(80_000), now=now)
        prop.publish(now=now)

        issues = prop.rescan_quality(market_reference=Money(300_000))

        assert DataQualityIssue.SUSPICIOUS_PRICE in issues

    def test_market_comparison_leaves_normal_price_alone(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        assert DataQualityIssue.SUSPICIOUS_PRICE not in prop.rescan_quality(
            market_reference=Money(300_000)
        )

    def test_quarantining_every_photo_reopens_the_missing_photo_issue(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        for media in prop.media:
            media.quarantine(reason="possible_stock_image")

        issues = prop.rescan_quality()

        assert DataQualityIssue.MISSING_PHOTOS in issues

    def test_quarantining_one_photo_keeps_the_listing_publishable(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        prop.media[0].quarantine(reason="possible_stock_image")

        assert DataQualityIssue.MISSING_PHOTOS not in prop.rescan_quality()

    def test_summary_lists_missing_fields(self, now: datetime) -> None:
        prop = Property(property_id=uuid.uuid4(), landlord_id=uuid.uuid4(), now=now)
        summary = prop.draft_summary()
        assert DataQualityIssue.MISSING_PHOTOS.value in summary["missing"]
        assert summary["rent"] is None


class TestSuspension:
    def test_suspended_property_cannot_be_edited(self, now: datetime) -> None:
        prop = complete_property(now)
        prop.publish(now=now)
        prop.suspend(now=now, reason="suspected duplicate listing")

        assert prop.status is PropertyStatus.SUSPENDED
        assert not prop.is_publicly_visible
        with pytest.raises(InvariantViolation):
            prop.set_rent(Money(1), now=now)