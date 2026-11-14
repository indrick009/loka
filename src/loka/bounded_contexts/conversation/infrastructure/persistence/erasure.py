"""Erase one user's conversation history, on their written request.

What goes: every message they sent and received, the turns read from them, the
decision-model readings, the outbox copies of the replies, and what the
sessions remembered of the conversation (facts, memory, open question). Their
lines in the conversation journal go too.

What stays, on purpose: a published listing, a dating profile, a reservation,
a payment. Those are records other people or the accounts rely on; the user
is told so and given the address to have them removed. The AI cost ledger
stays as well: it holds token counts, not words.

Not ``interfaces/http/demo_reset``: that one is for the two test numbers only
and removes everything, listings and payments included.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.ai.infrastructure.models import DecisionShadowRow
from loka.bounded_contexts.conversation.infrastructure.persistence.messaging_models import (
    ConversationSessionRow,
    ConversationTurnRow,
    InboundMessageRow,
    OutboundMessageRow,
)
from loka.shared.infrastructure.db.outbox import OutboxRecord
from loka.shared.infrastructure.logging import get_logger

_logger = get_logger(__name__)


async def erase_history(session: AsyncSession, *, phone: str, now: datetime) -> int:
    """Delete the history of ``phone``; the number of messages erased."""
    session_ids = list(
        (
            await session.execute(
                select(ConversationSessionRow.id).where(ConversationSessionRow.phone_e164 == phone)
            )
        ).scalars()
    )
    inbound = await session.execute(
        delete(InboundMessageRow).where(InboundMessageRow.sender_phone == phone)
    )
    outbound = await session.execute(
        delete(OutboundMessageRow).where(OutboundMessageRow.recipient_phone == phone)
    )
    if session_ids:
        await session.execute(
            delete(ConversationTurnRow).where(ConversationTurnRow.session_id.in_(session_ids))
        )
        await session.execute(
            delete(DecisionShadowRow).where(DecisionShadowRow.session_id.in_(session_ids))
        )
        # Published copies only: one still waiting is the reply being sent now.
        await session.execute(
            delete(OutboxRecord).where(
                OutboxRecord.aggregate_id.in_(session_ids), OutboxRecord.published_at.is_not(None)
            )
        )
        await session.execute(
            update(ConversationSessionRow)
            .where(ConversationSessionRow.id.in_(session_ids))
            .values(
                context_json={},
                flow="SUPPORT",
                step="START",
                assigned_property_id=None,
                # A turn that loaded the session before the erasure cannot save
                # the old facts back over it.
                revision=ConversationSessionRow.revision + 1,
                updated_at=now,
            )
        )
    return _count(inbound) + _count(outbound)


def _count(result: object) -> int:
    return int(getattr(result, "rowcount", 0) or 0)


def erase_journal(path: str, *, phone: str) -> None:
    """Drop the user's lines from the conversation journal; a journal off is nothing to do."""
    if not path:
        return
    target = Path(path)
    try:
        lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return
    kept = []
    for line in lines:
        try:
            if json.loads(line).get("phone") == phone:
                continue
        except ValueError:
            pass
        kept.append(line)
    try:
        tmp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
        tmp.write_text("".join(kept), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        _logger.warning("journal_erase_failed", error=type(exc).__name__)
