"""Booking: bus agencies, their lines, and the seats travellers reserve.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b0c1d2e3f4a5"
down_revision: str | None = "a9b0c1d2e3f4"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)
_WHEN = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "booking_agencies",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("phone", sa.String(20), nullable=False, unique=True),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("commission_xaf", sa.Integer(), nullable=False),
        sa.Column("created_at", _WHEN, nullable=False),
    )
    op.create_table(
        "booking_lines",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("agency_id", _UUID, nullable=False, index=True),
        sa.Column("origin", sa.String(60), nullable=False, index=True),
        sa.Column("destination", sa.String(60), nullable=False, index=True),
        sa.Column("bus_class", sa.String(12), nullable=False),
        sa.Column("price_xaf", sa.Integer(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False),
        sa.Column("layout", sa.String(4), nullable=False),
        sa.Column("times", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "booking_reservations",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("line_id", _UUID, nullable=False, index=True),
        sa.Column("agency_id", _UUID, nullable=False, index=True),
        sa.Column("client_user_id", _UUID, nullable=False),
        sa.Column("departs_at", _WHEN, nullable=False),
        sa.Column("seats", postgresql.JSONB(), nullable=False),
        sa.Column("passengers", postgresql.JSONB(), nullable=False),
        sa.Column("amount_xaf", sa.Integer(), nullable=False),
        sa.Column("commission_xaf", sa.Integer(), nullable=False),
        sa.Column("reference", sa.String(12), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("held_until", _WHEN, nullable=False),
        sa.Column("payer_phone", sa.String(20), nullable=True),
        sa.Column("payment_reference", sa.String(120), nullable=True),
        sa.Column("created_at", _WHEN, nullable=False),
    )


def downgrade() -> None:
    for table in ("booking_reservations", "booking_lines", "booking_agencies"):
        op.drop_table(table)
