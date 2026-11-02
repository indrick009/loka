"""Dating tables. Photo keys only: the images stay in private object storage."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class DatingProfileRow(Base):
    __tablename__ = "dating_profiles"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    first_name: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    age: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sex: Mapped[str] = mapped_column(String(8), nullable=False, default="")
    profession: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    country: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    city: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    photo_key: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    looking: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    seeking: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DatingAnswerRow(Base):
    __tablename__ = "dating_answers"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    from_profile: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    to_profile: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    liked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("from_profile", "to_profile", name="uq_dating_answer"),)


class DatingTalkRow(Base):
    """Two members asking each other questions through Kora before confirming."""

    __tablename__ = "dating_talks"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    first_profile: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    second_profile: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    # Per member: messages and photos sent, packs held, and the final yes.
    first_side: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    second_side: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
