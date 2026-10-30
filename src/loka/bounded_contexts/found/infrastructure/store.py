"""SQLAlchemy implementation of ``FoundStore``."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from loka.bounded_contexts.found.domain.documents import (
    MATCHED,
    PENDING_REVIEW,
    SEARCHING,
    Claim,
    Declaration,
)
from loka.bounded_contexts.found.infrastructure.models import FoundClaimRow, FoundDeclarationRow


def _declaration(row: FoundDeclarationRow) -> Declaration:
    return Declaration(
        declaration_id=row.id,
        finder_user_id=row.finder_user_id,
        document_type=row.document_type,
        place=row.place,
        found_on=row.found_on,
        description=row.description,
        photos=list(row.photos or []),
        created_at=row.created_at,
    )


def _claim(row: FoundClaimRow) -> Claim:
    return Claim(
        claim_id=row.id,
        owner_user_id=row.owner_user_id,
        document_type=row.document_type,
        full_name=row.full_name,
        place=row.place,
        lost_on=row.lost_on,
        description=row.description,
        created_at=row.created_at,
        status=row.status,
        declaration_id=row.declaration_id,
        proofs=list(row.proofs or []),
    )


class SqlAlchemyFoundStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_declaration(self, declaration: Declaration) -> None:
        self._session.add(
            FoundDeclarationRow(
                id=declaration.declaration_id,
                finder_user_id=declaration.finder_user_id,
                document_type=declaration.document_type,
                place=declaration.place,
                found_on=declaration.found_on,
                description=declaration.description,
                photos=list(declaration.photos),
                created_at=declaration.created_at,
            )
        )
        await self._session.flush()

    async def declaration(self, declaration_id: uuid.UUID) -> Declaration | None:
        row = await self._session.get(FoundDeclarationRow, declaration_id)
        return _declaration(row) if row else None

    async def add_photo(self, declaration_id: uuid.UUID, key: str) -> None:
        row = await self._session.get(FoundDeclarationRow, declaration_id)
        if row is not None and key not in (row.photos or []):
            row.photos = [*(row.photos or []), key]
            await self._session.flush()

    async def open_declarations(self, document_type: str) -> list[Declaration]:
        rows = await self._session.execute(
            select(FoundDeclarationRow).where(FoundDeclarationRow.document_type == document_type)
        )
        return [_declaration(row) for row in rows.scalars()]

    async def save_claim(self, claim: Claim) -> None:
        row = await self._session.get(FoundClaimRow, claim.claim_id)
        if row is None:
            self._session.add(
                FoundClaimRow(
                    id=claim.claim_id,
                    owner_user_id=claim.owner_user_id,
                    document_type=claim.document_type,
                    full_name=claim.full_name,
                    place=claim.place,
                    lost_on=claim.lost_on,
                    description=claim.description,
                    proofs=list(claim.proofs),
                    declaration_id=claim.declaration_id,
                    status=claim.status,
                    created_at=claim.created_at,
                )
            )
        else:
            row.proofs = list(claim.proofs)
            row.declaration_id = claim.declaration_id
            row.status = claim.status
        await self._session.flush()

    async def claim(self, claim_id: uuid.UUID) -> Claim | None:
        row = await self._session.get(FoundClaimRow, claim_id)
        return _claim(row) if row else None

    async def searching_claims(self, document_type: str) -> list[Claim]:
        rows = await self._session.execute(
            select(FoundClaimRow).where(
                FoundClaimRow.document_type == document_type,
                FoundClaimRow.status == SEARCHING,
            )
        )
        return [_claim(row) for row in rows.scalars()]

    async def claims_today(self, owner_user_id: uuid.UUID, *, now: datetime) -> int:
        count = await self._session.scalar(
            select(func.count())
            .select_from(FoundClaimRow)
            .where(
                FoundClaimRow.owner_user_id == owner_user_id,
                FoundClaimRow.created_at >= now - timedelta(days=1),
            )
        )
        return int(count or 0)

    async def claims_to_review(self) -> list[Claim]:
        rows = await self._session.execute(
            select(FoundClaimRow)
            .where(FoundClaimRow.status.in_((PENDING_REVIEW, MATCHED)))
            .order_by(FoundClaimRow.created_at)
        )
        return [_claim(row) for row in rows.scalars()]

    async def decide(
        self, claim_id: uuid.UUID, *, status: str, note: str | None, now: datetime
    ) -> Claim | None:
        row = await self._session.get(FoundClaimRow, claim_id)
        if row is None:
            return None
        row.status, row.note, row.decided_at = status, note, now
        await self._session.flush()
        return _claim(row)
