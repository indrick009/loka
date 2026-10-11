"""One availability question per listing, and the lodging count in search.

The business spec asks one question at the moment it counts and keeps no
availability state over time: no periodic re-verification, no automatic expiry,
no decrement on a match or a payment (conception_metier/kora-housing-v1.md §9).
``attempts`` and ``next_ask_at`` belonged to the polling design that rule
rejects, and nothing ever wrote them — the table had no reader and no writer at
all — so they go rather than sit there suggesting a schedule exists.

What replaces them is what the rule needs: who is waiting for the answer, a
status, and a partial unique index so a listing can only have one open question
at a time. Two open checks would let one answer settle the wrong one.

``lodgings`` lands on the search read model because an exhausted listing was
still being returned to tenants: the write model knew the quantity and the read
model did not.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "property_availability_checks",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "property_availability_checks",
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "property_availability_checks",
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_index("ix_availability_due", table_name="property_availability_checks")
    op.drop_column("property_availability_checks", "next_ask_at")
    op.drop_column("property_availability_checks", "attempts")
    op.create_index(
        "uq_availability_open_per_listing",
        "property_availability_checks",
        ["property_id"],
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_availability_landlord_open",
        "property_availability_checks",
        ["landlord_id", "status"],
    )

    op.add_column(
        "property_search_documents",
        sa.Column("lodgings", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("property_search_documents", "lodgings")
    op.drop_index("ix_availability_landlord_open", table_name="property_availability_checks")
    op.drop_index("uq_availability_open_per_listing", table_name="property_availability_checks")
    op.add_column(
        "property_availability_checks",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "property_availability_checks",
        sa.Column("next_ask_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_availability_due",
        "property_availability_checks",
        ["next_ask_at", "answered_at"],
    )
    op.drop_column("property_availability_checks", "version")
    op.drop_column("property_availability_checks", "status")
    op.drop_column("property_availability_checks", "tenant_id")
