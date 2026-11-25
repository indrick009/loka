"""« montre un autre » is a pass, never a yes (journal of 25 Nov 2026, 07:28)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from loka.bounded_contexts.auth.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.conversation.application.plugs.turn import PlugTurn
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
)
from loka.bounded_contexts.dating.conversation.turns import _answer

NOW = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)


def _turn(text: str, intent: str = "SUPPORT") -> PlugTurn:
    session = ConversationSession(
        session_id=uuid.uuid4(), user_id=uuid.uuid4(),
        phone=PhoneNumber.normalize("+237677364224"), now=NOW, flow=FlowName.DATING_PROFILE,
    )
    return PlugTurn(
        session=session, flow=FlowName.DATING_PROFILE, intent=intent, facts={}, known={},
        fresh=False, text=text, now=NOW,
    )


@pytest.mark.parametrize(
    "text", ["Montre un autre", "Une autre personne svp", "je veux voir un autre profil", "suivant"]
)
def test_asking_for_another_is_a_pass(text: str) -> None:
    assert _answer(_turn(text, intent="AFFIRMATIVE")) is False


def test_a_plain_yes_is_still_a_yes() -> None:
    assert _answer(_turn("oui")) is True
