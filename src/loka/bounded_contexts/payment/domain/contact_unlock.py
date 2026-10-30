"""The 1 000 FCFA that buys a way to reach someone, outside housing.

Dating unlocks only after a reciprocal match; Found only after the Kora team
validated ownership. Consent, or proof, always precedes the payment: this
record is created by the domain that checked it, never on request alone.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

UNLOCK_FEE_XAF = 1000

PENDING = "PENDING"
PAID = "PAID"
FAILED = "FAILED"

DATING = "DATING"
FOUND = "FOUND"


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
            amount_xaf=UNLOCK_FEE_XAF,
            payer_phone=payer_phone,
            payment_reference=payment_reference,
            created_at=now,
        )
