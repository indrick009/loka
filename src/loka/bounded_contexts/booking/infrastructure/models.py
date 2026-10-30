"""Booking tables."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from loka.shared.infrastructure.db.base import Base


class BookingAgencyRow(Base):
    __tablename__ = "booking_agencies"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    key_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    commission_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BookingLineRow(Base):
    __tablename__ = "booking_lines"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    agency_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    origin: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    destination: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    bus_class: Mapped[str] = mapped_column(String(12), nullable=False)
    price_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    seats: Mapped[int] = mapped_column(Integer, nullable=False)
    layout: Mapped[str] = mapped_column(String(4), nullable=False)
    times: Mapped[list[str]] = mapped_column(JSONB, nullable=False)


class BookingReservationRow(Base):
    __tablename__ = "booking_reservations"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    line_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    agency_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    client_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    departs_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    seats: Mapped[list[int]] = mapped_column(JSONB, nullable=False)
    passengers: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    amount_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_xaf: Mapped[int] = mapped_column(Integer, nullable=False)
    reference: Mapped[str] = mapped_column(String(12), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    held_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payer_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
