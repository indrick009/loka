"""Tell a tenant's shared pin from the centre of the district he typed.

A typed district now carries the coordinates of its centre, taken from the
offline OpenStreetMap gazetteer, so a gap can be measured ("à 5 km
d'Odza"). Only a real pin may widen an exact match, though: a tenant who
typed "Bali" must not be handed Akwa as if he had asked for it. Existing
demands keep ``true`` — every point stored before this column was a pin.
On PostgreSQL 11+ a constant default adds the column without rewriting rows.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b4c5d6e7f8a9"
down_revision: str | None = "a3b4c5d6e7f8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_demands",
        sa.Column("near_is_exact", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("tenant_demands", "near_is_exact")
