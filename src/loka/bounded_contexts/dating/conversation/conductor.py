"""The member's dating thread, conducted by the model (see ``domain/dating_agent``).

The model is shown the situation and chooses one move with its own words; the
moves are carried out here, by the same code as before — the answer recorded
against the person actually proposed (never a name guessed from the text), the
next person chosen by the matching, the yes path with its questions and its
notices. Nothing a member could be harmed by is left to the model.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from typing import TYPE_CHECKING

from loka.bounded_contexts.conversation.application.plugs.forms import say
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import FlowStep
from loka.bounded_contexts.dating.conversation import texts
from loka.bounded_contexts.dating.conversation.keys import LIKE_KEY, PROPOSED_KEY
from loka.bounded_contexts.dating.conversation.ranking import ranked_for
from loka.bounded_contexts.dating.domain.attributes import MUST, Wish
from loka.bounded_contexts.dating.domain.dating_agent import (
    DatingAgent,
    DatingMove,
    DatingSituation,
)
from loka.bounded_contexts.dating.domain.profile import Profile
from loka.shared.infrastructure.conversation_log import journal

if TYPE_CHECKING:
    from loka.bounded_contexts.dating.conversation.turns import DatingTurns

_LOOKING = {
    "QUARTIER": "son quartier",
    "VILLE": "sa ville",
    "PAYS": "tout le pays",
    "ETRANGER": "l'étranger",
    "PEU_IMPORTE": "partout",
}


def card(other: Profile) -> str:
    """What a member is shown of someone proposed, nothing more."""
    return f"{other.first_name}, {other.age} ans, {other.profession}, {other.city}"


class DatingConductor:
    def __init__(
        self, turns: DatingTurns, agent: DatingAgent, phones: frozenset[str] | None
    ) -> None:
        self._turns = turns
        self._agent = agent
        # Only these numbers while it is tried; everyone with None.
        self._phones = phones

    def conducts(self, phone: str) -> bool:
        return self._phones is None or phone in self._phones

    async def conduct(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile
    ) -> TurnOutcome | None:
        """``None`` when the model cannot be asked: the rules below decide."""
        proposed = await self._proposed(turn)
        situation = await self._situation(turn, member, proposed)
        move = await self._agent.move(situation)
        if move is None:
            return None
        journal("dating_move", str(member.user_id), move=move.move, liked=move.liked)
        return await self._carry_out(turn, desk, member, proposed, move)

    async def _proposed(self, turn: PlugTurn) -> Profile | None:
        raw = turn.session.context.get(PROPOSED_KEY)
        if turn.session.context.get("next_question") != LIKE_KEY or not isinstance(raw, str):
            return None
        return await self._turns._store.profile(uuid.UUID(raw))

    async def _situation(
        self, turn: PlugTurn, member: Profile, proposed: Profile | None
    ) -> DatingSituation:
        store = self._turns._store
        answers = [
            f"{card(other)} — {'oui' if liked else 'non'}" + (f" ({reason})" if reason else "")
            for other, liked, reason in await store.recent_answers(member.profile_id)
        ]
        ranked = await ranked_for(store, member, now=turn.now)
        left = (
            f"{len(ranked)} personne(s) compatible(s)"
            if ranked
            else "personne de compatible pour l'instant (critères, âge ou endroit où il cherche)"
        )
        thread_of = self._turns._talk._thread_of
        thread = await thread_of(turn.session.id) if thread_of else []
        text = " ".join((turn.text or "").split())
        if thread and thread[-1].speaker == "member" and thread[-1].text.strip() == text:
            thread = thread[:-1]
        return DatingSituation(
            member=member.first_name,
            profile=_profile(member),
            proposed=card(proposed) if proposed else None,
            answers=answers,
            standing=await self._turns._standing(member, turn.language),
            left=left,
            thread=thread,
            message=text,
        )

    async def _carry_out(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        proposed: Profile | None,
        move: DatingMove,
    ) -> TurnOutcome | None:
        turns, lang = self._turns, turn.language
        kind = move.move
        if kind in ("ANSWER_PROPOSAL", "NEXT") and proposed is not None:
            liked = move.liked is True if kind == "ANSWER_PROPOSAL" else False
            if liked:
                raw = turn.session.context.get("_score")
                score = float(raw) if isinstance(raw, int | float) else 0.0
                return await turns._decide(turn, desk, member, proposed, liked=True, score=score)
            return await self._no(turn, desk, member, proposed, move)
        if kind in ("ANSWER_PROPOSAL", "NEXT"):
            return await turns._propose(turn, desk, member, prefix=move.reply or "")
        if kind == "REASON" and move.reason:
            refused = next(
                (other for other, liked, _ in await turns._store.recent_answers(member.profile_id)
                 if not liked),
                None,
            )
            if refused is not None:
                member = await turns._learn_reason(member, refused, move.reason)
            prefix = move.reply or texts.reason_noted(lang)
            return await self._keep_or_propose(turn, desk, member, proposed, prefix)
        if kind == "SHOW_PHOTO" and proposed is not None and proposed.photo_key:
            return say(
                move.reply or texts.proposal(proposed, lang),
                question=LIKE_KEY,
                step=FlowStep.COLLECT_CHOICE,
                voiced=False,
                media=proposed.photo_key,
            )
        if kind == "SET_LOOKING" and move.looking:
            wanted = tuple(move.looking)
            await turns._store.update_looking(member.user_id, wanted)
            member = replace(member, looking=wanted)
            return await turns._propose(turn, desk, member, prefix=move.reply or "")
        if kind == "ADD_WISHES" and (move.wishes or move.age_min or move.age_max):
            member = await self._add_wishes(member, move)
            prefix = move.reply or texts.wishes_noted(lang)
            return await turns._propose(turn, desk, member, prefix=prefix)
        if kind == "WITHDRAW":
            return await turns._withdraw(turn, desk, member.user_id)
        if not move.reply:
            return None
        return await self._keep_or_propose(turn, desk, member, proposed, move.reply, ask=False)

    async def _no(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        proposed: Profile,
        move: DatingMove,
    ) -> TurnOutcome:
        """A no to the person proposed, with its reason when said; the next one."""
        turns = self._turns
        await turns._store.answer(
            from_profile=member.profile_id,
            to_profile=proposed.profile_id,
            liked=False,
            score=0.0,
            now=turn.now,
        )
        await turns._tell_no_follow_up(turn, desk, member, proposed)
        if move.reason:
            member = await turns._learn_reason(member, proposed, move.reason)
        prefix = move.reply or texts.passed(turn.language)
        return await turns._propose(turn, desk, member, prefix=prefix)

    async def _add_wishes(self, member: Profile, move: DatingMove) -> Profile:
        store = self._turns._store
        if move.wishes and move.wishes not in member.description:
            description = f"{member.description}\nCe que je recherche aussi : {move.wishes}"
            await store.update_description(member.user_id, description)
            member = await self._turns._read(replace(member, description=description))
        if move.age_min or move.age_max:
            low = move.age_min
            high = move.age_max if move.age_max != move.age_min else None
            await store.update_ages(member.user_id, low, high)
            member = replace(member, age_min=low, age_max=high)
        return member

    async def _keep_or_propose(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        proposed: Profile | None,
        text: str,
        *,
        ask: bool = True,
    ) -> TurnOutcome:
        """With a person proposed, the answer to them is still awaited; else the
        next person comes (``ask``) or idia just answers."""
        if proposed is not None:
            return say(text, question=LIKE_KEY, step=FlowStep.COLLECT_CHOICE, voiced=False)
        if ask:
            return await self._turns._propose(turn, desk, member, prefix=text)
        return await self._turns._close(turn, desk, text)


def _profile(member: Profile) -> str:
    ages = (
        f"{member.age_min or '?'} à {member.age_max or 'plus'} ans"
        if member.age_min or member.age_max
        else "pas dit"
    )
    where = ", ".join(_LOOKING.get(item, item) for item in member.looking) or "pas dit"
    wishes = "; ".join(_wish(wish) for wish in member.all_wishes())
    sought = "un homme" if member.sought_sex == "HOMME" else "une femme"
    return (
        f"{card(member)}, {member.sex.lower()}. Cherche {sought}, âge {ages}, où : {where}. "
        f"Critères : {wishes or 'aucun'}"
    )


def _wish(wish: Wish) -> str:
    what = " / ".join(wish.values) or f"{wish.low or ''}-{wish.high or ''}"
    absolute = " (absolu)" if wish.strength == MUST else ""
    return f"{wish.key} {'veut' if wish.wants else 'évite'} {what}{absolute}"
