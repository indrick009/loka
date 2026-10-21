"""Match a tenant's shared location by distance, not only by district name.

A tenant who shares his WhatsApp location used to be searched across the
whole city: the pin named the city and nothing else. Demands now keep the
point, search documents carry the listing's own, and the matcher measures the
distance between them. Nullable columns, no default: adding them rewrites no
row and takes no long lock. Existing documents are backfilled from their
listing so a listing published before this migration can be found by distance
too.

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a3b4c5d6e7f8"
down_revision: str | None = "f2a3b4c5d6e7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    for table in ("tenant_demands", "property_search_documents"):
        op.add_column(table, sa.Column("latitude", sa.Float(), nullable=True))
        op.add_column(table, sa.Column("longitude", sa.Float(), nullable=True))
    op.execute(
        """
        UPDATE property_search_documents AS document
           SET latitude = property.latitude,
               longitude = property.longitude
          FROM properties AS property
         WHERE property.id = document.property_id
           AND property.latitude IS NOT NULL
           AND property.longitude IS NOT NULL
        """
    )


def downgrade() -> None:
    for table in ("property_search_documents", "tenant_demands"):
        op.drop_column(table, "longitude")
        op.drop_column(table, "latitude")
