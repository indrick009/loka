"""Property DTOs.

Application-layer shapes returned to interfaces. They never expose ORM rows
nor mutable aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loka.bounded_contexts.property.domain.entities.property import Property


@dataclass(frozen=True, slots=True)
class PropertySummary:
    property_id: str
    landlord_id: str
    status: str
    type: str | None
    city: str | None
    neighbourhood: str | None
    public_price_xaf: int | None
    charging_policy: str
    bedrooms: int | None
    bathrooms: int | None
    surface_m2: int | None
    minimum_duration_months: int | None
    available_from: str | None
    photo_count: int
    completeness_score: float
    blocking_issues: list[str] = field(default_factory=list)

    @classmethod
    def from_aggregate(cls, prop: Property) -> PropertySummary:
        public = prop.public_price
        return cls(
            property_id=str(prop.id),
            landlord_id=str(prop.landlord_id),
            status=prop.status.value,
            type=prop.property_type.value if prop.property_type else None,
            city=prop.location.city if prop.location else None,
            neighbourhood=prop.location.neighbourhood if prop.location else None,
            public_price_xaf=public.amount if public else None,
            charging_policy=prop.charging_policy.value,
            bedrooms=prop.bedrooms.count if prop.bedrooms else None,
            bathrooms=prop.bedrooms.bathrooms if prop.bedrooms else None,
            surface_m2=prop.surface_area.square_metres if prop.surface_area else None,
            minimum_duration_months=(
                prop.minimum_duration.months if prop.minimum_duration else None
            ),
            available_from=(
                prop.availability.available_from.isoformat() if prop.availability else None
            ),
            photo_count=len(prop.usable_photos()),
            completeness_score=prop.completeness_score(),
            blocking_issues=sorted(issue.value for issue in prop.blocking_issues()),
        )

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)