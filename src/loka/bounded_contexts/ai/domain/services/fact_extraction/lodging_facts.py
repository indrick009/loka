"""What a lodging offers: security, amenities, availability, standing."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from loka.bounded_contexts.ai.domain.knowledge import market
from loka.bounded_contexts.housing.domain.value_objects.enums import (
    PropertyStanding,
)
from loka.shared.domain.errors import ValidationFailed

_AMENITY_AXES = ("parking", "water", "electricity", "internet", "security")


def _states_security(entities: dict[str, Any]) -> bool:
    """Whether step 9 was actually answered, not merely approached."""
    raw = entities.get("amenities")
    if not isinstance(raw, dict):
        return False
    return isinstance(raw.get("security"), bool) or isinstance(raw.get("parking"), bool)


def _build_security(entities: dict[str, Any]) -> dict[str, object]:
    """The two axes step 9 asks about: a guard, and parking.

    A separate fact from ``amenities`` so that "the landlord answered the
    security question" and "the landlord mentioned some amenity" cannot be
    confused — the first advances the form, the second must not.
    """
    raw = entities.get("amenities")
    if not isinstance(raw, dict):
        raise ValidationFailed(
            "amenities must be an object",
            context={"entity": "amenities", "value": repr(raw)},
        )
    stated: dict[str, object] = {}
    for axis in ("security", "parking"):
        value = raw.get(axis)
        if isinstance(value, bool):
            stated[axis] = value
    return stated


def _build_amenities(entities: dict[str, Any], *, hints: bool = False) -> dict[str, object]:
    """Normalise amenity candidates to the five axes plus extras.

    Only recognised axes with a real boolean become facts; a possible negated
    or nonsense value is dropped, never repaired. ``hints=True`` reads the
    ``amenities_hints`` entity instead — the probable deductions that must NOT
    become facts on a listing but are kept so the conversation can offer to
    confirm them.
    """
    raw = entities.get("amenities_hints" if hints else "amenities")
    if not isinstance(raw, dict):
        raise ValidationFailed(
            "amenities must be an object",
            context={"entity": "amenities_hints" if hints else "amenities", "value": repr(raw)},
        )
    normalised: dict[str, object] = {}
    for axis in _AMENITY_AXES:
        value = raw.get(axis)
        if isinstance(value, bool):
            normalised[axis] = value
    extras = raw.get("extras")
    if not hints and isinstance(extras, list):
        clean_extras: list[str] = []
        for extra in extras:
            label = str(extra).strip()
            if label and len(label) <= 80:
                clean_extras.append(label)
        if clean_extras:
            normalised["extras"] = clean_extras
    return normalised


_IMMEDIATE_AVAILABILITY = (
    "immediat",
    "maintenant",
    "tout de suite",
    "disponible",
    "des que possible",
    "asap",
    "oui",
)


def _build_availability(raw: Any) -> str:
    """Normalise availability to ``IMMEDIATE`` or an ISO date, or refuse it.

    The domain needs a real date to publish; storing a phrase the Property
    cannot read would leave MISSING_AVAILABILITY_DATE on a listing the landlord
    believes they answered.
    """
    text = str(raw).strip()
    lowered = market.normalize(text)
    if not lowered:
        raise ValidationFailed("availability is empty", context={"value": raw})
    if any(word in lowered for word in _IMMEDIATE_AVAILABILITY):
        return "IMMEDIATE"
    parsed = _parse_date(text)
    if parsed is not None:
        return parsed.isoformat()
    raise ValidationFailed(
        "availability must be a date or 'immediate'", context={"value": raw}
    )


def _parse_date(text: str) -> date | None:
    candidate = text.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def _build_conditions(raw: Any) -> str:
    text = str(raw).strip()
    if len(text) < 10:
        raise ValidationFailed("conditions are too vague", context={"value": raw})
    return text


_STANDING_ALIASES: dict[str, PropertyStanding] = {
    "MODERN": PropertyStanding.MODERN,
    "MODERNE": PropertyStanding.MODERN,
    "NON_MODERN": PropertyStanding.NON_MODERN,
    "NON_MODERNE": PropertyStanding.NON_MODERN,
    "ANCIEN": PropertyStanding.NON_MODERN,
    "ANCIENNE": PropertyStanding.NON_MODERN,
}


def _build_standing(entities: dict[str, Any]) -> str:
    """Read "moderne ou non" from either spelling the model may produce.

    A model handed a French message often answers ``"modern": true`` rather than
    the ``standing`` enum; both are accepted and normalised to one value, so the
    rest of the form never has to know which wording arrived.
    """
    raw = entities.get("standing")
    if raw is None and "modern" in entities:
        modern = entities["modern"]
        if isinstance(modern, bool):
            return (
                PropertyStanding.MODERN.value if modern else PropertyStanding.NON_MODERN.value
            )
        raw = modern
    text = str(raw).strip().upper().replace(" ", "_").replace("-", "_")
    if text in _STANDING_ALIASES:
        return _STANDING_ALIASES[text].value
    return PropertyStanding(text).value
