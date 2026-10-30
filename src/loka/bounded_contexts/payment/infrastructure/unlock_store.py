"""Contact unlocks: their table and its store."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from loka.bounded_contexts.payment.domain.contact_unlock import PAID, PENDING, ContactUnlock
from loka.shared.infrastructure.db.base import Base


class ContactUnlockRow(Base):
    __tablename__ = "contact_unlocks"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    payer_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    target_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    purpose: Mapped[str] = mapped_column(String(12), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    amount_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    payer_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    payment_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def _unlock(row: ContactUnlockRow) -> ContactUnlock:
    return ContactUnlock(
        unlock_id=row.id,
        payer_user_id=row.payer_user_id,
        target_user_id=row.target_user_id,
        purpose=row.purpose,
        subject_id=row.subject_id,
        amount_xaf=row.amount_xaf,
        payer_phone=row.payer_phone,
        payment_reference=row.payment_reference,
        created_at=row.created_at,
        status=row.status,
    )


class SqlAlchemyUnlockStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save(self, unlock: ContactUnlock) -> None:
        row = await self._session.get(ContactUnlockRow, unlock.unlock_id)
        if row is None:
            self._session.add(
                ContactUnlockRow(
                    id=unlock.unlock_id,
                    payer_user_id=unlock.payer_user_id,
                    target_user_id=unlock.target_user_id,
                    purpose=unlock.purpose,
                    subject_id=unlock.subject_id,
                    amount_xaf=unlock.amount_xaf,
                    payer_phone=unlock.payer_phone,
                    payment_reference=unlock.payment_reference,
                    status=unlock.status,
                    created_at=unlock.created_at,
                )
            )
        else:
            row.status = unlock.status
        await self._session.flush()

    async def pending(self) -> list[ContactUnlock]:
        rows = await self._session.execute(
            select(ContactUnlockRow).where(ContactUnlockRow.status == PENDING)
        )
        return [_unlock(row) for row in rows.scalars()]

    async def already_paid(
        self, payer_user_id: uuid.UUID, target_user_id: uuid.UUID, purpose: str
    ) -> bool:
        found = await self._session.scalar(
            select(ContactUnlockRow.id).where(
                ContactUnlockRow.payer_user_id == payer_user_id,
                ContactUnlockRow.target_user_id == target_user_id,
                ContactUnlockRow.purpose == purpose,
                ContactUnlockRow.status == PAID,
            )
        )
        return found is not None
