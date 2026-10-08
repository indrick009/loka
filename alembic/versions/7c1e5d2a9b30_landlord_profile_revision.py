"""landlord profile revision

Adds the optimistic-locking counter the landlord profile write model needs to
serialise concurrent verification decisions. Backfills existing rows to 0 so
the column can be non-nullable without a table rewrite lock beyond the default.

Revision ID: 7c1e5d2a9b30
Revises: 06df634a650b
Create Date: 2026-10-08 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1e5d2a9b30"
down_revision: str | None = "06df634a650b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "landlord_profiles",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.alter_column("landlord_profiles", "revision", server_default=None)


def downgrade() -> None:
    op.drop_column("landlord_profiles", "revision")