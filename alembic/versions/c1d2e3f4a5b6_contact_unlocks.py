"""The 1 000 FCFA contact unlocks of Dating and Found.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b0c1d2e3f4a5"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "contact_unlocks",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("payer_user_id", _UUID, nullable=False),
        sa.Column("target_user_id", _UUID, nullable=False),
        sa.Column("purpose", sa.String(12), nullable=False),
        sa.Column("subject_id", _UUID, nullable=False),
        sa.Column("amount_xaf", sa.Integer(), nullable=False),
        sa.Column("payer_phone", sa.String(20), nullable=False),
        sa.Column("payment_reference", sa.String(120), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("contact_unlocks")
