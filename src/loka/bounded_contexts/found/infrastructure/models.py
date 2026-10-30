"""Found tables. Document photos are object keys in private storage, never URLs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class FoundDeclarationRow(Base):
    __tablename__ = "found_declarations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    finder_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    place: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    found_on: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    photos: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FoundClaimRow(Base):
    __tablename__ = "found_claims"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    place: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    lost_on: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    proofs: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False, default=list)
    declaration_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
