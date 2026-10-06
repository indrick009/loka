"""Property <-> row mapper.

Kept separate from the aggregate so the domain never sees an ORM object and
the aggregate never sees SQL.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from loka.bounded_contexts.property.domain.entities.property import Amenities, Property
from loka.bounded_contexts.property.domain.entities.property_media import (
    MediaKind,
    MediaStatus,
    PropertyMedia,
)
from loka.bounded_contexts.property.domain.value_objects.enums import (
    AvailabilityWindow,
    BedroomCount,
    ChargingPolicy,
    DataQualityIssue,
    Duration,
    PropertyDraftData,
    PropertyStatus,
    PropertyType,
    SurfaceArea,
)
from loka.bounded_contexts.property.domain.value_objects.location import (
    Coordinates,
    Location,
)
from loka.bounded_contexts.property.domain.value_objects.money import Money
from loka.bounded_contexts.property.infrastructure.persistence.models import (
    PropertyMediaRow,
    PropertyRow,
)


def _optional_bool(value: Any) -> bool | None:
    """JSONB gives back whatever was stored, so narrow it explicitly."""
    if value is None:
        return None
    return bool(value)


def to_row(prop: Property) -> dict[str, Any]:
    location = prop.location
    rent = prop.rent
    charges = prop.charges
    public = prop.public_price
    return {
        "id": prop.id,
        "landlord_id": prop.landlord_id,
        "status": prop.status.value,
        "version": prop.version,
        "property_type": prop.property_type.value if prop.property_type else None,
        "city": location.city if location else None,
        "neighbourhood": location.neighbourhood if location else None,
        "address_hint": location.address_hint if location else None,
        "latitude": location.coordinates.latitude if location and location.coordinates else None,
        "longitude": location.coordinates.longitude if location and location.coordinates else None,
        "rent_xaf": rent.amount if rent else None,
        "charges_xaf": charges.amount if charges else None,
        "public_price_xaf": public.amount if public else None,
        "charging_policy": prop.charging_policy.value,
        "deposit_xaf": prop.deposit.amount if prop.deposit else None,
        "currency": rent.currency if rent else "XAF",
        "bedrooms": prop.bedrooms.count if prop.bedrooms else None,
        "bathrooms": prop.bedrooms.bathrooms if prop.bedrooms else None,
        "surface_m2": prop.surface_area.square_metres if prop.surface_area else None,
        "minimum_duration_months": prop.minimum_duration.months if prop.minimum_duration else None,
        "available_from": prop.availability.available_from if prop.availability else None,
        "amenities": {
            "parking": prop.amenities.parking,
            "water": prop.amenities.water,
            "electricity": prop.amenities.electricity,
            "internet": prop.amenities.internet,
            "security": prop.amenities.security,
            "extras": sorted(prop.amenities.extras),
        },
        "conditions": prop.conditions,
        "description": prop.description,
        "landlord_rules": prop.landlord_rules,
        "data_quality_issues": sorted(issue.value for issue in prop.data_quality_issues),
        "completeness_score": prop.completeness_score(),
        "is_verified": prop.is_verified,
        "published_at": prop.published_at,
        "last_availability_confirmed_at": prop.last_availability_confirmed_at,
        "created_at": prop.created_at,
        "updated_at": prop.updated_at,
    }


def media_to_row(media: PropertyMedia, property_id: object, owner_id: object) -> dict[str, Any]:
    return {
        "id": media.id,
        "property_id": property_id,
        "owner_id": owner_id,
        "kind": media.kind.value,
        "status": media.status.value,
        "object_key": media.object_key,
        "checksum": media.checksum,
        "perceptual_hash": media.perceptual_hash,
        "width": media.width,
        "height": media.height,
        "position": media.position,
        "quarantine_reason": media.quarantine_reason,
        "uploaded_at": media.uploaded_at,
        "created_at": media.created_at,
    }


def to_media(row: PropertyMediaRow) -> PropertyMedia:
    return PropertyMedia(
        media_id=row.id,
        object_key=row.object_key,
        kind=MediaKind(row.kind),
        status=MediaStatus(row.status),
        checksum=row.checksum,
        position=row.position,
        width=row.width,
        height=row.height,
        perceptual_hash=row.perceptual_hash,
        uploaded_at=row.uploaded_at,
        quarantine_reason=row.quarantine_reason,
        created_at=row.created_at,
    )


def from_row(row: PropertyRow, media_rows: list[PropertyMediaRow] | None = None) -> Property:
    """Rebuild the aggregate from persistence. Never leaks ORM rows upwards."""
    prop = Property(property_id=row.id, landlord_id=row.landlord_id, now=row.created_at)
    prop._assign_id(row.id)
    prop.status = PropertyStatus(row.status)
    prop.property_type = PropertyType(row.property_type) if row.property_type else None
    if row.city:
        coordinates = (
            Coordinates(latitude=row.latitude, longitude=row.longitude)
            if row.latitude is not None and row.longitude is not None
            else None
        )
        prop.location = Location(
            city=row.city,
            neighbourhood=row.neighbourhood,
            address_hint=row.address_hint,
            coordinates=coordinates,
        )
    prop.rent = Money(row.rent_xaf, row.currency) if row.rent_xaf is not None else None
    prop.charges = Money(row.charges_xaf, row.currency) if row.charges_xaf is not None else None
    prop.charging_policy = ChargingPolicy(row.charging_policy)
    prop.deposit = Money(row.deposit_xaf, row.currency) if row.deposit_xaf is not None else None
    if row.bedrooms is not None:
        prop.bedrooms = BedroomCount(row.bedrooms, row.bathrooms or 0)
    prop.surface_area = SurfaceArea(row.surface_m2) if row.surface_m2 else None
    prop.minimum_duration = (
        Duration(row.minimum_duration_months) if row.minimum_duration_months else None
    )
    prop.availability = (
        AvailabilityWindow(row.available_from) if isinstance(row.available_from, date) else None
    )
    amenities: dict[str, Any] = row.amenities or {}
    prop.amenities = Amenities(
        parking=_optional_bool(amenities.get("parking")),
        water=_optional_bool(amenities.get("water")),
        electricity=_optional_bool(amenities.get("electricity")),
        internet=_optional_bool(amenities.get("internet")),
        security=_optional_bool(amenities.get("security")),
        extras=frozenset(str(extra) for extra in amenities.get("extras") or ()),
    )
    prop.conditions = row.conditions
    prop.description = row.description
    prop.landlord_rules = row.landlord_rules
    prop.media = [to_media(media_row) for media_row in (media_rows or [])]
    prop.last_availability_confirmed_at = row.last_availability_confirmed_at
    prop.published_at = row.published_at
    prop.data_quality_issues = {DataQualityIssue(i) for i in (row.data_quality_issues or [])}
    prop.pending_draft_steps = (
        set() if row.status != PropertyStatus.DRAFT.value else set(PropertyDraftData)
    )
    prop.is_verified = row.is_verified
    prop.created_at = row.created_at
    prop.updated_at = row.updated_at
    prop.mark_persisted(row.version)
    return prop