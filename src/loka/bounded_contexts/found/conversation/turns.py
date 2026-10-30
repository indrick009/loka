"""What a Found turn does: declare, search, collect proof — never reveal."""

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
from loka.bounded_contexts.found.conversation import texts
from loka.bounded_contexts.found.conversation.spec import FOUND
from loka.bounded_contexts.found.domain.documents import (
    MAX_CLAIMS_PER_DAY,
    Claim,
    Declaration,
    could_be,
)
from loka.bounded_contexts.found.domain.store import FoundStore

DECLARATION_KEY = "_declaration"
CLAIM_KEY = "_claim"
PROOFS_KEY = FOUND.question_key("proofs")


class FoundTurns:
    def __init__(self, store: FoundStore) -> None:
        self._store = store

    async def apply(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome:
        if turn.session.user_id is None:
            return say(texts.nothing_yet(turn.language))
        if turn.flow is FlowName.FOUND_DECLARE:
            return await self._declare(turn, desk, turn.session.user_id)
        return await self._claim(turn, desk, turn.session.user_id)

    async def _ask(self, turn: PlugTurn, desk: PlugDesk) -> TurnOutcome | None:
        missing = FOUND.missing(turn.flow, turn.known)
        if not missing:
            return None
        field = missing[0]
        key = FOUND.question_key(field.name)
        await desk.settle(
            turn, step=FlowStep.COLLECT_FIELD, question_key=key, context=stored(turn.known)
        )
        return say(field.ask(turn.language), question=key, step=FlowStep.COLLECT_FIELD)

    async def _declare(self, turn: PlugTurn, desk: PlugDesk, finder: uuid.UUID) -> TurnOutcome:
        lang = turn.language
        recorded = turn.session.context.get(DECLARATION_KEY)
        if recorded and not turn.fresh:
            if turn.facts.get("photo"):
                await self._store.add_photo(uuid.UUID(str(recorded)), str(turn.facts["photo"]))
                return say(texts.photo_added(lang), voiced=False)
            return say(texts.declared(lang))
        asked = await self._ask(turn, desk)
        if asked is not None:
            return asked
        known = turn.known
        declaration = Declaration(
            declaration_id=uuid.uuid4(),
            finder_user_id=finder,
            document_type=str(known["document_type"]),
            place=known.get("place"),
            found_on=str(known["found_on"]),
            # The name read on the document is what an owner's search is
            # compared with, so it is kept with the description.
            description=" — ".join(
                part
                for part in (str(known["description"]), str(known.get("full_name") or ""))
                if part
            ),
            photos=[str(known["photo"])],
            created_at=turn.now,
        )
        await self._store.add_declaration(declaration)
        for claim in await self._store.searching_claims(declaration.document_type):
            if claim.owner_user_id != finder and could_be(claim, declaration):
                claim.matched(declaration)
                await self._store.save_claim(claim)
                await desk.notify(
                    claim.owner_user_id,
                    texts.possible_match_later("fr"),
                    now=turn.now,
                    steer=Steer(
                        flow=FlowName.FOUND_CLAIM,
                        step=FlowStep.COLLECT_FIELD,
                        question_key=PROOFS_KEY,
                        context={CLAIM_KEY: str(claim.claim_id)},
                    ),
                )
        await desk.settle(
            turn,
            step=FlowStep.PUBLISHED,
            question_key=None,
            context={DECLARATION_KEY: str(declaration.declaration_id)},
        )
        return say(texts.declared(lang), step=FlowStep.PUBLISHED)

    async def _claim(self, turn: PlugTurn, desk: PlugDesk, owner: uuid.UUID) -> TurnOutcome:
        lang = turn.language
        open_claim = turn.session.context.get(CLAIM_KEY)
        if open_claim and not turn.fresh:
            return await self._proof(turn, desk, uuid.UUID(str(open_claim)))
        if turn.fresh and await self._store.claims_today(owner, now=turn.now) >= MAX_CLAIMS_PER_DAY:
            await desk.settle(turn, step=FlowStep.PUBLISHED, question_key=None, context={})
            return say(texts.too_many_searches(lang), step=FlowStep.PUBLISHED)
        asked = await self._ask(turn, desk)
        if asked is not None:
            return asked
        known = turn.known
        claim = Claim(
            claim_id=uuid.uuid4(),
            owner_user_id=owner,
            document_type=str(known["document_type"]),
            full_name=str(known["full_name"]),
            place=known.get("place"),
            lost_on=str(known["lost_on"]),
            description=str(known["description"]),
            created_at=turn.now,
        )
        match = next(
            (
                declaration
                for declaration in await self._store.open_declarations(claim.document_type)
                if declaration.finder_user_id != owner and could_be(claim, declaration)
            ),
            None,
        )
        if match is not None:
            claim.matched(match)
        await self._store.save_claim(claim)
        if match is None:
            await desk.settle(
                turn,
                step=FlowStep.PUBLISHED,
                question_key=None,
                context={CLAIM_KEY: str(claim.claim_id)},
            )
            return say(texts.nothing_yet(lang), step=FlowStep.PUBLISHED)
        await desk.settle(
            turn,
            step=FlowStep.COLLECT_FIELD,
            question_key=PROOFS_KEY,
            context={CLAIM_KEY: str(claim.claim_id)},
        )
        return say(texts.possible_match(lang), question=PROOFS_KEY, step=FlowStep.COLLECT_FIELD)

    async def _proof(self, turn: PlugTurn, desk: PlugDesk, claim_id: uuid.UUID) -> TurnOutcome:
        lang = turn.language
        claim = await self._store.claim(claim_id)
        if claim is None or claim.declaration_id is None:
            return say(texts.nothing_yet(lang))
        if turn.facts.get("photo"):
            claim.add_proof({"kind": "photo", "value": str(turn.facts["photo"])})
        elif turn.facts.get("proof_text") or turn.text:
            claim.add_proof(
                {"kind": "text", "value": str(turn.facts.get("proof_text") or turn.text)}
            )
        else:
            return say(texts.possible_match(lang), question=PROOFS_KEY)
        await self._store.save_claim(claim)
        await desk.settle(
            turn,
            step=FlowStep.AWAITING_REVIEW,
            question_key=PROOFS_KEY,
            context={CLAIM_KEY: str(claim.claim_id)},
        )
        return say(texts.proof_received(lang), question=PROOFS_KEY, step=FlowStep.AWAITING_REVIEW)
