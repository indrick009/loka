"""The tenant's terms put with an availability question, and the reply (§8).

Both nullable JSON: most questions carry no offer, and an answer to one that
did may say nothing of the offer ("c'est déjà pris").

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e7f8a9b0c1d2"
down_revision: str | None = "d6e7f8a9b0c1"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "property_availability_checks",
        sa.Column("offer", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "property_availability_checks",
        sa.Column("offer_reply", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("property_availability_checks", "offer_reply")
    op.drop_column("property_availability_checks", "offer")
