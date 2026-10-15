"""Keep "si un logement correspond, je reviens vers vous".

Two queues, one promise:

- ``demand.matching`` hears a demand recorded or restated, and a listing
  published, and asks the Property context whether a demand is now answered;
- ``demand.proposals`` hears that it is, and writes to the tenant.

They are separate so a tenant who cannot be written to — no thread at all —
parks one proposal in the dead-letter queue instead of rolling back a match
that is already true.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.ai.application.use_cases.present_match_proposals import (
    PresentMatchProposalsUseCase,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.property.application.use_cases.match_tenant_demands import (
    MatchTenantDemandsUseCase,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database

DEMAND_MATCHING_QUEUE = "demand.matching"
DEMAND_PROPOSALS_QUEUE = "demand.proposals"

_DEMAND_EVENTS = frozenset({"TenantDemandRecorded", "TenantDemandRestated"})


class DemandMatchingConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database

    async def handle(self, payload: dict[str, Any]) -> None:
        event_type = payload.get("event_type")
        now = datetime.now(UTC)
        async with unit_of_work(self._database) as uow:
            matcher = MatchTenantDemandsUseCase(uow)
            if event_type in _DEMAND_EVENTS:
                await matcher.for_demand(uuid_field(payload, "demand_id"), now=now)
            elif event_type == "PropertyPublished":
                await matcher.for_listing(uuid_field(payload, "property_id"), now=now)
            else:
                raise PermanentHandlerError(f"no matching for event {event_type!r}")


class DemandProposalConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database

    async def handle(self, payload: dict[str, Any]) -> None:
        tenant_id = uuid_field(payload, "tenant_id")
        proposals = _metadata(payload).get("proposals")
        if not isinstance(proposals, list) or not proposals:
            raise PermanentHandlerError("a demand was answered with no proposals")

        async with unit_of_work(self._database) as uow:
            sent = await PresentMatchProposalsUseCase(
                uow, uow.repository(CONVERSATION_SESSION_REPOSITORY)
            ).execute(
                tenant_id=tenant_id,
                proposals=[item for item in proposals if isinstance(item, dict)],
                correlation_id=str(payload.get("correlation_id") or uuid.uuid4()),
                now=datetime.now(UTC),
            )
        if not sent:
            # Waiting will not give him a thread: park it where it can be seen.
            raise PermanentHandlerError(f"tenant {tenant_id} has no conversation to write to")


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("event payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("event payload has no metadata")
    return metadata


def uuid_field(payload: dict[str, Any], name: str) -> uuid.UUID:
    value = _metadata(payload).get(name)
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError(f"event field {name!r} is missing")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(f"event field {name!r} is not a uuid: {value}") from exc
