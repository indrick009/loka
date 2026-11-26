"""The the fee that buys a way to reach someone, outside housing.

Dating unlocks only after a reciprocal match; Found only after the idia team
validated ownership. Consent, or proof, always precedes the payment: this
record is created by the domain that checked it, never on request alone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from loka.shared.domain.pricing import DATING_PACK_XAF, DATING_UNLOCK_XAF, FOUND_UNLOCK_XAF

PENDING = "PENDING"
PAID = "PAID"
FAILED = "FAILED"

DATING = "DATING"
FOUND = "FOUND"
# Not a contact: 10 more messages and 5 more photos relayed by idia in a Dating
# talk, bought by a member who used his own. ``subject_id`` is the talk.
DATING_PLUS = "DATING_PLUS"


def fee_for(purpose: str) -> int:
    """What each kind of unlock costs (see ``pricing``)."""
    return {DATING: DATING_UNLOCK_XAF, FOUND: FOUND_UNLOCK_XAF, DATING_PLUS: DATING_PACK_XAF}.get(
        purpose, DATING_UNLOCK_XAF
    )


@dataclass(slots=True)
class ContactUnlock:
    unlock_id: uuid.UUID
    payer_user_id: uuid.UUID
    target_user_id: uuid.UUID
    purpose: str
    subject_id: uuid.UUID
    amount_xaf: int
    payer_phone: str
    payment_reference: str
    created_at: datetime
    status: str = PENDING

    @classmethod
    def requested(
        cls,
        *,
        payer_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
        purpose: str,
        subject_id: uuid.UUID,
        payer_phone: str,
        payment_reference: str,
        now: datetime,
    ) -> ContactUnlock:
        return cls(
            unlock_id=uuid.uuid4(),
            payer_user_id=payer_user_id,
            target_user_id=target_user_id,
            purpose=purpose,
            subject_id=subject_id,
            amount_xaf=fee_for(purpose),
            payer_phone=payer_phone,
            payment_reference=payment_reference,
            created_at=now,
        )
