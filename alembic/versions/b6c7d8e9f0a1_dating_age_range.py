"""Dating: the age range a member seeks.

Empty for the profiles made before: no bound, proposed as before.

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dating_profiles", sa.Column("age_min", sa.Integer(), nullable=True))
    op.add_column("dating_profiles", sa.Column("age_max", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("dating_profiles", "age_max")
    op.drop_column("dating_profiles", "age_min")
