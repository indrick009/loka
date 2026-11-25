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

    async def busy(self, user_id: uuid.UUID, **_: Any) -> bool:
        return False


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

    await _apply(talk_, desk, jerry, talk, _turn(session, "oui", addressee="SEND"))
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
    turn = _turn(session, "Tu aimes quoi le week-end ?", addressee="MATCH")
    await _apply(talk_, desk, jerry, talk, turn)
    assert desk.relayed == ["Jerry : « Tu aimes quoi le week-end ? »"]


async def test_a_message_the_model_did_not_place_is_asked_about() -> None:
    talk_, desk, jerry, talk, session = _setup()
    outcome = await _apply(talk_, desk, jerry, talk, _turn(session, "Rencontre"))
    assert outcome.question == RELAY_KEY
    assert desk.relayed == []


async def test_an_end_closes_the_talk_and_tells_the_other() -> None:
    talk_, desk, jerry, talk, session = _setup()
    talk_._store.open_talk_of = _none  # type: ignore[attr-defined]
    talk_._store.liked_by_both = _nobody  # type: ignore[attr-defined]
    proposed: list[str] = []

    async def propose(prefix: str) -> Any:
        proposed.append(prefix)

    turn = _turn(session, "Dis-lui que je ne veux plus", addressee="END")
    await talk_.apply(turn, desk, jerry, talk.talk_id, propose=propose)  # type: ignore[arg-type]
    assert not talk.is_open
    assert proposed
    assert not any("ne veux plus" in text for text in desk.relayed)


async def _none(*_: Any) -> None:
    return None


async def _nobody(*_: Any) -> list[Any]:
    return []


async def test_send_to_him_relays_the_message_kept_before() -> None:
    talk_, desk, jerry, talk, session = _setup()
    first = _turn(session, "Je veux une photo de toi", addressee="UNCLEAR")
    await _apply(talk_, desk, jerry, talk, first)
    await _apply(talk_, desk, jerry, talk, _turn(session, "Send to him", addressee="SEND"))
    assert desk.relayed == ["Jerry : « Je veux une photo de toi »"]


async def test_only_the_quoted_words_are_relayed() -> None:
    talk_, desk, jerry, talk, session = _setup()
    turn = _turn(
        session, "Demande lui « tu habites où à Yaoundé »",
        addressee="MATCH", relay_text="tu habites où à Yaoundé",
    )
    await _apply(talk_, desk, jerry, talk, turn)
    assert desk.relayed == ["Jerry : « tu habites où à Yaoundé »"]


async def test_confirmer_inside_a_short_message_is_the_decision() -> None:
    talk_, desk, jerry, talk, session = _setup()
    await _apply(talk_, desk, jerry, talk, _turn(session, "okay good. CONFIRMER"))
    assert not any("okay good" in text for text in desk.relayed)
    assert talk.side(jerry.profile_id).sent == 0


async def test_envoie_a_her_name_passes_on_the_kept_message() -> None:
    talk_, desk, jerry, talk, session = _setup()
    first = _turn(session, "Envoie moi quelques photos de toi", addressee="IDIA", reply="…")
    await _apply(talk_, desk, jerry, talk, first)
    await _apply(talk_, desk, jerry, talk, _turn(session, "Envoie à joyce", addressee="SEND"))
    assert desk.relayed == ["Jerry : « Envoie moi quelques photos de toi »"]


async def test_an_unread_message_is_asked_about_not_relayed() -> None:
    talk_, desk, jerry, talk, session = _setup()
    outcome = await _apply(talk_, desk, jerry, talk, _turn(session, "Je veux changer ma photo"))
    assert desk.relayed == []
    assert outcome.question == RELAY_KEY
