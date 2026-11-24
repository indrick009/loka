"""Referral nudges: the purchases after which the buyer was invited to share.

Purchases settled before this table existed are marked done, so nobody is
invited for an old purchase on the day it ships.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: str | None = "f4a5b6c7d8e9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "referral_nudges",
        sa.Column("source", sa.String(64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        """
        insert into referral_nudges (source, created_at)
        select 'fee:' || id::text, now() from service_fee_payments where status = 'SUCCEEDED'
        union all
        select 'unlock:' || id::text, now() from contact_unlocks where status = 'PAID'
        union all
        select 'booking:' || id::text, now() from booking_reservations where status = 'PAID'
        """
    )


def downgrade() -> None:
    op.drop_table("referral_nudges")
