"""Fraud rule engine.

A pure function from a fact snapshot to a list of risk signals. No I/O, no
clock, no randomness: the same facts always produce the same signals, which is
what keeps the recomputed risk score explainable.

Rules are deliberately conservative. A rule fires only on evidence the signalled
context actually stores, and the aggregate decides what the combined weight
means for the human-in-the-loop band. Nothing here bans anyone.
"""

from __future__ import annotations

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    RiskReason,
    RiskSignal,
    RiskSubject,
)
from loka.bounded_contexts.fraud.domain.value_objects.facts import (
    LandlordFacts,
    PropertyFacts,
    UserFacts,
)

VERIFIED = "VERIFIED"


def signals_for(subject: RiskSubject, facts: object) -> list[RiskSignal]:
    """Dispatch a fact snapshot to the rules of its subject kind."""
    if subject is RiskSubject.LANDLORD:
        if not isinstance(facts, LandlordFacts):
            return []
        return landlord_signals(facts)
    if subject is RiskSubject.USER:
        if not isinstance(facts, UserFacts):
            return []
        return user_signals(facts)
    if subject is RiskSubject.PROPERTY:
        if not isinstance(facts, PropertyFacts):
            return []
        return property_signals(facts)
    return []


def landlord_signals(facts: LandlordFacts) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    if facts.verification_status != VERIFIED:
        signals.append(
            RiskSignal(
                reason=RiskReason.IDENTITY_UNVERIFIED,
                weight=40,
                detail=f"verification_status={facts.verification_status}",
            )
        )
    if facts.open_report_count >= 1:
        signals.append(_listing_reports(facts.open_report_count))
    return signals


def user_signals(facts: UserFacts) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    if facts.phone_e164_hash and facts.accounts_with_same_phone > 1:
        signals.append(
            RiskSignal(
                reason=RiskReason.PHONE_REUSE,
                weight=50,
                detail=f"{facts.accounts_with_same_phone} accounts share the phone number",
            )
        )
    if facts.failed_payments_14d >= 5:
        signals.append(
            RiskSignal(
                reason=RiskReason.MULTIPLE_REJECTIONS,
                weight=60,
                detail=f"{facts.failed_payments_14d} failed payments in 14 days",
            )
        )
    elif facts.failed_payments_14d >= 2:
        signals.append(
            RiskSignal(
                reason=RiskReason.MULTIPLE_REJECTIONS,
                weight=30,
                detail=f"{facts.failed_payments_14d} failed payments in 14 days",
            )
        )
    return signals


def property_signals(facts: PropertyFacts) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    if (
        facts.city
        and facts.city_median_price_xaf is not None
        and facts.city_median_price_xaf > 0
    ):
        price = facts.public_price_xaf if facts.public_price_xaf is not None else facts.rent_xaf
        if price is not None and price < facts.city_median_price_xaf // 2:
            signals.append(
                RiskSignal(
                    reason=RiskReason.PRICE_ANOMALY,
                    weight=30,
                    detail=(
                        f"price {price} vs median {facts.city_median_price_xaf} "
                        f"in {facts.city}"
                    ),
                )
            )
    if facts.other_landlords_sharing_media >= 1:
        weight = min(45 + 15 * (facts.other_landlords_sharing_media - 1), 85)
        signals.append(
            RiskSignal(
                reason=RiskReason.DUPLICATE_PHOTOS,
                weight=weight,
                detail=f"{facts.other_landlords_sharing_media} other landlords share media",
            )
        )
    if facts.open_report_count >= 1:
        signals.append(_listing_reports(facts.open_report_count))
    return signals


def _listing_reports(open_reports: int) -> RiskSignal:
    weight = min(30 + 10 * (open_reports - 1), 70)
    return RiskSignal(
        reason=RiskReason.LISTING_REPORTED,
        weight=weight,
        detail=f"{open_reports} open report(s)",
    )