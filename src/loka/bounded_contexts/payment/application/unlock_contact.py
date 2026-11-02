"""Ask a payer's phone for the 1 000 FCFA that unlocks someone's contact."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Protocol

from loka.bounded_contexts.payment.domain.contact_unlock import (
    DATING_PLUS,
    UNLOCK_FEE_XAF,
    ContactUnlock,
)

_NO = ("non", "no", "pas maintenant", "plus tard", "annule")
_YES = ("payer", "paye", "oui", "yes", "ok", "d'accord", "dac", "vas-y", "go")


class MobileMoney(Protocol):
    async def request(
        self, *, reservation_id: uuid.UUID, amount_xaf: int, payer_phone: str, label: str
    ) -> str | None: ...

    async def status(self, reference: str) -> str | None: ...


class UnlockStore(Protocol):
    async def save(self, unlock: ContactUnlock) -> None: ...


def payer_answer(text: str | None, own_phone: str) -> str | None:
    """The number to charge: his own on a yes, the one he typed, ``""`` for no,
    ``None`` when he said neither."""
    folded = (text or "").strip().casefold()
    digits = re.sub(r"\D", "", folded)
    if len(digits) >= 9:
        return f"+237{digits[-9:]}"
    if folded.startswith(_NO):
        return ""
    if folded.startswith(_YES):
        return own_phone
    return None


class UnlockContact:
    def __init__(self, payments: MobileMoney, store: UnlockStore) -> None:
        self._payments = payments
        self._store = store

    async def request(
        self,
        *,
        payer_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        purpose: str,
        subject_id: uuid.UUID,
        payer_phone: str,
        now: datetime,
    ) -> bool:
        unlock_id = uuid.uuid4()
        reference = await self._payments.request(
            reservation_id=unlock_id,
            amount_xaf=UNLOCK_FEE_XAF,
            payer_phone=payer_phone,
            label=(
                "Kora - questions supplementaires"
                if purpose == DATING_PLUS
                else f"Kora - mise en relation {purpose.lower()}"
            ),
        )
        if reference is None:
            return False
        unlock = ContactUnlock.requested(
            payer_user_id=payer_user_id,
            target_user_id=target_user_id,
            purpose=purpose,
            subject_id=subject_id,
            payer_phone=payer_phone,
            payment_reference=reference,
            now=now,
        )
        unlock.unlock_id = unlock_id
        await self._store.save(unlock)
        return True
