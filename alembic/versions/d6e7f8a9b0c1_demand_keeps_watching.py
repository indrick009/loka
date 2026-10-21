"""An answered demand keeps watching, and is asked after a month.

``proposed_property_ids`` keeps a later listing from sending a lodging twice;
``confirmed_at`` is when the tenant last said he is looking (backfilled with
the demand's own date) and ``reminded_at`` when he was asked whether he still
is. The array gets a constant default, so adding it rewrites no row.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6e7f8a9b0c1"
down_revision: str | None = "c5d6e7f8a9b0"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_demands",
        sa.Column(
            "proposed_property_ids",
            postgresql.ARRAY(sa.String(36)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "tenant_demands", sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute("UPDATE tenant_demands SET confirmed_at = received_at")
    op.alter_column("tenant_demands", "confirmed_at", nullable=False)
    op.add_column(
        "tenant_demands", sa.Column("reminded_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tenant_demands", "reminded_at")
    op.drop_column("tenant_demands", "confirmed_at")
    op.drop_column("tenant_demands", "proposed_property_ids")
