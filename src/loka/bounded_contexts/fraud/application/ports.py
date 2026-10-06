"""Ports the fraud application layer depends on.

``FraudFacts`` is the only boundary through which the fraud context reads the
behavioural evidence stored by other contexts. Implementations are encouraged
to use defensive SQL: facts are an *advisory* view, and a missing row must
never crash an evaluation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.fraud.domain.value_objects.facts import (
    LandlordFacts,
    PropertyFacts,
    UserFacts,
)


class FraudFacts(Protocol):
    async def landlord_facts(self, landlord_id: uuid.UUID, *, now: datetime) -> LandlordFacts: ...

    async def user_facts(self, user_id: uuid.UUID, *, now: datetime) -> UserFacts: ...

    async def property_facts(self, property_id: uuid.UUID, *, now: datetime) -> PropertyFacts: ...

    async def payment_subjects(self, payment_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
        """Resolve (tenant_id, landlord_id) for a payment id, or None."""


class FraudEvaluation(Protocol):
    async def evaluate(self, *, subject: str, subject_id: uuid.UUID, now: datetime) -> None:
        """Evaluate one subject, persisting its risk profile."""