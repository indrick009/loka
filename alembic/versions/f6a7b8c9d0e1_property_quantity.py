"""property quantity

One listing can stand for several identical lodgings: same type, same
characteristics, same price. Two identical studios are published once with a
quantity of two, while two studios at different prices stay two listings,
because the tenant is comparing a price.

The column decides what "le logement est encore disponible ?" even asks — "are
any left?" rather than "is it gone?" — and it only ever moves when the landlord
declares one taken.

``NOT NULL DEFAULT 1`` because every existing row is exactly one lodging, which
is what the model assumed before this column existed. The default makes the
backfill implicit and the migration safe to replay.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-11 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "properties",
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("properties", "quantity")
