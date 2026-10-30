"""What a Dating turn does: build the profile, propose, record a yes or a no."""

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
from loka.bounded_contexts.dating.conversation.spec import DATING
from loka.bounded_contexts.dating.domain.matching import best_candidates
from loka.bounded_contexts.dating.domain.profile import ADULT_AGE, Profile
from loka.bounded_contexts.dating.domain.store import DatingStore

PROPOSED_KEY = "_proposed"
LIKE_KEY = DATING.question_key("like")
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
    def __init__(self, store: DatingStore) -> None:
        self._store = store

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        user, lang = turn.session.user_id, turn.language
        if user is None or await self._store.refused_minor(user):
            return await self._close(turn, desk, texts.minor(lang))
        member = await self._store.profile_of(user)
        if member is None:
            return await self._register(turn, desk, user)
        proposed = turn.session.context.get(PROPOSED_KEY)
        if not turn.fresh and turn.session.context.get("next_question") == LIKE_KEY and proposed:
            return await self._answer(turn, desk, member, uuid.UUID(str(proposed)))
        return await self._propose(turn, desk, member, prefix="")

    async def _close(self, turn: PlugTurn, desk: PlugDesk, text: str) -> TurnOutcome:
        await desk.settle(turn, step=FlowStep.PUBLISHED, question_key=None, context={})
        return say(text, step=FlowStep.PUBLISHED)

    async def _register(self, turn: PlugTurn, desk: PlugDesk, user: uuid.UUID) -> TurnOutcome:
        known, lang = turn.known, turn.language
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
            return say(text, question=key, step=FlowStep.COLLECT_FIELD)
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
        return say(text, question=LIKE_KEY, step=FlowStep.COLLECT_CHOICE, voiced=False)

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
            # Consent on both sides: the only moment a match exists.
            await desk.notify(other.user_id, texts.match(member, "fr"), now=turn.now)
            return await self._close(turn, desk, texts.match(other, lang))
        if their_answer is None:
            await desk.notify(
                other.user_id,
                texts.proposal(member, "fr"),
                now=turn.now,
                steer=Steer(
                    flow=FlowName.DATING_PROFILE,
                    step=FlowStep.COLLECT_CHOICE,
                    question_key=LIKE_KEY,
                    context={PROPOSED_KEY: str(member.profile_id), "_score": score},
                ),
            )
        return await self._propose(turn, desk, member, prefix=texts.liked(lang))
