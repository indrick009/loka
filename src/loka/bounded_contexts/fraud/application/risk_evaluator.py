"""Application service that applies the rule engine to one subject.

``RiskEvaluator`` never commits: it loads facts, scores signals and updates
the profile within the caller's transaction so a use case can stage several
side effects (a new report *and* the freshly bumped profile) atomically.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import cast

from loka.bounded_contexts.fraud.domain.entities.risk_profile import (
    RiskProfile,
    RiskSubject,
)
from loka.bounded_contexts.fraud.domain.services.rule_engine import signals_for
from loka.bounded_contexts.fraud.domain.value_objects.facts import (
    Facts,
    LandlordFacts,
    PropertyFacts,
    UserFacts,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import ValidationFailed
from loka.shared.domain.identifiers import new_id

REPOSITORY_NAME = "fraud_profile"
FACTS_NAME = "fraud_facts"


class RiskEvaluator:
    def __init__(self, uow: UnitOfWork) -> None:
        self._profiles = uow.repository(REPOSITORY_NAME)
        self._facts = uow.repository(FACTS_NAME)

    async def apply(
        self, subject: RiskSubject, subject_id: uuid.UUID, now: datetime
    ) -> RiskProfile:
        facts = await self._gather(subject, subject_id, now)
        signals = signals_for(subject, facts)
        profile = cast(
            RiskProfile | None, await self._profiles.get_by_subject(subject.value, subject_id)
        )
        if profile is None:
            profile = RiskProfile(
                profile_id=new_id(),
                subject=subject,
                subject_id=subject_id,
                now=now,
            )
        profile.apply_signals(signals, now=now)
        await self._profiles.save(profile)
        return profile

    async def _gather(self, subject: RiskSubject, subject_id: uuid.UUID, now: datetime) -> Facts:
        if subject is RiskSubject.LANDLORD:
            return cast(
                LandlordFacts, await self._facts.landlord_facts(subject_id, now=now)
            )
        if subject is RiskSubject.USER:
            return cast(UserFacts, await self._facts.user_facts(subject_id, now=now))
        if subject is RiskSubject.PROPERTY:
            return cast(
                PropertyFacts, await self._facts.property_facts(subject_id, now=now)
            )
        if subject is RiskSubject.CONVERSATION:
            raise ValidationFailed(
                "conversation risk evaluation is not wired yet",
                context={"subject": subject.value},
            )
        raise ValidationFailed("unknown risk subject", context={"subject": subject.value})