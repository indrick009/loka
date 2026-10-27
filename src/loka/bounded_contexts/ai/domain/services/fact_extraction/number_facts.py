"""Numbers a user gives: amounts, charges, a choice, a count."""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.ai.domain.services.fact_extraction.limits import (
    MAX_PLAUSIBLE_RENT_XAF,
    MAX_RESULT_CHOICES,
)
from loka.bounded_contexts.housing.domain.value_objects.enums import (
    BedroomCount,
    ChargingPolicy,
)
from loka.bounded_contexts.housing.domain.value_objects.money import Money
from loka.shared.domain.errors import ValidationFailed


def _as_int(raw: Any) -> int:
    """Coerce a candidate number, or refuse it as a validation failure.

    A model may answer ``{"price": "beaucoup"}``. ``int()`` raises ``ValueError``
    there, which is not a ``DomainError``: without this the exception escapes
    the extractor and crashes the consumer instead of dropping the field and
    asking the question again.
    """
    if isinstance(raw, bool) or raw is None:
        raise ValidationFailed("expected a number", context={"value": repr(raw)})
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValidationFailed(
            "expected a number", context={"value": repr(raw)}
        ) from exc


def _build_charges(entities: dict[str, Any]) -> tuple[int, str, dict[str, int] | None]:
    """The monthly charges and whether they are included in the rent.

    A landlord who says "charges incluses" states a *policy*, not an amount; one
    who says "15 000 de charges" states an amount. Both must produce a value the
    Property quality gate accepts, otherwise it reports MISSING_CHARGES forever.

    When the landlord splits the breakdown ("eau 5 000, électricité 10 000"),
    the two amounts are summed into ``charges`` and the split is returned
    separately so the published listing can state it without a schema change.
    """
    included = entities.get("charges_included")
    policy_raw = entities.get("charging_policy")
    if policy_raw is not None and str(policy_raw).strip():
        policy = ChargingPolicy(str(policy_raw).strip().upper().replace(" ", "_"))
    elif isinstance(included, bool):
        policy = ChargingPolicy.INCLUDED if included else ChargingPolicy.EXTRA
    elif "charges" in entities or "water_charges" in entities or "electricity_charges" in entities:
        policy = ChargingPolicy.EXTRA
    else:
        policy = ChargingPolicy.INCLUDED

    breakdown: dict[str, int] | None = None
    water = entities.get("water_charges")
    electricity = entities.get("electricity_charges")
    if water is not None or electricity is not None:
        water_amount = _as_int(water) if water is not None else 0
        electricity_amount = _as_int(electricity) if electricity is not None else 0
        if min(water_amount, electricity_amount) < 0:
            raise ValidationFailed(
                "charges cannot be negative",
                context={"water": water_amount, "electricity": electricity_amount},
            )
        breakdown = {"water": water_amount, "electricity": electricity_amount}
        amount = water_amount + electricity_amount
    else:
        amount = _as_int(entities["charges"]) if "charges" in entities else 0
        if amount < 0:
            raise ValidationFailed("charges cannot be negative", context={"value": amount})
    return amount, policy.value, breakdown


def _build_choice(raw: Any) -> int:
    """Which line of the result list the tenant picked, one-based.

    Bounded by what was actually shown: a tenant answering "7" to a list of
    three has not chosen anything, and resolving it to the nearest listing
    would charge him 1 000 FCFA for a lodging he never saw.
    """
    choice = _as_int(raw)
    if not 1 <= choice <= MAX_RESULT_CHOICES:
        raise ValidationFailed(
            "a choice must name one of the listings shown",
            context={"entity": "property_choice", "value": repr(raw)},
        )
    return choice


def _build_quantity(raw: Any) -> int:
    """How many identical lodgings one listing stands for (§5).

    Same type, same characteristics, same price: one listing, quantity two.
    Different prices are two listings, because a tenant is comparing a price —
    splitting that case is the conversation's job, not this function's.
    """
    quantity = _as_int(raw)
    if quantity < 1:
        raise ValidationFailed(
            "a listing must stand for at least one lodging",
            context={"entity": "quantity", "value": repr(raw)},
        )
    if quantity > 50:
        # Past this it is a price, a surface or a typo that wandered into the
        # wrong field, and a listing claiming fifty studios is not credible.
        raise ValidationFailed(
            "a single listing cannot stand for more than 50 lodgings",
            context={"entity": "quantity", "value": repr(raw)},
        )
    return quantity


def _build_rent(raw: Any) -> int:
    amount = Money(amount=_as_int(raw)).require_positive(reason="rent must be positive")
    if amount.amount > MAX_PLAUSIBLE_RENT_XAF:
        raise ValidationFailed(
            "rent is above the plausible range for this market",
            context={"amount": amount.amount, "ceiling": MAX_PLAUSIBLE_RENT_XAF},
        )
    return amount.amount


def _build_rooms(entities: dict[str, Any]) -> dict[str, int]:
    rooms = BedroomCount(
        count=_as_int(entities.get("bedrooms", 0)),
        bathrooms=_as_int(entities.get("bathrooms", 1)),
    )
    return {"bedrooms": rooms.count, "bathrooms": rooms.bathrooms}
