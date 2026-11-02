"""What a Dating turn does: build the profile, propose, record a yes or a no.

Two yeses open a talk (see ``talk``): questions relayed by Kora, then each
confirms. Only then is it a match, and the contact can be unlocked.
"""

from __future__ import annotations

import uuid

from loka.bounded_contexts.conversation.application.plugs.forms import say, stored
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn, Steer
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.dating.conversation import texts
from loka.bounded_contexts.dating.conversation.keys import (
    LIKE_KEY,
    MATCH_KEY,
    PLUS_KEY,
    PROPOSED_KEY,
    TALK_ID,
    TALK_KEY,
    UNLOCK_KEY,
)
from loka.bounded_contexts.dating.conversation.spec import DATING
from loka.bounded_contexts.dating.conversation.talk import DatingTalk
from loka.bounded_contexts.dating.domain.matching import best_candidates
from loka.bounded_contexts.dating.domain.profile import ADULT_AGE, Profile
from loka.bounded_contexts.dating.domain.store import DatingStore
from loka.bounded_contexts.payment.application.unlock_contact import UnlockContact, payer_answer
from loka.bounded_contexts.payment.domain.contact_unlock import DATING as DATING_PURPOSE

# Never taken from a passing remark: the description is asked with its privacy
# promise, and where to look only once the description is in.
ASKED_ONLY = ("description", "looking")
_YES = ("oui", "yes", "ok", "d'accord", "dac", "volontiers", "bien sûr", "carrément")
_NO = ("non", "no", "pas intéress", "bof", "suivant")


def _answer(turn: PlugTurn) -> bool | None:
    if turn.intent == "AFFIRMATIVE":
        return True
    if turn.intent == "NEGATIVE":
        return False
    folded = (turn.text or "").strip().casefold()
    if folded.startswith(_NO):
        return False
    if folded.startswith(_YES):
        return True
    return None


class DatingTurns:
    def __init__(self, store: DatingStore, *, unlock: UnlockContact | None = None) -> None:
        self._store = store
        self._unlock = unlock
        self._talk = DatingTalk(store, unlock=unlock)

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        user, lang = turn.session.user_id, turn.language
        if user is None or await self._store.refused_minor(user):
            return await self._close(turn, desk, texts.minor(lang))
        member = await self._store.profile_of(user)
        if member is None:
            return await self._register(turn, desk, user)
        question = turn.session.context.get("next_question")
        talk_id = turn.session.context.get(TALK_ID)
        if not turn.fresh and question in (TALK_KEY, PLUS_KEY) and talk_id:
            return await self._talk.apply(
                turn,
                desk,
                member,
                uuid.UUID(str(talk_id)),
                propose=lambda prefix: self._propose(turn, desk, member, prefix=prefix),
            )
        matched = turn.session.context.get(MATCH_KEY)
        if not turn.fresh and turn.session.context.get("next_question") == UNLOCK_KEY and matched:
            return await self._pay(turn, desk, member, uuid.UUID(str(matched)))
        proposed = turn.session.context.get(PROPOSED_KEY)
        if not turn.fresh and turn.session.context.get("next_question") == LIKE_KEY and proposed:
            return await self._answer(turn, desk, member, uuid.UUID(str(proposed)))
        return await self._propose(turn, desk, member, prefix="")

    async def _close(self, turn: PlugTurn, desk: PlugDesk, text: str) -> TurnOutcome:
        await desk.settle(turn, step=FlowStep.PUBLISHED, question_key=None, context={})
        # Exact copy: rewritten, « c'est noté » became « je n'ai pas encore de
        # photo pour lui » after a member asked to see one.
        return say(text, step=FlowStep.PUBLISHED, voiced=False)

    async def _register(self, turn: PlugTurn, desk: PlugDesk, user: uuid.UUID) -> TurnOutcome:
        known, lang = turn.known, turn.language
        unasked = {name for name in ASKED_ONLY if name in turn.facts and turn.open_field != name}
        if unasked:
            # Only answers to their own questions: observed live, « je cherche une
            # femme sérieuse » was kept as the description and « je vis à
            # Yaoundé » as where to look, so the member was never told the
            # description matters most and nobody else reads it.
            known = {name: value for name, value in known.items() if name not in unasked}
        age = known.get("age")
        if isinstance(age, int) and age < ADULT_AGE:
            # Refused, not hidden: nothing about the person is kept.
            await self._store.refuse_minor(user, now=turn.now)
            return await self._close(turn, desk, texts.minor(lang))
        missing = DATING.missing(turn.flow, known)
        if missing:
            field = missing[0]
            prefix = (
                texts.inappropriate(lang)
                if turn.rejected.get("description") == "inappropriate"
                else ""
            )
            key = DATING.question_key(field.name)
            await desk.settle(
                turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=stored(known)
            )
            text = f"{prefix}\n{field.ask(lang)}".strip()
            return say(text, question=key, step=FlowStep.COLLECT_FIELD, voiced=False)
        member = Profile.create(
            user_id=user,
            now=turn.now,
            fields={**known, "photo_key": known["photo"]},
        )
        await self._store.add(member)
        return await self._propose(turn, desk, member, prefix=texts.ready(lang))

    async def _propose(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, *, prefix: str
    ) -> TurnOutcome:
        lang = turn.language
        partner = await self._talk.waiting(member)
        if partner is not None:
            return await self._talk.begin(
                turn,
                desk,
                member,
                partner,
                prefix=prefix,
                propose=lambda again: self._propose(turn, desk, member, prefix=again),
            )
        excluded = await self._store.answered(member.profile_id)
        ranked = best_candidates(member, await self._store.members(), excluded=excluded)
        if not ranked:
            return await self._close(turn, desk, f"{prefix}\n{texts.nobody_yet(lang)}".strip())
        other, score = ranked[0]
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_CHOICE,
            question_key=LIKE_KEY,
            context={PROPOSED_KEY: str(other.profile_id), "_score": round(score, 3)},
        )
        text = f"{prefix}\n{texts.proposal(other, lang)}".strip()
        return say(
            text,
            question=LIKE_KEY,
            step=FlowStep.COLLECT_CHOICE,
            voiced=False,
            media=other.photo_key or None,
        )

    async def _answer(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, proposed: uuid.UUID
    ) -> TurnOutcome:
        lang = turn.language
        answer = _answer(turn)
        other = await self._store.profile(proposed)
        if other is None:
            return await self._propose(turn, desk, member, prefix="")
        if answer is None:
            return say(texts.yes_or_no(lang), question=LIKE_KEY, step=FlowStep.COLLECT_CHOICE)
        raw_score = turn.session.context.get("_score")
        score = float(raw_score) if isinstance(raw_score, int | float) else 0.0
        await self._store.answer(
            from_profile=member.profile_id,
            to_profile=other.profile_id,
            liked=answer,
            score=score,
            now=turn.now,
        )
        if not answer:
            return await self._propose(turn, desk, member, prefix=texts.passed(lang))
        their_answer = await self._store.likes(other.profile_id, member.profile_id)
        if their_answer:
            # Both said yes: their questions first, the match once both confirm.
            return await self._talk.begin(
                turn,
                desk,
                member,
                other,
                prefix="",
                propose=lambda prefix: self._propose(turn, desk, member, prefix=prefix),
            )
        if their_answer is None and await self._store.open_talk_of(other.profile_id) is None:
            # Not while they talk with someone else: the proposal would take
            # their thread away from that talk. It reaches them afterwards.
            await desk.notify(
                other.user_id,
                texts.proposal(member, "fr"),
                media_object_key=member.photo_key or None,
                now=turn.now,
                steer=Steer(
                    flow=FlowName.DATING_PROFILE,
                    step=FlowStep.COLLECT_CHOICE,
                    question_key=LIKE_KEY,
                    context={PROPOSED_KEY: str(member.profile_id), "_score": score},
                ),
            )
        return await self._propose(turn, desk, member, prefix=texts.liked(lang))

    async def _pay(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, matched: uuid.UUID
    ) -> TurnOutcome:
        """The 1 000 FCFA unlock, only ever offered after both said yes."""
        other = await self._store.profile(matched)
        if other is None or self._unlock is None:
            return await self._propose(turn, desk, member, prefix="")
        payer = payer_answer(turn.text, turn.session.phone.e164)
        if payer is None:
            return say(texts.pay_or_not(), question=UNLOCK_KEY, voiced=False)
        if payer == "":
            return await self._close(turn, desk, texts.unlock_later())
        asked = await self._unlock.request(
            payer_user_id=member.user_id,
            target_user_id=other.user_id,
            purpose=DATING_PURPOSE,
            subject_id=other.profile_id,
            payer_phone=payer,
            now=turn.now,
        )
        if not asked:
            return say(texts.unlock_failed(), question=UNLOCK_KEY, voiced=False)
        return await self._close(turn, desk, texts.unlock_sent(payer))
