"""A tenant's search, carried from the conversation into the catalogue.

The pipeline states a search in its own narrow terms — a city, a type, a budget
— and this is the only place that translates it into a recorded demand. It
returns a bool and no demand: the conversation's job is to acknowledge, and
what gets prepared afterwards is nobody's business inside the thread.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from loka.bounded_contexts.ai.application.ports import ConversationSearchQuery
from loka.bounded_contexts.property.application.use_cases.record_tenant_demand import (
    RecordTenantDemandCommand,
    RecordTenantDemandUseCase,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.domain.errors import DomainError


class ConversationTenantDemands:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def record(
        self, *, tenant_id: uuid.UUID, query: ConversationSearchQuery, now: datetime
    ) -> bool:
        try:
            await RecordTenantDemandUseCase(self._uow).execute(
                RecordTenantDemandCommand(
                    tenant_id=tenant_id,
                    property_type=query.property_types[0] if query.property_types else None,
                    city=query.city,
                    neighbourhoods=query.neighbourhoods,
                    max_rent_xaf=query.budget_xaf,
                    near=query.near,
                ),
                now=now,
            )
        except DomainError:
            # The demand could not be stored, so there is nothing to come back
            # to. Reported as False rather than raised: the caller then sends a
            # reply saying so instead of promising a follow-up that will never
            # happen, and the turn is never lost to a silence.
            return False
        return True
