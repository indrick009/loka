"""Two members who both said yes, asking each other questions through Kora.

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
    PLUS_KEY,
    TALK_ID,
    TALK_KEY,
    UNLOCK_KEY,
)
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.bounded_contexts.dating.domain.relay import gives_contact
from loka.bounded_contexts.dating.domain.store import DatingStore
from loka.bounded_contexts.dating.domain.talk import Talk
from loka.bounded_contexts.payment.application.unlock_contact import UnlockContact, payer_answer
from loka.bounded_contexts.payment.domain.contact_unlock import DATING_PLUS

Propose = Callable[[str], Awaitable[TurnOutcome]]

# « oui je confirme », « ok elle me plaît »: a decision may open with a yes.
_LEAD = r"^\W*(?:(?:oui|ouais|ok|yes|non|no)\W+)?(?:je\s+)?"
_CONFIRM = re.compile(_LEAD + r"(confirm\w*|(il|elle) me plai?\w*|(le|la) veux|valide\w*)\b")
_STOP = re.compile(
    _LEAD + r"(stop|arrete\w*|j arrete|(il|elle) ne me plai?\w* pas|pas interesse\w*|"
    r"abandonne|ne (le|la) veux pas|ne veux plus)\b"
)
_PLUS = re.compile(r"^\W*(plus|more)\W*$")
# A relayed message is a question, not a letter.
LONGEST = 600


def _folded(text: str) -> str:
    # The typographic apostrophe first: folding to ASCII would drop it and glue
    # « j (curly) arrête » into one word.
    spaced = text.replace("\u2019", " ").replace("'", " ")
    plain = unicodedata.normalize("NFKD", spaced).encode("ascii", "ignore").decode("ascii")
    return plain.casefold()


def _steer(talk: Talk) -> Steer:
    return Steer(
        flow=FlowName.DATING_PROFILE,
        step=FlowStep.COLLECT_CHOICE,
        question_key=TALK_KEY,
        context={TALK_ID: str(talk.talk_id)},
    )


class DatingTalk:
    def __init__(self, store: DatingStore, *, unlock: UnlockContact | None = None) -> None:
        self._store = store
        self._unlock = unlock

    @staticmethod
    async def _reply(
        turn: PlugTurn, desk: PlugDesk, talk: Talk, text: str, key: str = TALK_KEY
    ) -> TurnOutcome:
        """Answer inside the talk, the thread left on ``key``: the offer of
        more questions waits for its answer, anything else for the next message."""
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_CHOICE,
            question_key=key,
            context={TALK_ID: str(talk.talk_id)},
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
            other.user_id, talk_texts.opened(member, "fr"), now=turn.now, steer=_steer(talk)
        )
        opened = talk_texts.opened(other, turn.language)
        return await self._reply(turn, desk, talk, f"{prefix}\n{opened}".strip())

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
                return await self._reply(turn, desk, talk, talk_texts.only_text_and_photos())
            return await self._photo(turn, desk, member, other, talk, media_key)
        text = " ".join((turn.text or "").split())
        folded = _folded(text)
        if _STOP.match(folded):
            return await self._stop(turn, desk, member, other, talk, propose=propose)
        if _CONFIRM.match(folded):
            return await self._confirm(turn, desk, member, other, talk)
        if turn.session.context.get("next_question") == PLUS_KEY:
            return await self._buy(turn, desk, member, other, talk)
        if _PLUS.match(folded):
            if self._unlock is None:
                return await self._reply(turn, desk, talk, talk_texts.plus_unavailable(other))
            return await self._reply(turn, desk, talk, talk_texts.plus_offer(other), PLUS_KEY)
        if not text:
            return await self._reply(turn, desk, talk, talk_texts.only_text_and_photos())
        return await self._relay(turn, desk, member, other, talk, text)

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
            return await self._reply(turn, desk, talk, talk_texts.contact_refused())
        if turn.rejected.get("message") == "inappropriate":
            return await self._reply(turn, desk, talk, talk_texts.inappropriate())
        side = talk.side(member.profile_id)
        if side.messages_left <= 0:
            return await self._reply(turn, desk, talk, talk_texts.used_up(other, "messages"))
        side.sent += 1
        await self._store.save_talk(talk)
        await desk.notify(other.user_id, talk_texts.relayed(member, text[:LONGEST]), now=turn.now)
        return await self._reply(turn, desk, talk, talk_texts.sent(other, side.messages_left))

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
            return await self._reply(turn, desk, talk, talk_texts.used_up(other, "photos"))
        side.photos += 1
        await self._store.save_talk(talk)
        await desk.notify(
            other.user_id,
            talk_texts.photo_caption(member),
            now=turn.now,
            media_object_key=media_key,
        )
        return await self._reply(turn, desk, talk, talk_texts.photo_sent(other, side.photos_left))

    async def _confirm(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, other: Profile, talk: Talk
    ) -> TurnOutcome:
        matched = talk.confirm(member.profile_id)
        await self._store.save_talk(talk)
        if not matched:
            await desk.notify(other.user_id, talk_texts.they_confirmed(member), now=turn.now)
            return await self._reply(turn, desk, talk, talk_texts.confirmed(other))
        # Consent on both sides, after their questions: the only moment a match exists.
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
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_CHOICE,
            question_key=UNLOCK_KEY,
            context={MATCH_KEY: str(other.profile_id)},
        )
        return say(texts.match(other, turn.language), question=UNLOCK_KEY, voiced=False)

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
                me.user_id, talk_texts.opened(them, "fr"), now=turn.now, steer=_steer(talk)
            )

    async def _buy(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, other: Profile, talk: Talk
    ) -> TurnOutcome:
        """The 1 000 FCFA for another pack of messages and photos."""
        if self._unlock is None:
            return await self._reply(turn, desk, talk, talk_texts.plus_unavailable(other))
        payer = payer_answer(turn.text, turn.session.phone.e164)
        if payer is None:
            return await self._reply(turn, desk, talk, talk_texts.plus_offer(other), PLUS_KEY)
        if payer == "":
            return await self._reply(turn, desk, talk, talk_texts.plus_declined(other))
        asked = await self._unlock.request(
            payer_user_id=member.user_id,
            target_user_id=other.user_id,
            purpose=DATING_PLUS,
            subject_id=talk.talk_id,
            payer_phone=payer,
            now=turn.now,
        )
        if not asked:
            return await self._reply(turn, desk, talk, texts.unlock_failed(), PLUS_KEY)
        return await self._reply(turn, desk, talk, talk_texts.plus_requested(payer))
