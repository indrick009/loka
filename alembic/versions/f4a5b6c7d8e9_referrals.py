"""Referral: codes, who came through whom, and the 10 % credited per purchase.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4a5b6c7d8e9"
down_revision: str | None = "e3f4a5b6c7d8"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "referral_codes",
        sa.Column("user_id", _UUID, primary_key=True),
        sa.Column("code", sa.String(12), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "referrals",
        sa.Column("referee_user_id", _UUID, primary_key=True),
        sa.Column("referrer_user_id", _UUID, nullable=False, index=True),
        sa.Column("code", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "referral_commissions",
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("referrer_user_id", _UUID, nullable=False, index=True),
        sa.Column("referee_user_id", _UUID, nullable=False),
        sa.Column("amount_xaf", sa.Integer(), nullable=False),
        sa.Column("commission_xaf", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("referral_commissions")
    op.drop_table("referrals")
    op.drop_table("referral_codes")
