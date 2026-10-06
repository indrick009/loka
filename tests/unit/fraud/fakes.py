"""In-memory doubles for the fraud unit tests.

``FakeFraudFacts`` mirrors the SQL adapter's contract: report counts are
derived live from the report store (so dismissing a report lowers the score on
the next evaluation), verification defaults to ``PENDING``, and a missing
property raises not-found.
"""

from __future__ import annotations

import uuid
from types import TracebackType
from typing import Any

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    ReportStatus,
    RiskProfile,
)
from loka.bounded_contexts.fraud.domain.value_objects.facts import (
    LandlordFacts,
    PropertyFacts,
    UserFacts,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ResourceNotFound

OPEN_REPORT_STATUS = (ReportStatus.OPEN, ReportStatus.INVESTIGATING)


class FakeRiskProfileRepository:
    def __init__(self) -> None:
        self.profiles: dict[uuid.UUID, RiskProfile] = {}
        self.saved: list[RiskProfile] = []

    async def get_by_subject(self, subject: str, subject_id: uuid.UUID) -> RiskProfile | None:
        for profile in self.profiles.values():
            if profile.subject.value == subject and profile.subject_id == subject_id:
                return profile
        return None

    async def get(self, profile_id: uuid.UUID) -> RiskProfile | None:
        return self.profiles.get(profile_id)

    async def save(self, profile: RiskProfile) -> None:
        self.profiles[profile.profile_id] = profile
        self.saved.append(profile)

    async def list_escalations(self, limit: int = 50) -> list[RiskProfile]:
        matches = [
            p
            for p in self.profiles.values()
            if p.under_manual_review or p.restricted_until is not None
        ]
        return sorted(matches, key=lambda p: p.last_evaluated_at or p.updated_at)[-limit:]

    async def count_by_band(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for profile in self.profiles.values():
            band = profile.band.value
            counts[band] = counts.get(band, 0) + 1
        return counts

    async def active_signal_reasons(self, limit: int = 10) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for profile in self.profiles.values():
            for reason in {signal.reason.value for signal in profile.signals}:
                counts[reason] = counts.get(reason, 0) + 1
        ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return ordered[:limit]

    def seed(self, profile: RiskProfile) -> None:
        self.profiles[profile.profile_id] = profile


class FakeReportRepository:
    def __init__(self) -> None:
        self.reports: dict[uuid.UUID, Report] = {}
        self.added: list[Report] = []
        self.saved: list[Report] = []

    async def add(self, report: Report) -> None:
        self.reports[report.report_id] = report
        self.added.append(report)

    async def get(self, report_id: uuid.UUID) -> Report | None:
        return self.reports.get(report_id)

    async def save(self, report: Report) -> None:
        self.reports[report.report_id] = report
        self.saved.append(report)

    async def list(
        self,
        *,
        status: ReportStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Report]:
        reports = list(self.reports.values())
        if status is not None:
            reports = [r for r in reports if r.status is status]
        reports.sort(key=lambda r: r.created_at, reverse=True)
        return reports[offset : offset + limit]

    async def count_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for report in self.reports.values():
            counts[report.status.value] = counts.get(report.status.value, 0) + 1
        return counts

    async def open_count_for_target(self, target_type: str, target_id: uuid.UUID) -> int:
        return sum(
            1
            for report in self.reports.values()
            if report.target_type == target_type
            and report.target_id == target_id
            and report.status in OPEN_REPORT_STATUS
        )

    def seed(self, report: Report) -> None:
        self.reports[report.report_id] = report


class FakeFraudFacts:
    """Report counts are read live from ``reports`` to mimic the SQL adapter."""

    def __init__(self, reports: FakeReportRepository | None = None) -> None:
        self._reports = reports or FakeReportRepository()
        self.verifications: dict[uuid.UUID, str] = {}
        self.photos: dict[uuid.UUID, str] = {}
        self.landlords: dict[uuid.UUID, list[uuid.UUID]] = {}
        self.users: dict[uuid.UUID, tuple[str, int]] = {}
        self.properties: dict[uuid.UUID, PropertyFacts] = {}
        self.payments: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID]] = {}
        self.failed_payments: dict[uuid.UUID, int] = {}

    async def landlord_facts(self, landlord_id: uuid.UUID, *, now: Any) -> LandlordFacts:
        verification = self.verifications.get(landlord_id, "PENDING")
        open_reports = await self._reports.open_count_for_target("LANDLORD", landlord_id)
        return LandlordFacts(
            landlord_id=landlord_id,
            verification_status=verification,
            active_property_count=len(self.landlords.get(landlord_id, [])),
            open_report_count=open_reports,
        )

    async def user_facts(self, user_id: uuid.UUID, *, now: Any) -> UserFacts:
        phone_hash, accounts = self.users.get(user_id, ("", 1))
        return UserFacts(
            user_id=user_id,
            phone_e164_hash=phone_hash,
            accounts_with_same_phone=accounts,
            failed_payments_14d=self.failed_payments.get(user_id, 0),
        )

    async def property_facts(self, property_id: uuid.UUID, *, now: Any) -> PropertyFacts:
        base = self.properties.get(property_id)
        if base is None:
            raise ResourceNotFound(
                "property not found", context={"property_id": str(property_id)}
            )
        open_reports = await self._reports.open_count_for_target("PROPERTY", property_id)
        return PropertyFacts(
            property_id=base.property_id,
            landlord_id=base.landlord_id,
            status=base.status,
            city=base.city,
            rent_xaf=base.rent_xaf,
            public_price_xaf=base.public_price_xaf,
            other_landlords_sharing_media=base.other_landlords_sharing_media,
            open_report_count=open_reports,
            city_median_price_xaf=base.city_median_price_xaf,
        )

    async def payment_subjects(
        self, payment_id: uuid.UUID
    ) -> tuple[uuid.UUID, uuid.UUID] | None:
        return self.payments.get(payment_id)

    def seed_payment(
        self, payment_id: uuid.UUID, tenant_id: uuid.UUID, landlord_id: uuid.UUID
    ) -> None:
        self.payments[payment_id] = (tenant_id, landlord_id)


class FakeUnitOfWork(UnitOfWork):
    def __init__(
        self,
        profiles: FakeRiskProfileRepository,
        reports: FakeReportRepository,
        facts: FakeFraudFacts,
    ) -> None:
        self.profiles = profiles
        self.reports = reports
        self.facts = facts
        self.collected: list[object] = []
        self.commits = 0

    def __enter__(self) -> FakeUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        return None

    @property
    def events(self) -> object:
        return None

    def collect(self, aggregate: object) -> None:
        self.collected.append(aggregate)

    def repository(self, name: str) -> Any:
        if name == "fraud_profile":
            return self.profiles
        if name == "fraud_report":
            return self.reports
        if name == "fraud_facts":
            return self.facts
        raise LookupError(name)