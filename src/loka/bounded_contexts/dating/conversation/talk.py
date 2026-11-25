"""Two members who both said yes, asking each other questions through idia.

Every message is relayed as written, under the sender's first name; a photo is
relayed as a photo. Nothing that would reach the other without the unlock — a
number, an account, a link — is passed on. The decision is explicit: CONFIRMER
or STOP, because a bare « oui » here answers the other's question.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Awaitable, Callable

from loka.bounded_contexts.conversation.application.plugs.forms import say
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn, Steer
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.dating.conversation import talk_texts, texts
from loka.bounded_contexts.dating.conversation.keys import (
    MATCH_KEY,
    PENDING_RELAY,
    PLUS_KEY,
    RELAY_KEY,
    TALK_ID,
    TALK_KEY,
    UNLOCK_KEY,
)
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.relay import gives_contact
from loka.bounded_contexts.dating.domain.store import DatingStore
from loka.bounded_contexts.dating.domain.talk import Talk
from loka.bounded_contexts.dating.domain.talk_reader import TalkLine, TalkReader, TalkReading
from loka.bounded_contexts.payment.application.unlock_contact import UnlockContact, payer_answer
from loka.bounded_contexts.payment.domain.contact_unlock import DATING_PLUS

Propose = Callable[[str], Awaitable[TurnOutcome]]
# The last lines of a member's thread, oldest first.
ThreadOf = Callable[[uuid.UUID], Awaitable[list[TalkLine]]]

# The two words idia itself asks for, when they are the whole message. Every
# other message is read by the model, which sees the thread, the other's name
# and the message kept (decision of 25 Nov 2026: no word lists in its place).
_KEYWORDS = {"confirmer": "CONFIRM", "confirme": "CONFIRM", "stop": "END"}
_PLUS = re.compile(r"^\W*(plus|more)\W*$")
# A relayed message is a question, not a letter.
LONGEST = 600
# Shown to the model in the talk's facts: who the other is, and what idia
# kept unsent, so « envoie-lui » points at something.
WITH_KEY = "discussion_avec"
KEPT_KEY = "message_garde"


def _words_for(other: Profile, text: str, extracted: object) -> str:
    """What to pass on: the words for the other, without « demande-lui » around them.

    Only ever a piece of what was written: « « Pourquoi il y a un souci ? »
    transmets le message entre parenthèses » reached Margaret whole (journal of
    25 Nov 2026, 09:49). A rewording by the model is not the member's words.
    """
    if not isinstance(extracted, str):
        return text
    piece = " ".join(extracted.split()).strip(" «»\"'")
    if len(piece) >= 2 and _folded(piece) in _folded(text):
        return piece
    return text


def _folded(text: str) -> str:
    # The typographic apostrophe first: folding to ASCII would drop it and glue
    # « j (curly) arrête » into one word.
    spaced = text.replace("\u2019", " ").replace("'", " ")
    plain = unicodedata.normalize("NFKD", spaced).encode("ascii", "ignore").decode("ascii")
    return plain.casefold()


def _steer(talk: Talk, other: Profile) -> Steer:
    return Steer(
        flow=FlowName.DATING_PROFILE,
        step=FlowStep.COLLECT_CHOICE,
        question_key=TALK_KEY,
        context={TALK_ID: str(talk.talk_id), WITH_KEY: other.first_name},
    )


class DatingTalk:
    def __init__(
        self,
        store: DatingStore,
        *,
        unlock: UnlockContact | None = None,
        reader: TalkReader | None = None,
        thread_of: ThreadOf | None = None,
    ) -> None:
        self._store = store
        self._unlock = unlock
        self._reader = reader
        self._thread_of = thread_of

    @staticmethod
    async def _reply(
        turn: PlugTurn,
        desk: PlugDesk,
        talk: Talk,
        other: Profile,
        text: str,
        key: str = TALK_KEY,
        *,
        extra: dict[str, str] | None = None,
    ) -> TurnOutcome:
        """Answer inside the talk, the thread left on ``key``: the offer of
        more questions waits for its answer, anything else for the next message."""
        # What was not relayed is kept for « transmets-lui »; anything else
        # clears it, so an old message is never sent by a later « oui ».
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_CHOICE,
            question_key=key,
            context={
                TALK_ID: str(talk.talk_id),
                WITH_KEY: other.first_name,
                PENDING_RELAY: "",
                KEPT_KEY: "",
                **(extra or {}),
            },
        )
        return say(text, question=key, step=FlowStep.COLLECT_CHOICE, voiced=False)

    async def waiting(self, member: Profile) -> Profile | None:
        """Someone who said yes to this member, and he to them, not yet talked to.

        Their talk could not open when they both said yes, because one of them
        was already talking with someone else.
        """
        if await self._store.open_talk_of(member.profile_id) is not None:
            return None
        for partner_id in await self._store.liked_by_both(member.profile_id):
            if await self._store.ever_talked(member.profile_id, partner_id):
                continue
            if await self._store.open_talk_of(partner_id) is not None:
                continue
            partner = await self._store.profile(partner_id)
            if partner is not None:
                return partner
        return None

    async def begin(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        other: Profile,
        *,
        prefix: str,
        propose: Propose,
    ) -> TurnOutcome:
        """Both said yes: the questions start, unless one is already talking."""
        busy = await self._store.open_talk_of(member.profile_id) or await self._store.open_talk_of(
            other.profile_id
        )
        if busy is not None:
            return await propose(f"{prefix}\n{talk_texts.waiting(other)}".strip())
        talk = Talk.open(member.profile_id, other.profile_id, now=turn.now)
        await self._store.save_talk(talk)
        await desk.notify(
            other.user_id,
            talk_texts.opened(member, "fr"),
            now=turn.now,
            steer=_steer(talk, member),
        )
        opened = talk_texts.opened(other, turn.language)
        return await self._reply(turn, desk, talk, other, f"{prefix}\n{opened}".strip())

    async def apply(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        talk_id: uuid.UUID,
        *,
        propose: Propose,
    ) -> TurnOutcome:
        talk = await self._store.talk(talk_id)
        if talk is None or not talk.is_open:
            return await propose("")
        other = await self._store.profile(talk.other(member.profile_id).profile_id)
        if other is None:
            talk.close()
            await self._store.save_talk(talk)
            return await propose("")
        media_key = turn.facts.get("media_key")
        if isinstance(media_key, str):
            if turn.facts.get("media_kind") != "IMAGE":
                return await self._reply(turn, desk, talk, other, talk_texts.only_text_and_photos())
            return await self._photo(turn, desk, member, other, talk, media_key)
        text = " ".join((turn.text or "").split())
        folded = _folded(text)
        if turn.session.context.get("next_question") == PLUS_KEY:
            return await self._buy(turn, desk, member, other, talk)
        if _PLUS.match(folded):
            if self._unlock is None:
                return await self._reply(
                    turn, desk, talk, other, talk_texts.plus_unavailable(other)
                )
            return await self._reply(
                turn, desk, talk, other, talk_texts.plus_offer(other), PLUS_KEY
            )
        if not text:
            return await self._reply(turn, desk, talk, other, talk_texts.only_text_and_photos())
        pending = turn.session.context.get(PENDING_RELAY)
        kept = pending if isinstance(pending, str) and pending else None
        reading = None if folded.strip(" .!") in _KEYWORDS else await self._read(
            turn, member, other, kept, text
        )
        decision = _KEYWORDS.get(folded.strip(" .!")) or (
            reading.decision if reading else turn.facts.get("addressee")
        )
        relay_text = reading.relay_text if reading else turn.facts.get("relay_text")
        answer_for_idia = (reading.reply if reading else None) or turn.model_reply
        if decision == "END":
            return await self._stop(turn, desk, member, other, talk, propose=propose)
        if decision == "CONFIRM":
            return await self._confirm(turn, desk, member, other, talk)
        if decision == "SEND":
            if kept is None:
                return await self._reply(turn, desk, talk, other, talk_texts.nothing_to_send(other))
            return await self._relay(turn, desk, member, other, talk, kept)
        if decision == "KEEP" and kept is not None:
            return await self._reply(turn, desk, talk, other, talk_texts.kept_back(other))
        words = _words_for(other, text, relay_text)
        keep = {PENDING_RELAY: words[:LONGEST], KEPT_KEY: words[:LONGEST]}
        if decision == "IDIA" and answer_for_idia:
            answer = f"{answer_for_idia}\n{talk_texts.not_relayed(other)}"
            return await self._reply(turn, desk, talk, other, answer, extra=keep)
        if decision != "MATCH":
            # Only when the model itself cannot tell, or did not say.
            return await self._reply(
                turn,
                desk,
                talk,
                other,
                talk_texts.relay_or_not(other, words[:LONGEST]),
                RELAY_KEY,
                extra=keep,
            )
        return await self._relay(turn, desk, member, other, talk, words)

    async def _read(
        self, turn: PlugTurn, member: Profile, other: Profile, kept: str | None, text: str
    ) -> TalkReading | None:
        if self._reader is None:
            return None
        thread = await self._thread_of(turn.session.id) if self._thread_of else []
        if thread and thread[-1].speaker == "member" and thread[-1].text.strip() == text:
            thread = thread[:-1]
        return await self._reader.read(
            member=member.first_name,
            other=other.first_name,
            thread=thread,
            kept=kept,
            message=text,
        )

    async def _relay(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        other: Profile,
        talk: Talk,
        text: str,
    ) -> TurnOutcome:
        if gives_contact(text):
            return await self._reply(turn, desk, talk, other, talk_texts.contact_refused())
        if turn.rejected.get("message") == "inappropriate":
            return await self._reply(turn, desk, talk, other, talk_texts.inappropriate())
        side = talk.side(member.profile_id)
        if side.messages_left <= 0:
            return await self._reply(turn, desk, talk, other, talk_texts.used_up(other, "messages"))
        side.sent += 1
        await self._store.save_talk(talk)
        await desk.notify(other.user_id, talk_texts.relayed(member, text[:LONGEST]), now=turn.now)
        return await self._reply(
            turn, desk, talk, other, talk_texts.sent(other, side.messages_left)
        )

    async def _photo(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        other: Profile,
        talk: Talk,
        media_key: str,
    ) -> TurnOutcome:
        side = talk.side(member.profile_id)
        if side.photos_left <= 0:
            return await self._reply(turn, desk, talk, other, talk_texts.used_up(other, "photos"))
        side.photos += 1
        await self._store.save_talk(talk)
        await desk.notify(
            other.user_id,
            talk_texts.photo_caption(member),
            now=turn.now,
            media_object_key=media_key,
        )
        return await self._reply(
            turn, desk, talk, other, talk_texts.photo_sent(other, side.photos_left)
        )

    async def _confirm(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, other: Profile, talk: Talk
    ) -> TurnOutcome:
        matched = talk.confirm(member.profile_id)
        await self._store.save_talk(talk)
        if not matched:
            await desk.notify(other.user_id, talk_texts.they_confirmed(member), now=turn.now)
            return await self._reply(turn, desk, talk, other, talk_texts.confirmed(other))
        # Consent on both sides, after their questions: the only moment a match
        # exists. Only the man unlocks the conversation for now; the woman is
        # asked for nothing and gets his number when he pays (decision of
        # 25 Nov 2026).
        payer = other if member.sex == "FEMME" else member
        if payer is member:
            await desk.notify(
                other.user_id,
                texts.match_waiting(member, "fr"),
                now=turn.now,
                steer=Steer(
                    flow=FlowName.DATING_PROFILE, step=FlowStep.PUBLISHED, question_key=None
                ),
            )
            await desk.settle(
                turn,
                step=FlowStep.COLLECT_CHOICE,
                question_key=UNLOCK_KEY,
                context={MATCH_KEY: str(other.profile_id)},
            )
            return say(texts.match(other, turn.language), question=UNLOCK_KEY, voiced=False)
        await desk.notify(
            other.user_id,
            texts.match(member, "fr"),
            now=turn.now,
            steer=Steer(
                flow=FlowName.DATING_PROFILE,
                step=FlowStep.COLLECT_CHOICE,
                question_key=UNLOCK_KEY,
                context={MATCH_KEY: str(member.profile_id)},
            ),
        )
        await desk.settle(turn, step=FlowStep.PUBLISHED, question_key=None, context={})
        return say(texts.match_waiting(other, turn.language), voiced=False)

    async def _stop(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        other: Profile,
        talk: Talk,
        *,
        propose: Propose,
    ) -> TurnOutcome:
        talk.close()
        await self._store.save_talk(talk)
        await desk.notify(
            other.user_id,
            talk_texts.they_stopped(member),
            now=turn.now,
            steer=Steer(flow=FlowName.DATING_PROFILE, step=FlowStep.PUBLISHED, question_key=None),
        )
        partner = await self.waiting(other)
        if partner is not None and partner.profile_id != member.profile_id:
            await self._open_between(desk, other, partner, turn=turn)
        return await propose(talk_texts.stopped())

    async def _open_between(
        self, desk: PlugDesk, a: Profile, b: Profile, *, turn: PlugTurn
    ) -> None:
        """A talk that was waiting on one of them, opened for two other threads."""
        talk = Talk.open(a.profile_id, b.profile_id, now=turn.now)
        await self._store.save_talk(talk)
        for me, them in ((a, b), (b, a)):
            await desk.notify(
                me.user_id, talk_texts.opened(them, "fr"), now=turn.now, steer=_steer(talk, them)
            )

    async def _buy(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, other: Profile, talk: Talk
    ) -> TurnOutcome:
        """The 1 000 FCFA for another pack of messages and photos."""
        if self._unlock is None:
            return await self._reply(turn, desk, talk, other, talk_texts.plus_unavailable(other))
        payer = payer_answer(turn.text, turn.session.phone.e164)
        if payer is None:
            return await self._reply(
                turn, desk, talk, other, talk_texts.plus_offer(other), PLUS_KEY
            )
        if payer == "":
            return await self._reply(turn, desk, talk, other, talk_texts.plus_declined(other))
        asked = await self._unlock.request(
            payer_user_id=member.user_id,
            target_user_id=other.user_id,
            purpose=DATING_PLUS,
            subject_id=talk.talk_id,
            payer_phone=payer,
            now=turn.now,
        )
        if not asked:
            return await self._reply(turn, desk, talk, other, texts.unlock_failed(), PLUS_KEY)
        return await self._reply(turn, desk, talk, other, talk_texts.plus_requested(payer))
