"""In a talk, only what is meant for the other member is relayed.

Journal of 24 Nov 2026, 17:25: « on peut envoyer des vidéos ? » and « qu'est-ce
qu'elle attend d'un homme ? » were both relayed to Joyce, two of ten messages.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from loka.bounded_contexts.auth.domain.value_objects.phone_number import PhoneNumber
from loka.bounded_contexts.conversation.application.plugs.turn import PlugTurn
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    ConversationSession,
    FlowName,
)
from loka.bounded_contexts.dating.conversation.keys import (
    PENDING_RELAY,
    RELAY_KEY,
    TALK_ID,
    TALK_KEY,
)
from loka.bounded_contexts.dating.conversation.talk import DatingTalk
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.talk import Talk

NOW = datetime(2026, 11, 25, 9, 0, tzinfo=UTC)


def _member(name: str, sex: str) -> Profile:
    return Profile(
        profile_id=uuid.uuid4(), user_id=uuid.uuid4(), first_name=name, age=25, sex=sex,
        profession="x", country="Cameroun", city="Yaoundé", photo_key="p.jpg",
        description="d" * 30, looking=("PEU_IMPORTE",), seeking=None, created_at=NOW,
    )


class Store:
    def __init__(self, talk: Talk, other: Profile) -> None:
        self._talk, self._other = talk, other

    async def talk(self, talk_id: uuid.UUID) -> Talk:
        return self._talk

    async def profile(self, profile_id: uuid.UUID) -> Profile:
        return self._other

    async def save_talk(self, talk: Talk) -> None:
        self._talk = talk


class Desk:
    def __init__(self) -> None:
        self.relayed: list[str] = []

    async def settle(self, turn: PlugTurn, *, step: Any, question_key: Any, context: Any) -> None:
        turn.session.context = {**context, "next_question": question_key}

    async def notify(self, user_id: uuid.UUID, text: str, **_: Any) -> bool:
        self.relayed.append(text)
        return True


def _setup() -> tuple[DatingTalk, Desk, Profile, Talk, ConversationSession]:
    jerry, joyce = _member("Jerry", "HOMME"), _member("Joyce", "FEMME")
    talk = Talk.open(jerry.profile_id, joyce.profile_id, now=NOW)
    session = ConversationSession(
        session_id=uuid.uuid4(), user_id=jerry.user_id,
        phone=PhoneNumber.normalize("+237680424176"), now=NOW, flow=FlowName.DATING_PROFILE,
    )
    session.context = {TALK_ID: str(talk.talk_id), "next_question": TALK_KEY}
    return DatingTalk(Store(talk, joyce)), Desk(), jerry, talk, session  # type: ignore[arg-type]


def _turn(session: ConversationSession, text: str, **facts: Any) -> PlugTurn:
    return PlugTurn(
        session=session, flow=FlowName.DATING_PROFILE, intent="SUPPORT", facts=facts,
        known=facts, fresh=False, text=text, now=NOW, model_reply=facts.pop("reply", None),
    )


async def _apply(talk_: DatingTalk, desk: Desk, member: Profile, talk: Talk, turn: PlugTurn) -> Any:
    async def propose(prefix: str) -> Any:
        raise AssertionError("no proposal expected")

    return await talk_.apply(turn, desk, member, talk.talk_id, propose=propose)  # type: ignore[arg-type]


async def test_an_unclear_message_is_asked_about_then_sent_on_yes() -> None:
    talk_, desk, jerry, talk, session = _setup()
    asked = "Déjà qu'est-ce qu'elle attend d'un homme ?"
    outcome = await _apply(talk_, desk, jerry, talk, _turn(session, asked, addressee="UNCLEAR"))
    assert desk.relayed == []
    assert outcome.question == RELAY_KEY
    assert session.context[PENDING_RELAY] == asked

    await _apply(talk_, desk, jerry, talk, _turn(session, "oui"))
    assert desk.relayed == [f"Jerry : « {asked} »"]


async def test_a_question_for_idia_is_answered_and_not_relayed() -> None:
    talk_, desk, jerry, talk, session = _setup()
    outcome = await _apply(
        talk_, desk, jerry, talk,
        _turn(session, "On peut envoyer des vidéos ?", addressee="IDIA",
              reply="Seulement des messages écrits et des photos."),
    )
    assert desk.relayed == []
    assert outcome.reply_text.startswith("Seulement des messages écrits et des photos.")
    assert talk.side(jerry.profile_id).sent == 0


async def test_a_message_for_the_match_is_relayed_at_once() -> None:
    talk_, desk, jerry, talk, session = _setup()
    await _apply(talk_, desk, jerry, talk, _turn(session, "Tu aimes quoi le week-end ?"))
    assert desk.relayed == ["Jerry : « Tu aimes quoi le week-end ? »"]


async def test_words_for_idia_are_asked_about_even_unclassified() -> None:
    """Journal of 25 Nov 2026, 08:38: « Rencontre » was relayed to Margaret."""
    talk_, desk, jerry, talk, session = _setup()
    for text in ("Rencontre", "Je ne veux pas que tu transmettes à Joyce"):
        session.context = {TALK_ID: str(talk.talk_id), "next_question": TALK_KEY}
        outcome = await _apply(talk_, desk, jerry, talk, _turn(session, text, addressee="MATCH"))
        assert outcome.question == RELAY_KEY
    assert desk.relayed == []
