"""Conversation analysis handler.

Drains ``ai.requests``, whose messages are the ``ConversationAnalysisRequested``
events staged by the ingestion use case. It re-reads the message through the
messaging repository instead of trusting the event: the event carries ids only,
so the text stays in exactly one place.

What it refuses to do is invent. A payload without the ids of an existing
message and session is a permanent failure and goes to the DLQ, because retrying
it would fail identically for ever. A model outage, on the other hand, is
transient and must be retried.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.ai.application.use_cases.analyse_conversation_message import (
    AnalysisCommand,
)
from loka.bounded_contexts.ai.infrastructure.composition import (
    analyse_conversation_message_use_case,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

AI_REQUEST_QUEUE = "ai.requests"

_logger = get_logger(__name__)


class ConversationAnalysisConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        message_id, session_id = parse_analysis_payload(payload)
        async with unit_of_work(self._database) as uow:
            report = await analyse_conversation_message_use_case(
                uow, settings=self._settings
            ).execute(
                AnalysisCommand(message_id=message_id, session_id=session_id),
                now=datetime.now(UTC),
            )
        _logger.info(
            "conversation_message_analysed",
            message_id=str(report.message_id),
            session_id=str(report.session_id),
            intent=report.intent,
            confidence=report.confidence,
            used_llm=report.used_llm,
            accepted=report.accepted,
            reason=report.reason,
            next_step=report.next_step,
            question_key=report.question_key,
            rejected_facts=sorted(report.rejected_facts),
            cost_usd=str(report.cost_usd),
        )


def parse_analysis_payload(payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID]:
    """Extract the ids, or refuse the message permanently."""
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("analysis payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("analysis payload has no metadata")

    def require(name: str) -> uuid.UUID:
        value = metadata.get(name)
        if not isinstance(value, str) or not value.strip():
            raise PermanentHandlerError(f"analysis field '{name}' is missing")
        try:
            return uuid.UUID(value)
        except ValueError as exc:
            raise PermanentHandlerError(
                f"analysis field '{name}' is not a uuid: {value}"
            ) from exc

    return require("message_id"), require("session_id")
