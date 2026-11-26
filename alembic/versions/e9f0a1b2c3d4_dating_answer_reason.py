"""Dating: why a member said no to a profile, in their words.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dating_answers", sa.Column("reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("dating_answers", "reason")
