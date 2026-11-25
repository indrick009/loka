"""What a Dating turn does: build the profile, propose, record a yes or a no.

Two yeses open a talk (see ``talk``): questions relayed by idia, then each
confirms. Only then is it a match, and the contact can be unlocked.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace

from loka.bounded_contexts.conversation.application.plugs.forms import ask_field, say, stored
from loka.bounded_contexts.conversation.application.plugs.turn import PlugDesk, PlugTurn, Steer
from loka.bounded_contexts.conversation.application.services.privacy import fold
from loka.bounded_contexts.conversation.application.use_cases.analyse_conversation_message.turn import (  # noqa: E501
    TurnOutcome,
)
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.dating.conversation import texts
from loka.bounded_contexts.dating.conversation.keys import (
    BACK_KEY,
    LIKE_KEY,
    MATCH_KEY,
    PLUS_KEY,
    PROPOSED_KEY,
    RELAY_KEY,
    TALK_ID,
    TALK_KEY,
    UNLOCK_KEY,
)
from loka.bounded_contexts.dating.conversation.spec import DATING, country_of
from loka.bounded_contexts.dating.conversation.talk import DatingTalk, ThreadOf
from loka.bounded_contexts.dating.domain.judge import SHORTLIST, MatchJudge
from loka.bounded_contexts.dating.domain.matching import appeal, best_candidates
from loka.bounded_contexts.dating.domain.photo_check import PhotoCheck
from loka.bounded_contexts.dating.domain.profile import ADULT_AGE, LOOKING, Profile
from loka.bounded_contexts.dating.domain.store import DatingStore
from loka.bounded_contexts.dating.domain.talk import CLOSED
from loka.bounded_contexts.dating.domain.talk_reader import TalkReader
from loka.bounded_contexts.payment.application.unlock_contact import UnlockContact, payer_answer
from loka.bounded_contexts.payment.domain.contact_unlock import DATING as DATING_PURPOSE

# Never taken from a passing remark: the description is asked with its privacy
# promise. Where to look is kept when said, and still asked after the
# description when it was not: « je cherche l'amour dans mon quartier » got
# « c'est noté ! » then the question again at the end (journal of 7 Nov 2026).
ASKED_ONLY = ("description",)
_YES = ("oui", "yes", "ok", "d'accord", "dac", "volontiers", "bien sûr", "carrément")
# « ça n'a pas marché », « c'est fini entre nous »: said straight away, no
# question needed.
_DID_NOT_WORK = re.compile(
    r"pas (marche|fonctionne|abouti)|c.?est fini|on a (rompu|arrete)|separe|"
    r"didn.?t work|not working|broke up"
)
_NO = ("non", "no", "pas intéress", "bof", "suivant")


PHOTO_KEY = DATING.question_key("photo")

# « montre un autre », « une autre personne », « suivant »: a pass, not a yes
# (journal of 25 Nov 2026, 07:28: « Montre un autre » then « Ok » became a yes).
# « Propose autre chose » too (10:38 the same day).
_NEXT = re.compile(
    r"\b(un|une|d) autres?\b|\bautres? (profils?|personnes?|chose)\b|\bsuivant|\bnext\b"
)


_PROFILE = re.compile(r"\bprofil\w*\b")
# « Supprime mon profil »: idia's model answered « je supprime ton profil »,
# then « oui, c'est supprimé », and nothing was done: the profile was still
# proposed (journal of 25 Nov 2026, 18:05, Al-harazi).
_ERASE = r"(supprim|effac|retir|desactiv|enlev|delete|remove)\w*"
_LEAVE = re.compile(rf"\b{_ERASE}\b.*\bprofil|\bprofil\w*\b.*\b{_ERASE}\b")


def _answer(turn: PlugTurn) -> bool | None:
    if _NEXT.search(fold(turn.text or "")):
        return False
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
    def __init__(
        self,
        store: DatingStore,
        *,
        unlock: UnlockContact | None = None,
        judge: MatchJudge | None = None,
        photos: PhotoCheck | None = None,
        reader: TalkReader | None = None,
        thread_of: ThreadOf | None = None,
    ) -> None:
        self._store = store
        self._unlock = unlock
        self._judge = judge
        self._photos = photos
        self._talk = DatingTalk(store, unlock=unlock, reader=reader, thread_of=thread_of)

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        user, lang = turn.session.user_id, turn.language
        if user is None or await self._store.refused_minor(user):
            return await self._close(turn, desk, texts.minor(lang))
        if _LEAVE.search(fold(turn.text or "")) and not fold(turn.text or "").startswith(
            "comment"
        ):
            return await self._withdraw(turn, desk, user)
        if await self._store.in_relation(user):
            return await self._relation(turn, desk, user)
        member = await self._store.profile_of(user)
        if member is None:
            return await self._register(turn, desk, user)
        question = turn.session.context.get("next_question")
        photo = turn.facts.get("photo")
        if not member.photo_key and not turn.session.context.get(TALK_ID):
            # No valid photo, no activation: the photo comes first.
            return await self._photo_first(turn, desk, member, photo)
        if isinstance(photo, str) and photo and question in (None, BACK_KEY):
            # A photo once the profile exists replaces the profile's: Jerry was
            # told he could, then nothing happened (journal of 24 Nov, 11:32).
            refused = await self._refusal(photo, turn.language, member.sex)
            if refused:
                return await self._close(turn, desk, refused)
            await self._store.update_photo(user, photo)
            return await self._close(turn, desk, texts.photo_updated(turn.language))
        if question in (None, BACK_KEY):
            again = await self._named_again(member, turn.text or "")
            if again is not None:
                # « Je vais essayer Dave »: a « non » taken back is a yes, said
                # plainly; asking OUI or NON again read as not listening
                # (journal of 25 Nov 2026, 07:56).
                return await self._decide(turn, desk, member, again, liked=True, score=0.0)
        looking = turn.facts.get("looking")
        if isinstance(looking, list) and looking and question in (None, BACK_KEY):
            # « peu importe » once the profile exists widens where to look.
            wanted = tuple(str(item) for item in looking if item in LOOKING)
            if wanted and wanted != member.looking:
                await self._store.update_looking(user, wanted)
                member = replace(member, looking=wanted)
                return await self._propose(turn, desk, member, prefix="")
        talk_id = turn.session.context.get(TALK_ID)
        in_talk = not turn.fresh and question in (TALK_KEY, PLUS_KEY, RELAY_KEY) and talk_id
        if in_talk and isinstance(photo, str) and photo and _PROFILE.search(fold(turn.text or "")):
            # « Je veux changer ma photo » mid-talk: the photo captioned
            # « profil » is hers, not one for the other (journal of 25 Nov
            # 2026, 12:48). The talk goes on.
            refused = await self._refusal(photo, turn.language, member.sex)
            if not refused:
                await self._store.update_photo(user, photo)
            return say(
                refused or texts.photo_updated(turn.language),
                question=str(question),
                step=FlowStep.COLLECT_CHOICE,
                voiced=False,
            )
        if in_talk:
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

    async def _withdraw(self, turn: PlugTurn, desk: PlugDesk, user: uuid.UUID) -> TurnOutcome:
        member = await self._store.named(user)
        if member is not None:
            talk = await self._store.open_talk_of(member.profile_id)
            if talk is not None:
                talk.status = CLOSED
                await self._store.save_talk(talk)
            await self._store.withdraw(user)
        return await self._close(turn, desk, texts.withdrawn(turn.language))

    async def _photo_first(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, photo: object
    ) -> TurnOutcome:
        """A profile whose photo was refused or removed: activated by a valid one."""
        lang = turn.language
        if not isinstance(photo, str) or not photo:
            await desk.settle(
                turn, step=FlowStep.COLLECT_FIELD, question_key=PHOTO_KEY, context={}
            )
            return say(texts.photo_needed(lang), question=PHOTO_KEY, voiced=False)
        refused = await self._refusal(photo, lang, member.sex)
        if refused:
            await desk.settle(
                turn, step=FlowStep.COLLECT_FIELD, question_key=PHOTO_KEY, context={}
            )
            return say(refused, question=PHOTO_KEY, voiced=False)
        await self._store.update_photo(member.user_id, photo)
        member = replace(member, photo_key=photo)
        return await self._propose(turn, desk, member, prefix=texts.photo_updated(lang))

    async def _relation(self, turn: PlugTurn, desk: PlugDesk, user: uuid.UUID) -> TurnOutcome:
        """Contacts exchanged: proposed to nobody until the member says it did not work.

        Each one says it for themself: the other stays in the relation until they
        write too.
        """
        lang = turn.language
        asked = not turn.fresh and turn.session.context.get("next_question") == BACK_KEY
        answer = _answer(turn) if asked else None
        if answer is False:
            return await self._close(turn, desk, texts.stay_in_relation(lang))
        if answer or _DID_NOT_WORK.search(fold(turn.text or "")):
            await self._store.back_on_market(user)
            member = await self._store.profile_of(user)
            if member is not None:
                return await self._propose(turn, desk, member, prefix=texts.back_on_market(lang))
        await desk.settle(turn, step=FlowStep.COLLECT_CHOICE, question_key=BACK_KEY, context={})
        return say(
            texts.in_relation(lang), question=BACK_KEY, step=FlowStep.COLLECT_CHOICE, voiced=False
        )

    async def _close(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        text: str,
        context: dict[str, object] | None = None,
    ) -> TurnOutcome:
        await desk.settle(
            turn, step=FlowStep.PUBLISHED, question_key=None, context=context or {}
        )
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
            # What was already given stays, and what is added to it completes
            # it. Dropped whole, a member's red flags sent after his description
            # erased the description too, and he was asked for it again
            # (journal of 22 Nov 2026, 21:25 UTC).
            for name in unasked:
                before, added = turn.session.context.get(name), turn.facts.get(name)
                if isinstance(before, str) and before.strip():
                    extra = added.strip() if isinstance(added, str) else ""
                    known[name] = f"{before}\n{extra}" if extra and extra not in before else before
        if known.get("city") and not known.get("country"):
            # Never asked: a Cameroonian city already set it, else the number does.
            known["country"] = country_of(turn.session.phone.e164)
        age = known.get("age")
        if isinstance(age, int) and age < ADULT_AGE:
            # Refused, not hidden: nothing about the person is kept.
            await self._store.refuse_minor(user, now=turn.now)
            return await self._close(turn, desk, texts.minor(lang))
        new_photo = turn.facts.get("photo")
        refused = (
            await self._refusal(new_photo, lang, known.get("sex"))
            if isinstance(new_photo, str)
            else ""
        )
        if refused:
            known = {name: value for name, value in known.items() if name != "photo"}
        missing = DATING.missing(turn.flow, known)
        if missing:
            field = missing[0]
            prefix = refused or (
                texts.inappropriate(lang)
                if turn.rejected.get("description") == "inappropriate"
                else ""
            )
            key = DATING.question_key(field.name)
            await desk.settle(
                turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=stored(known)
            )
            return ask_field(turn, DATING, field, prefix=prefix)
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
        # Nor anyone who already said no to them: Dave was shown Ornela, who had
        # passed on him, and his yes met « n'a pas donné suite » at once
        # (journal of 25 Nov 2026, 10:26).
        excluded = await self._store.answered(member.profile_id) | await self._store.passed_by(
            member.profile_id
        )
        refusals = await self._store.refusals()
        ranked = best_candidates(
            member, await self._store.members(), excluded=excluded, refusals=refusals
        )
        if not ranked:
            state = await self._standing(member, lang)
            raw = turn.session.context.get("_quiet")
            # Read even on a fresh turn: « montre-moi d'autres profils » opens the
            # flow anew each time, and a counter reset there sent the same
            # sentence three times (journal of 24 Nov 2026, 13:14 to 13:17).
            quiet = raw if isinstance(raw, int) else 0
            anywhere = replace(member, looking=("PEU_IMPORTE",))
            narrow = "PEU_IMPORTE" not in member.looking and bool(
                best_candidates(anywhere, await self._store.members(), excluded=excluded)
            )
            nobody = texts.nobody_now(
                state, quiet, lang, seen_any=bool(excluded), narrow=narrow
            )
            text = f"{prefix}\n{nobody}".strip()
            return await self._close(
                turn,
                desk,
                text,
                context={"rencontres": state or "aucune demande en cours", "_quiet": quiet + 1},
            )
        other, score, why = await self._best(member, ranked, refusals)
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_CHOICE,
            question_key=LIKE_KEY,
            context={PROPOSED_KEY: str(other.profile_id), "_score": round(score, 3), "_why": why},
        )
        text = f"{prefix}\n{texts.proposal(other, lang)}".strip()
        return say(
            text,
            question=LIKE_KEY,
            step=FlowStep.COLLECT_CHOICE,
            voiced=False,
            media=other.photo_key or None,
        )

    async def _refusal(self, photo: str | None, language: str, sex: object = None) -> str:
        """Why this photo cannot be the profile's, or ``""`` when it can."""
        if not photo or self._photos is None:
            return ""
        verdict = await self._photos.check(photo, sex=sex if isinstance(sex, str) else None)
        return "" if verdict.ok else texts.photo_refused(verdict.reason, language)

    async def _named_again(self, member: Profile, text: str) -> Profile | None:
        """A profile this member passed on, named in the message."""
        words = set(fold(text).split())
        for other in await self._store.passed_on(member.profile_id):
            if fold(other.first_name) in words:
                return other
        return None

    async def _standing(self, member: Profile, language: str) -> str:
        """The member's yeses: those awaiting an answer, those not followed up."""
        waiting: list[str] = []
        silent: list[str] = []
        for other in await self._store.said_yes_to(member.profile_id):
            answer = await self._store.likes(other.profile_id, member.profile_id)
            if answer is None:
                waiting.append(other.first_name)
            elif answer is False:
                silent.append(other.first_name)
        return texts.standing(waiting, silent, language)

    async def _best(
        self,
        member: Profile,
        ranked: list[tuple[Profile, float]],
        refusals: dict[uuid.UUID, tuple[int, int]],
    ) -> tuple[Profile, float, str]:
        """The model's pick among the best few, read both ways; else the first.

        The model's score is weighed by how often each profile was turned
        down, as the first ranking is.
        """
        shortlist = [profile for profile, _ in ranked[:SHORTLIST]]
        if self._judge is None or len(shortlist) < 2:
            return ranked[0][0], ranked[0][1], ""
        verdicts = await self._judge.rank(member, shortlist)
        if not verdicts:
            return ranked[0][0], ranked[0][1], ""
        weighed = sorted(
            (
                (v.score * appeal(*refusals.get(v.profile_id, (0, 0))), v)
                for v in verdicts
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        score, verdict = weighed[0]
        best = next(p for p in shortlist if p.profile_id == verdict.profile_id)
        return best, score, verdict.reason

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
        return await self._decide(turn, desk, member, other, liked=answer, score=score)

    async def _decide(
        self,
        turn: PlugTurn,
        desk: PlugDesk,
        member: Profile,
        other: Profile,
        *,
        liked: bool,
        score: float,
    ) -> TurnOutcome:
        """Record a yes or a no to ``other``, then what follows from it."""
        lang = turn.language
        answer = liked
        await self._store.answer(
            from_profile=member.profile_id,
            to_profile=other.profile_id,
            liked=answer,
            score=score,
            now=turn.now,
        )
        if not answer:
            if (
                await self._store.likes(other.profile_id, member.profile_id)
                and await self._store.open_talk_of(other.profile_id) is None
                and not await desk.busy(other.user_id, now=turn.now)
            ):
                # Never into a talk under way: « écris rencontre » sent to Dave
                # mid-talk had his next two messages relayed to Margaret
                # (journal of 25 Nov 2026, 08:37). He hears it afterwards.
                # They said yes and wait: told now, never left with a yes that
                # gets no answer (decision of 25 Nov 2026), never « refused ».
                await desk.notify(other.user_id, texts.no_follow_up(member), now=turn.now)
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
        if (
            their_answer is None
            and await self._store.open_talk_of(other.profile_id) is None
            and not await desk.busy(other.user_id, now=turn.now)
        ):
            # Not while they talk with someone else, nor while they answer
            # anything else: the proposal would take their thread away. It
            # reaches them when they next ask for profiles.
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
        # One person at a time: after a yes, idia waits for theirs. Proposing the
        # next profile in the same breath read as a loop (journal of 16 Nov
        # 2026, 09:23: « Oui » to Estelle answered with Aïcha). Another profile
        # comes when the member asks for one.
        state = await self._standing(member, lang)
        return await self._close(
            turn, desk, texts.liked(lang), context={"rencontres": state} if state else None
        )

    async def _pay(
        self, turn: PlugTurn, desk: PlugDesk, member: Profile, matched: uuid.UUID
    ) -> TurnOutcome:
        """The 1 000 FCFA unlock, only ever offered after both said yes."""
        other = await self._store.profile(matched)
        if other is None or self._unlock is None:
            return await self._propose(turn, desk, member, prefix="")
        if member.sex == "FEMME":
            # Only the man unlocks for now: she is never asked to pay.
            return await self._close(turn, desk, texts.match_waiting(other, turn.language))
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
