"""Referral persistence, and the one read of what members bought.

The purchases are read from the payment and booking tables as they stand:
referral never changes how anyone pays, it only looks at what was settled.
On a bus ticket it is idia's commission that counts, never the fare the
agency keeps.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.referral.domain.referral import commission, new_code
from loka.bounded_contexts.referral.infrastructure.models import (
    ReferralCodeRow,
    ReferralCommissionRow,
    ReferralRow,
)

_PURCHASES = """
    select 'fee:' || p.id::text as source, p.tenant_id as buyer, p.amount_xaf as amount
      from service_fee_payments p where p.status = 'SUCCEEDED'
    union all
    select 'unlock:' || u.id::text, u.payer_user_id, u.amount_xaf
      from contact_unlocks u where u.status = 'PAID'
    union all
    select 'booking:' || b.id::text, b.client_user_id, b.commission_xaf
      from booking_reservations b where b.status = 'PAID'
"""


@dataclass(frozen=True, slots=True)
class Credit:
    referrer_user_id: uuid.UUID
    commission_xaf: int


class SqlAlchemyReferralStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def has_bought(self, user_id: uuid.UUID) -> bool:
        found = await self._session.scalar(
            text(f"select 1 from ({_PURCHASES}) x where x.buyer = :user limit 1"),
            {"user": user_id},
        )
        return found is not None

    async def code_of(self, user_id: uuid.UUID, *, now: datetime) -> str:
        row = await self._session.get(ReferralCodeRow, user_id)
        if row is not None:
            return row.code
        code = new_code()
        while await self.owner_of(code) is not None:
            code = new_code()
        self._session.add(ReferralCodeRow(user_id=user_id, code=code, created_at=now))
        await self._session.flush()
        return code

    async def owner_of(self, code: str) -> uuid.UUID | None:
        return await self._session.scalar(
            select(ReferralCodeRow.user_id).where(ReferralCodeRow.code == code)
        )

    async def attribute(
        self, *, referee: uuid.UUID, code: str, now: datetime
    ) -> uuid.UUID | None:
        """Record who brought ``referee``; the referrer, or ``None`` when refused."""
        referrer = await self.owner_of(code)
        if referrer is None or referrer == referee:
            return None
        if await self._session.get(ReferralRow, referee) is not None:
            return None
        self._session.add(
            ReferralRow(
                referee_user_id=referee, referrer_user_id=referrer, code=code, created_at=now
            )
        )
        await self._session.flush()
        return referrer

    async def standing(self, user_id: uuid.UUID) -> tuple[int, int]:
        """(referees, credit earned in FCFA)."""
        referees = await self._session.scalar(
            select(func.count()).select_from(ReferralRow).where(
                ReferralRow.referrer_user_id == user_id
            )
        )
        earned = await self._session.scalar(
            select(func.coalesce(func.sum(ReferralCommissionRow.commission_xaf), 0)).where(
                ReferralCommissionRow.referrer_user_id == user_id
            )
        )
        return int(referees or 0), int(earned or 0)

    async def credit_new_purchases(self, *, now: datetime) -> list[Credit]:
        """Credit every settled purchase of a referee not credited yet; once each."""
        rows = (
            await self._session.execute(
                text(
                    f"select x.source, x.buyer, x.amount, r.referrer_user_id "
                    f"from ({_PURCHASES}) x "
                    f"join referrals r on r.referee_user_id = x.buyer "
                    f"left join referral_commissions c on c.source = x.source "
                    f"where c.source is null"
                )
            )
        ).all()
        credits = []
        for source, buyer, amount, referrer in rows:
            earned = commission(int(amount or 0))
            if earned <= 0:
                continue
            self._session.add(
                ReferralCommissionRow(
                    source=source,
                    referrer_user_id=referrer,
                    referee_user_id=buyer,
                    amount_xaf=int(amount),
                    commission_xaf=earned,
                    created_at=now,
                )
            )
            credits.append(Credit(referrer_user_id=referrer, commission_xaf=earned))
        await self._session.flush()
        return credits
