"""Dating talks: questions and photos relayed by Kora before a match is confirmed.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "dating_talks",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("first_profile", _UUID, nullable=False, index=True),
        sa.Column("second_profile", _UUID, nullable=False, index=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("first_side", postgresql.JSONB(), nullable=False),
        sa.Column("second_side", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dating_talks")
