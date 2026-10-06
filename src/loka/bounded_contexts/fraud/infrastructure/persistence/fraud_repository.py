"""SQLAlchemy fraud repositories.

The risk profile maps signals both onto the profile row (JSONB snapshot) and
onto ``fraud_signals`` (append-only audit trail): the snapshot answers "what
is the current score", the audit trail answers "what evidence was seen".
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    RecommendedAction,
    Report,
    ReportReason,
    ReportStatus,
    RiskProfile,
    RiskReason,
    RiskScore,
    RiskSignal,
    RiskSubject,
)
from loka.bounded_contexts.fraud.infrastructure.persistence.models import (
    FraudSignalRow,
    ReportRow,
    RiskProfileRow,
)


class SqlAlchemyRiskProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_subject(self, subject: str, subject_id: uuid.UUID) -> RiskProfile | None:
        result = await self._session.execute(
            select(RiskProfileRow).where(
                RiskProfileRow.subject == subject,
                RiskProfileRow.subject_id == subject_id,
            )
        )
        row = result.scalar_one_or_none()
        return _profile_from_row(row) if row is not None else None

    async def get(self, profile_id: uuid.UUID) -> RiskProfile | None:
        row = await self._session.get(RiskProfileRow, profile_id)
        return _profile_from_row(row) if row is not None else None

    async def save(self, profile: RiskProfile) -> None:
        row = await self._session.get(RiskProfileRow, profile.profile_id)
        row = _row_from_profile(profile, row)
        if row is not None:
            self._session.add(row)
        await self._sync_signals(profile)

    async def list_escalations(self, limit: int = 50) -> list[RiskProfile]:
        result = await self._session.execute(
            select(RiskProfileRow)
            .where(
                (RiskProfileRow.under_manual_review.is_(True))
                | (RiskProfileRow.restricted_until.isnot(None))
            )
            .order_by(RiskProfileRow.last_evaluated_at.desc().nullslast())
            .limit(limit)
        )
        return [_profile_from_row(row) for row in result.scalars()]

    async def count_by_band(self) -> dict[str, int]:
        result = await self._session.execute(
            select(RiskProfileRow.risk_band, func.count())
            .group_by(RiskProfileRow.risk_band)
        )
        return {band: int(count) for band, count in result.all()}

    async def active_signal_reasons(self, limit: int = 10) -> list[tuple[str, int]]:
        result = await self._session.execute(
            select(FraudSignalRow.reason, func.count())
            .where(FraudSignalRow.active.is_(True))
            .group_by(FraudSignalRow.reason)
            .order_by(func.count().desc())
            .limit(limit)
        )
        return [(str(reason), int(count)) for reason, count in result.all()]

    async def _sync_signals(self, profile: RiskProfile) -> None:
        """Point the audit trail at the profile's current active signals."""
        result = await self._session.execute(
            select(FraudSignalRow.id, FraudSignalRow.reason).where(
                FraudSignalRow.profile_id == profile.profile_id,
                FraudSignalRow.active.is_(True),
            )
        )
        active: dict[str, uuid.UUID] = {
            reason: row_id for row_id, reason in result.all()
        }
        wanted = {signal.reason.value for signal in profile.signals}
        stale = active.keys() - wanted
        if stale:
            await self._session.execute(
                update(FraudSignalRow)
                .where(
                    FraudSignalRow.profile_id == profile.profile_id,
                    FraudSignalRow.reason.in_(stale),
                    FraudSignalRow.active.is_(True),
                )
                .values(active=False, expires_at=profile.updated_at)
            )
        detected = profile.last_evaluated_at or profile.updated_at
        for signal in profile.signals:
            row_id = active.get(signal.reason.value)
            if row_id is not None:
                await self._session.execute(
                    update(FraudSignalRow)
                    .where(FraudSignalRow.id == row_id)
                    .values(weight=signal.weight, detail=signal.detail, detected_at=detected)
                )
            else:
                self._session.add(
                    FraudSignalRow(
                        id=uuid.uuid4(),
                        profile_id=profile.profile_id,
                        subject=profile.subject.value,
                        subject_id=profile.subject_id,
                        reason=signal.reason.value,
                        weight=signal.weight,
                        detail=signal.detail,
                        source="rules",
                        active=True,
                        detected_at=detected,
                        expires_at=None,
                    )
                )


class SqlAlchemyReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, report: Report) -> None:
        self._session.add(_row_from_report(report))

    async def get(self, report_id: uuid.UUID) -> Report | None:
        row = await self._session.get(ReportRow, report_id)
        return _report_from_row(row) if row is not None else None

    async def save(self, report: Report) -> None:
        row = await self._session.get(ReportRow, report.report_id)
        if row is None:
            self._session.add(_row_from_report(report))
            return
        row.target_type = report.target_type
        row.target_id = report.target_id
        row.reason = report.reason.value
        row.description = report.description
        row.status = report.status.value
        row.evidence = list(report.evidence)
        row.assigned_to = report.assigned_to
        row.resolution = report.resolution
        row.updated_at = report.updated_at

    async def list(
        self,
        *,
        status: ReportStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Report]:
        stmt = select(ReportRow)
        if status is not None:
            stmt = stmt.where(ReportRow.status == status.value)
        stmt = stmt.order_by(ReportRow.created_at.desc()).offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return [_report_from_row(row) for row in result.scalars()]

    async def count_by_status(self) -> dict[str, int]:
        result = await self._session.execute(
            select(ReportRow.status, func.count()).group_by(ReportRow.status)
        )
        return {status: int(count) for status, count in result.all()}

    async def open_count_for_target(self, target_type: str, target_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(ReportRow)
            .where(
                ReportRow.target_type == target_type,
                ReportRow.target_id == target_id,
                ReportRow.status.in_(["OPEN", "INVESTIGATING"]),
            )
        )
        return int(result.scalar_one())


def _profile_from_row(row: RiskProfileRow) -> RiskProfile:
    profile = RiskProfile(
        profile_id=row.id,
        subject=RiskSubject(row.subject),
        subject_id=row.subject_id,
        now=row.created_at,
    )
    profile.current_score = RiskScore(score=row.risk_score, reasons=tuple(row.reasons))
    profile.signals = [_signal_from_dict(signal) for signal in row.signals]
    profile.recommended_action = RecommendedAction(row.recommended_action)
    profile.under_manual_review = row.under_manual_review
    profile.restricted_until = row.restricted_until
    profile.last_evaluated_at = row.last_evaluated_at
    profile.updated_at = row.updated_at
    profile.mark_persisted(0)
    return profile


def _signal_from_dict(data: dict[str, Any]) -> RiskSignal:
    return RiskSignal(
        reason=RiskReason(str(data["reason"])),
        weight=int(data["weight"]),
        detail=str(data["detail"]) if data.get("detail") else None,
    )


def _row_from_profile(profile: RiskProfile, row: RiskProfileRow | None) -> RiskProfileRow | None:
    if row is None:
        row = RiskProfileRow(id=profile.profile_id)
    row.subject = profile.subject.value
    row.subject_id = profile.subject_id
    row.risk_score = profile.current_score.score
    row.risk_band = profile.current_score.band.value
    row.recommended_action = profile.recommended_action.value
    row.reasons = list(profile.current_score.reasons)
    row.signals = [_signal_to_dict(signal) for signal in profile.signals]
    row.under_manual_review = profile.under_manual_review
    row.restricted_until = profile.restricted_until
    row.last_evaluated_at = profile.last_evaluated_at
    row.created_at = profile.created_at
    row.updated_at = profile.updated_at
    return row


def _signal_to_dict(signal: RiskSignal) -> dict[str, Any]:
    return {"reason": signal.reason.value, "weight": signal.weight, "detail": signal.detail}


def _report_from_row(row: ReportRow) -> Report:
    report = Report(
        report_id=row.id,
        reporter_id=row.reporter_id,
        reason=ReportReason(row.reason),
        description=row.description,
        target_type=row.target_type,
        target_id=row.target_id,
        now=row.created_at,
        evidence=list(row.evidence),
    )
    report.status = ReportStatus(row.status)
    report.assigned_to = row.assigned_to
    report.resolution = row.resolution
    report.updated_at = row.updated_at
    report.mark_persisted(0)
    return report


def _row_from_report(report: Report) -> ReportRow:
    return ReportRow(
        id=report.report_id,
        reporter_id=report.reporter_id,
        target_type=report.target_type,
        target_id=report.target_id,
        reason=report.reason.value,
        description=report.description,
        status=report.status.value,
        evidence=list(report.evidence),
        assigned_to=report.assigned_to,
        resolution=report.resolution,
        created_at=report.created_at,
        updated_at=report.updated_at,
    )