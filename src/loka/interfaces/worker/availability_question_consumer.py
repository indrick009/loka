"""Deliver "le logement est encore disponible ?" to the landlord.

A tenant picked a lodging, so its landlord has to be asked. He is not
answering a message of his own — nothing of his triggered this — so the
question cannot be staged as a reply to a turn. It is staged against the thread
he last used, with his own number on it.

Two translations stand between the event and the message, and both have bitten
already: a listing names a landlord *profile* while a WhatsApp thread belongs
to an *account*, and the account is what holds the phone number.

A landlord with no session at all is a permanent failure, not a retry: no
amount of waiting will produce a thread for someone who has never written to
Kora, and the question would be redelivered for ever.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.landlord.infrastructure.composition import LANDLORD_DIRECTORY
from loka.bounded_contexts.messaging.domain.events.message_events import (
    WhatsAppMessageSendRequested,
)
from loka.bounded_contexts.messaging.infrastructure.composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.property.application.use_cases.check_availability import (
    LANDLORD_QUESTION_KEY,
)
from loka.shared.infrastructure.broker.consumer import ConsumerBase, PermanentHandlerError
from loka.shared.infrastructure.broker.topology import Broker, QueueSpec
from loka.shared.infrastructure.composition import unit_of_work
from loka.shared.infrastructure.config.settings import Settings
from loka.shared.infrastructure.db.engine import Database
from loka.shared.infrastructure.logging import get_logger

AVAILABILITY_QUESTION_QUEUE = "availability.questions"

_logger = get_logger(__name__)


class AvailabilityQuestionConsumer(ConsumerBase):
    def __init__(
        self, spec: QueueSpec, broker: Broker, database: Database, settings: Settings
    ) -> None:
        super().__init__(broker)
        self.queue_spec = spec
        self._database = database
        self._settings = settings

    async def handle(self, payload: dict[str, Any]) -> None:
        landlord_profile_id = _uuid_field(payload, "landlord_id")
        property_id = _uuid_field(payload, "property_id")

        async with unit_of_work(self._database) as uow:
            landlords = uow.repository(LANDLORD_DIRECTORY)
            account_id = await landlords.user_id_for_profile(landlord_profile_id)
            if account_id is None:
                raise PermanentHandlerError(
                    f"no account owns landlord profile {landlord_profile_id}"
                )

            sessions = uow.repository(CONVERSATION_SESSION_REPOSITORY)
            session = await sessions.latest_by_user(account_id)
            if session is None:
                raise PermanentHandlerError(
                    f"landlord {account_id} has no conversation to be asked in"
                )

            uow.events.stage(
                [
                    WhatsAppMessageSendRequested(
                        aggregate_type="ConversationSession",
                        aggregate_id=session.id,
                        aggregate_version=session.revision,
                        occurred_at=datetime.now(UTC),
                        session_id=session.id,
                        recipient_phone=session.phone.e164,
                        reply_kind="QUESTION",
                        question_key=LANDLORD_QUESTION_KEY,
                        # No model-written sentence: the wording of this
                        # question *is* the business rule, and a rephrased
                        # version asks something slightly different.
                        text=None,
                        intent="AVAILABILITY_CHECK",
                        language=session.language,
                    )
                ],
                # The question belongs to the check that caused it, so a
                # support trace follows one tenant's choice from his number to
                # the landlord's phone.
                correlation_id=str(_uuid_field(payload, "check_id")),
            )
            await uow.commit()

        _logger.info(
            "availability_question_sent",
            property_id=str(property_id),
            landlord_id=str(landlord_profile_id),
            session_id=str(session.id),
        )


def _uuid_field(payload: dict[str, Any], name: str) -> uuid.UUID:
    body = payload.get("payload")
    if not isinstance(body, dict):
        raise PermanentHandlerError("availability event payload is missing or not an object")
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        raise PermanentHandlerError("availability event payload has no metadata")
    value = metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise PermanentHandlerError(f"availability event field {name!r} is missing")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PermanentHandlerError(
            f"availability event field {name!r} is not a uuid: {value}"
        ) from exc
