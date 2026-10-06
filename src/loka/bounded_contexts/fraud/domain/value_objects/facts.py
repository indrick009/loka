"""Behavioural facts consumed by the fraud rule engine.

Each fact snapshot is an immutable view of what other contexts know about a
subject. The rule engine is a pure function over these snapshots, so every
rule is unit-testable without a database and identical facts always yield the
same score.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loka.shared.domain.value_object import ValueObject


@dataclass(frozen=True, slots=True)
class LandlordFacts(ValueObject):
    landlord_id: uuid.UUID
    verification_status: str
    active_property_count: int
    open_report_count: int


@dataclass(frozen=True, slots=True)
class UserFacts(ValueObject):
    user_id: uuid.UUID
    phone_e164_hash: str
    accounts_with_same_phone: int
    failed_payments_14d: int


@dataclass(frozen=True, slots=True)
class PropertyFacts(ValueObject):
    property_id: uuid.UUID
    landlord_id: uuid.UUID
    status: str
    city: str | None
    rent_xaf: int | None
    public_price_xaf: int | None
    other_landlords_sharing_media: int
    open_report_count: int
    city_median_price_xaf: int | None


Facts = LandlordFacts | UserFacts | PropertyFacts