"""Composition root of the Messaging context.

Wiring lives here so the use case keeps depending on ports only, and so the
index key used by the identity lookup is derived from configuration in exactly
one place.
"""

from __future__ import annotations

from typing import Any

from loka.bounded_contexts.identity.infrastructure.persistence.user_repository import (
    SqlAlchemyUserRepository,
)
from loka.bounded_contexts.messaging.application.use_cases.receive_inbound_message import (
    ReceiveInboundMessageUseCase,
)
from loka.bounded_contexts.messaging.infrastructure.persistence import (
    conversation_session_repository,
    inbound_message_repository,
)
from loka.shared.application.unit_of_work import UnitOfWork
from loka.shared.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

INBOUND_MESSAGE_REPOSITORY = "inbound_messages"
CONVERSATION_SESSION_REPOSITORY = "conversation_sessions"


def _inbound_messages(uow: SqlAlchemyUnitOfWork) -> Any:
    return inbound_message_repository.SqlAlchemyInboundMessageRepository(uow.session)


def _conversation_sessions(uow: SqlAlchemyUnitOfWork) -> Any:
    return conversation_session_repository.SqlAlchemyConversationSessionRepository(uow.session)


MESSAGING_REPOSITORY_FACTORIES: dict[str, Any] = {
    INBOUND_MESSAGE_REPOSITORY: _inbound_messages,
    CONVERSATION_SESSION_REPOSITORY: _conversation_sessions,
}


def receive_inbound_message_use_case(
    uow: UnitOfWork, *, index_key: bytes
) -> ReceiveInboundMessageUseCase:
    if not isinstance(uow, SqlAlchemyUnitOfWork):
        raise TypeError(
        "receive_inbound_message_use_case requires a SqlAlchemyUnitOfWork"
    )
    session = uow.session
    return ReceiveInboundMessageUseCase(
        uow,
        inbound=uow.repository("inbound_messages"),
        sessions=uow.repository("conversation_sessions"),
        users=SqlAlchemyUserRepository(session, index_key=index_key, unit_of_work=uow),
    )
