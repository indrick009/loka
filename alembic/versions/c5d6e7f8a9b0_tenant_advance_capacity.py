"""Hold the advance a landlord asks against what a tenant can pay.

The tenant's fourth question (§3) — how many months of rent he can hand over
up front — is stored on his demand, and the landlord's requirement is copied
into the search read model so the catalogue can be filtered on it. Existing
documents are backfilled from the listing they project; both columns are
nullable, so a listing or a demand without the figure is never held back.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c5d6e7f8a9b0"
down_revision: str | None = "b4c5d6e7f8a9"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("tenant_demands", sa.Column("max_advance_months", sa.Integer(), nullable=True))
    op.add_column(
        "property_search_documents", sa.Column("advance_months", sa.Integer(), nullable=True)
    )
    op.execute(
        "UPDATE property_search_documents AS d SET advance_months = p.advance_months "
        "FROM properties AS p WHERE p.id = d.property_id"
    )


def downgrade() -> None:
    op.drop_column("property_search_documents", "advance_months")
    op.drop_column("tenant_demands", "max_advance_months")
