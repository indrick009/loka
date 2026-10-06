"""Fraud persistence contracts.

The application layer only knows these protocols; concrete SQLAlchemy adapters
live in the infrastructure layer and share the unit of work's session.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    Report,
    ReportStatus,
    RiskProfile,
)


class RiskProfileRepository(Protocol):
    async def get_by_subject(self, subject: str, subject_id: uuid.UUID) -> RiskProfile | None: ...

    async def get(self, profile_id: uuid.UUID) -> RiskProfile | None: ...

    async def save(self, profile: RiskProfile) -> None: ...

    async def list_escalations(self, limit: int = 50) -> list[RiskProfile]: ...

    async def count_by_band(self) -> dict[str, int]: ...

    async def active_signal_reasons(self, limit: int = 10) -> list[tuple[str, int]]: ...


class ReportRepository(Protocol):
    async def add(self, report: Report) -> None: ...

    async def get(self, report_id: uuid.UUID) -> Report | None: ...

    async def save(self, report: Report) -> None: ...

    async def list(
        self,
        *,
        status: ReportStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Report]: ...

    async def count_by_status(self) -> dict[str, int]: ...

    async def open_count_for_target(self, target_type: str, target_id: uuid.UUID) -> int: ...