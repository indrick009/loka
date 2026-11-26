"""Dating: what a member is and wants, read from the profile by a model.

Empty for the profiles made before, until they are read: no effect on the
ranking meanwhile.

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c7d8e9f0a1b2"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dating_profiles",
        sa.Column("traits", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "dating_profiles",
        sa.Column("wishes", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )


def downgrade() -> None:
    op.drop_column("dating_profiles", "wishes")
    op.drop_column("dating_profiles", "traits")
