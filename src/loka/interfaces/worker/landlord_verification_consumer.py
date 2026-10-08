"""Landlord verification handler.

Drains ``landlord.verification``, whose messages are the
``LandlordVerificationSubmitted`` events staged by the submit use case. The
event carries the request id; the handler re-reads the aggregate so the model
always sees the persisted evidence. A payload without a parseable request id is
a permanent failure and goes to the DLQ; a model outage is handled inside the
use case, which leaves the request for an operator rather than failing here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.landlord.application.use_cases.adjudicate_landlord_verification import (
    AdjudicateLandlordVerificationCommand,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import (
    adjudicate_landlord_verification_use_case,
    unit_of_work,
)
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

LANDLORD_VERIFICATION_QUEUE = "landlord.verification"

_logger = get_logger(__name__)


class LandlordVerificationConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        request_id = parse_verification_request_id(payload)
        async with unit_of_work(self._database) as uow:
            view = await adjudicate_landlord_verification_use_case(
                uow, settings=self._settings
            ).execute(
                AdjudicateLandlordVerificationCommand(request_id=request_id),
                now=datetime.now(UTC),
            )
        _logger.info(
            "landlord_verification_adjudicated",
            request_id=view.request_id,
            route=view.route,
            decision=view.decision,
            confidence=view.confidence,
            can_publish=view.can_publish,
        )


def parse_verification_request_id(payload: dict[str, Any]) -> uuid.UUID:
    """Extract the request id, or refuse the message permanently."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("landlord verification payload is missing or not an object")
    value = body.get("request_id")
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError("landlord verification field 'request_id' is missing")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(
            f"landlord verification field 'request_id' is not a uuid: {value}"
        ) from exc