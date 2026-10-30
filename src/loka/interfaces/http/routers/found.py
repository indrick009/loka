"""The Kora team's review of ownership claims (Found §7).

A person decides, never the platform: until a claim is validated here, its
owner learns nothing about the document beyond "a possible match exists".
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from loka.bounded_contexts.conversation.application.plugs.desk import ConversationDesk
from loka.bounded_contexts.conversation.application.plugs.turn import Steer
from loka.bounded_contexts.conversation.domain.entities.conversation_session import (
    FlowName,
    FlowStep,
)
from loka.bounded_contexts.conversation.infrastructure.messaging_composition import (
    CONVERSATION_SESSION_REPOSITORY,
)
from loka.bounded_contexts.found.conversation import texts
from loka.bounded_contexts.found.conversation.spec import FOUND
from loka.bounded_contexts.found.domain.documents import REJECTED, VALIDATED
from loka.bounded_contexts.found.infrastructure.store import SqlAlchemyFoundStore
from loka.interfaces.http.auth import Principal, require_admin
from loka.interfaces.http.container import get_container
from loka.shared.domain.clock import SystemClock
from loka.shared.domain.errors import ResourceNotFound
from loka.shared.infrastructure.composition import unit_of_work

router = APIRouter(prefix="/found", tags=["found"])
clock = SystemClock()


class Decision(BaseModel):
    validated: bool
    note: str | None = Field(default=None, max_length=2000)


@router.get("/claims", status_code=status.HTTP_200_OK)
async def claims_to_review(
    request: Request, principal: Annotated[Principal, Depends(require_admin)]
) -> list[dict[str, Any]]:
    async with unit_of_work(get_container(request).database) as uow:
        store = SqlAlchemyFoundStore(uow.session)
        found = []
        for claim in await store.claims_to_review():
            declaration = (
                await store.declaration(claim.declaration_id) if claim.declaration_id else None
            )
            found.append(
                {
                    "claim_id": str(claim.claim_id),
                    "status": claim.status,
                    "document_type": claim.document_type,
                    "full_name": claim.full_name,
                    "lost_on": claim.lost_on,
                    "description": claim.description,
                    "proofs": claim.proofs,
                    "declaration": None
                    if declaration is None
                    else {
                        "description": declaration.description,
                        "found_on": declaration.found_on,
                        "place": declaration.place,
                        "photos": declaration.photos,
                    },
                }
            )
    return found


@router.post("/claims/{claim_id}/decision", status_code=status.HTTP_200_OK)
async def decide(
    claim_id: uuid.UUID,
    body: Decision,
    request: Request,
    principal: Annotated[Principal, Depends(require_admin)],
) -> dict[str, str]:
    now = clock.now()
    async with unit_of_work(get_container(request).database) as uow:
        store = SqlAlchemyFoundStore(uow.session)
        claim = await store.decide(
            claim_id, status=VALIDATED if body.validated else REJECTED, note=body.note, now=now
        )
        if claim is None:
            raise ResourceNotFound("no such claim", context={"claim_id": str(claim_id)})
        desk = ConversationDesk(uow, uow.repository(CONVERSATION_SESSION_REPOSITORY))
        await desk.notify(
            claim.owner_user_id,
            texts.validated("fr") if body.validated else texts.refused("fr"),
            now=now,
            # Validated, his next message answers the unlock offer; refused,
            # it is read as more proof for the same claim.
            steer=Steer(
                flow=FlowName.FOUND_CLAIM,
                step=FlowStep.COLLECT_CHOICE,
                question_key=FOUND.question_key("unlock" if body.validated else "proofs"),
                context={"_claim": str(claim.claim_id)},
            ),
        )
        await uow.commit()
    return {"claim_id": str(claim_id), "status": claim.status}
