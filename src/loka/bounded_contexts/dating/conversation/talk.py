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
# « okay good. CONFIRMER » was relayed as a message (journal of 25 Nov 2026,
# 11:01): the word, anywhere in a few words, is the decision.
_CONFIRM_WORD = re.compile(r"\bconfirmer?\b")
_SHORT = 8
_PLUS = re.compile(r"^\W*(plus|more)\W*$")
_SEND = re.compile(r"^\W*(oui|ouais|yes|ok|okay|envoie|transmets|vas y|d accord)\b")
# « Envoie à Joyce », « transmets-lui », « send to him »: an order about the
# message kept just before, never a message (journal of 25 Nov 2026, 12:52).
_PASS_ON = re.compile(r"^\W*(envoie\w*|envoi|transmet\w*|send|forward|passe\w*)\b")
_KEEP = re.compile(r"^\W*(non|no|laisse|annule|pas la peine)\b")
# A relayed message is a question, not a letter.
LONGEST = 600


_ABOUT_IDIA = re.compile(r"\b(transmet\w*|idia|rencontres?|profils?|d autres?)\b")


def _about_idia(folded: str, other: Profile) -> bool:
    """Words for idia, or the other member named in the third person."""
    name = _folded(other.first_name).strip()
    named = bool(name) and re.search(rf"\b{re.escape(name)}\b", folded) is not None
    return bool(_ABOUT_IDIA.search(folded)) or named


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
        turn: PlugTurn,
        desk: PlugDesk,
        talk: Talk,
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
            context={TALK_ID: str(talk.talk_id), PENDING_RELAY: "", **(extra or {})},
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
        addressee = turn.facts.get("addressee")
        if addressee == "END":
            # « Je ne veux pas Dave, je veux une autre rencontre », then « dis-lui
            # que je ne veux plus »: both kept the talk open, and his messages
            # kept reaching her (journal of 25 Nov 2026, 08:54 and 09:45).
            return await self._stop(turn, desk, member, other, talk, propose=propose)
        short = len(folded.split()) <= _SHORT
        if (
            _CONFIRM.match(folded)
            or addressee == "CONFIRM"
            or (short and _CONFIRM_WORD.search(folded))
        ):
            return await self._confirm(turn, desk, member, other, talk)
        if turn.session.context.get("next_question") == PLUS_KEY:
            return await self._buy(turn, desk, member, other, talk)
        pending = turn.session.context.get(PENDING_RELAY)
        if not isinstance(pending, str) or not pending:
            pending = None
        if pending is not None and _PASS_ON.match(folded) and len(folded.split()) <= 5:
            addressee = "SEND"
        if addressee == "SEND":
            # « Send to him » after a message idia kept: that message, not these
            # words (journal of 25 Nov 2026, 10:40).
            if pending is None:
                return await self._reply(turn, desk, talk, talk_texts.nothing_to_send(other))
            return await self._relay(turn, desk, member, other, talk, pending)
        if turn.session.context.get("next_question") == RELAY_KEY and pending is not None:
            if _SEND.match(folded):
                return await self._relay(turn, desk, member, other, talk, pending)
            if _KEEP.match(folded):
                return await self._reply(turn, desk, talk, talk_texts.kept_back(other))
            # Anything else is a new message, read as such below.
        if _PLUS.match(folded):
            if self._unlock is None:
                return await self._reply(turn, desk, talk, talk_texts.plus_unavailable(other))
            return await self._reply(turn, desk, talk, talk_texts.plus_offer(other), PLUS_KEY)
        if not text:
            return await self._reply(turn, desk, talk, talk_texts.only_text_and_photos())
        # Not everything written in a talk is for the other member: « on peut
        # envoyer des vidéos ? » and « qu'est-ce qu'elle attend d'un homme ? »
        # were both relayed to her (journal of 24 Nov 2026, 17:25).
        words = _words_for(other, text, turn.facts.get("relay_text"))
        if addressee != "IDIA" and _about_idia(_folded(words), other):
            # « Rencontre », « je ne veux pas que tu transmettes à Margaret »:
            # read as for her, both were relayed (journal of 25 Nov 2026, 08:38).
            addressee = "UNCLEAR"
        kept = {PENDING_RELAY: words[:LONGEST]}
        if addressee == "IDIA" and turn.model_reply:
            answer = f"{turn.model_reply}\n{talk_texts.not_relayed(other)}"
            return await self._reply(turn, desk, talk, answer, extra=kept)
        if addressee != "MATCH":
            # Unread is not « for her »: « je veux changer ma photo » and « je
            # parle à toi » reached Jerry unread (journal of 25 Nov 2026, 12:48).
            return await self._reply(
                turn,
                desk,
                talk,
                talk_texts.relay_or_not(other, words[:LONGEST]),
                RELAY_KEY,
                extra=kept,
            )
        return await self._relay(turn, desk, member, other, talk, words)

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
