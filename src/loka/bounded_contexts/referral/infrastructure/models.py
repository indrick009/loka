"""Referral tables: who owns which code, who came through whom, what was earned."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class ReferralCodeRow(Base):
    __tablename__ = "referral_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    code: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralRow(Base):
    """One referrer per referee, for life: the referee is the key."""

    __tablename__ = "referrals"

    referee_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    referrer_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    code: Mapped[str] = mapped_column(String(12), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralNudgeRow(Base):
    """A purchase after which the buyer was invited to share their link: once each."""

    __tablename__ = "referral_nudges"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReferralCommissionRow(Base):
    """10 % of one purchase; ``source`` names the purchase, so it is credited once."""

    __tablename__ = "referral_commissions"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    referrer_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    referee_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    amount_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
