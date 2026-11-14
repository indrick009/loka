"""Complaint reports: signalements written on WhatsApp by anyone, unverified at first.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e3f4a5b6c7d8"
down_revision: str | None = "d2e3f4a5b6c7"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "complaint_reports",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("reference", sa.String(12), nullable=False, unique=True),
        sa.Column("phone", sa.String(20), nullable=False, index=True),
        sa.Column("session_id", _UUID, nullable=True),
        sa.Column("account", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "media_keys", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("complaint_reports")
