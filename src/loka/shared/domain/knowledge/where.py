"""A place as any service stores it: city, district and, when known, a point.

Built from what the model or a WhatsApp pin gave — the same rules as Housing:
a district alone names its city, a pin names its city and nearest district,
and nothing unknown is promoted to a city.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from loka.shared.domain.knowledge import market
from loka.shared.domain.knowledge.places import (
    canonical_district,
    district_centre,
    nearest_district,
)


def place_from(entities: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_lat, raw_lon = entities.get("latitude"), entities.get("longitude")
    pinned = isinstance(raw_lat, int | float) and isinstance(raw_lon, int | float)
    latitude = float(raw_lat) if isinstance(raw_lat, int | float) else 0.0
    longitude = float(raw_lon) if isinstance(raw_lon, int | float) else 0.0
    raw_district = str(entities.get("neighbourhood") or "").strip()
    raw_city = str(entities.get("city") or "").strip()
    city = (
        market.resolve_city(raw_city)
        or market.resolve_neighbourhood(raw_district)
        or (market.resolve_city_by_coordinates(latitude, longitude) if pinned else None)
    )
    if city is None:
        return None
    district = (
        canonical_district(raw_district, city=city)
        or market.canonical_neighbourhood(raw_district)
        or raw_district
    )
    if not district and pinned:
        district = nearest_district(latitude, longitude) or ""
    place: dict[str, Any] = {"city": city, "neighbourhood": district or None}
    if pinned:
        place["latitude"], place["longitude"] = latitude, longitude
    elif district:
        centre = district_centre(district, city=city)
        if centre is not None:
            place["latitude"], place["longitude"] = centre
    return place


def place_label(place: Mapping[str, Any] | None) -> str:
    if not place:
        return ""
    district, city = place.get("neighbourhood"), place.get("city")
    return f"{district}, {city}" if district else str(city or "")


def km_between(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> float | None:
    """Distance between two places that both carry a point, else ``None``."""
    if not a or not b:
        return None
    try:
        return market.distance_km(
            float(a["latitude"]), float(a["longitude"]), float(b["latitude"]), float(b["longitude"])
        )
    except (KeyError, TypeError, ValueError):
        return None
