"""Where a lodging is: coordinates, districts and location hints."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.housing.domain.value_objects.location import (
    Coordinates,
    Location,
)
from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.knowledge import market
from loka.shared.domain.knowledge.places import (
    canonical_district,
    district_centre,
    nearest_district,
)


def _accepted_neighbourhoods(main: str, listed: Any) -> list[str]:
    """Every neighbourhood named, the main one first, each once."""
    names: list[str] = []
    candidates = [main, *(listed if isinstance(listed, list) else [])]
    for candidate in candidates:
        name = str(candidate).strip() if isinstance(candidate, str) else ""
        if name and name.casefold() not in {known.casefold() for known in names}:
            names.append(name)
    return names


def _build_coordinates(entities: dict[str, Any]) -> Coordinates | None:
    """The pin, or nothing. A half-sent pair is nothing, never a guess."""
    latitude, longitude = entities.get("latitude"), entities.get("longitude")
    if not isinstance(latitude, int | float) or isinstance(latitude, bool):
        return None
    if not isinstance(longitude, int | float) or isinstance(longitude, bool):
        return None
    return Coordinates(latitude=float(latitude), longitude=float(longitude))


def _clean_hint(raw: Any) -> str:
    hint = str(raw).strip()
    if len(hint) < 2:
        raise ValidationFailed("location hint is too vague", context={"value": raw})
    return hint


def _resolve_location_hint(raw: Any) -> dict[str, str] | None:
    """Turn a bare place name into a publishable location, or ``None``.

    ``None`` means "not in the district index": the caller then keeps the raw
    hint rather than inventing a city for it.
    """
    hint = str(raw).strip()
    if len(hint) < 2:
        return None
    city = market.resolve_city(hint)
    if city is not None:
        return {"city": city, "neighbourhood": ""}
    district_city = market.resolve_neighbourhood(hint)
    if district_city is not None:
        return {
            "city": district_city,
            "neighbourhood": market.canonical_neighbourhood(hint) or hint,
        }
    return None


def _build_location(entities: dict[str, Any]) -> dict[str, Any]:
    city = str(entities.get("city") or "").strip()
    neighbourhood = entities.get("neighbourhood")
    neighbourhood_text = str(neighbourhood).strip() if isinstance(neighbourhood, str) else ""
    accepted = _accepted_neighbourhoods(neighbourhood_text, entities.get("neighbourhoods"))
    neighbourhood_text = accepted[0] if accepted else ""
    coordinates = _build_coordinates(entities)
    if not city and neighbourhood_text:
        # "Bastos" alone is a complete answer here: the district index knows
        # it is in Yaoundé. Without this the location question never closes.
        city = market.resolve_neighbourhood(neighbourhood_text) or ""
    if not city and coordinates is not None:
        # A shared pin says the city without naming it, which is the whole
        # reason asking for one after a pin is a defect.
        city = market.resolve_city_by_coordinates(
            coordinates.latitude, coordinates.longitude
        ) or ""
    if not city:
        raise ValidationFailed(
            "no city was extracted", context={"entities": sorted(entities)}
        )
    # One spelling per district, the gazetteer's, so a typed "Ekounou" and
    # an "Ékounou" named from a pin are the same place to the matching.
    accepted = [canonical_district(name, city=city) or name for name in accepted]
    neighbourhood_text = accepted[0] if accepted else ""
    if not neighbourhood_text and coordinates is not None:
        # A pin with no place name: the nearest known district names it,
        # so a tenant reads "Studio - Odza" rather than "Studio - Yaoundé".
        neighbourhood_text = (
            nearest_district(coordinates.latitude, coordinates.longitude) or ""
        )
    location = Location(
        city=city,
        neighbourhood=neighbourhood_text or None,
        coordinates=coordinates,
    )
    built: dict[str, Any] = {
        "city": location.city,
        "neighbourhood": location.neighbourhood or "",
    }
    if len(accepted) > 1:
        # Only a tenant names several; a listing stands in one place, and
        # the landlord's form reads ``neighbourhood`` alone.
        built["neighbourhoods"] = accepted
    if location.coordinates is not None:
        # Carried through the facts because the pin is what makes the
        # listing precise: a neighbourhood alone is publishable, a pin is
        # exact, and dropping it here would throw that away at the last step.
        built["latitude"] = location.coordinates.latitude
        built["longitude"] = location.coordinates.longitude
    elif location.neighbourhood:
        # A typed district carries its centre, marked approximate: enough
        # to measure a gap ("à 5 km d'Odza"), never mistaken for a pin.
        centre = district_centre(location.neighbourhood, city=location.city)
        if centre is not None:
            built["latitude"], built["longitude"] = centre
            built["approximate"] = True
    return built
