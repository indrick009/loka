"""rental applications and visits

Creates the write models for Phase 7: the rental application (interest,
negotiation, decision, confirmation) and the visit lifecycle (request,
schedule, cancel, complete). Both carry a revision column for optimistic
locking, mirroring the property and landlord write models.

Revision ID: d4e5f6a7b8c9
Revises: 7c1e5d2a9b30
Create Date: 2026-10-08 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "7c1e5d2a9b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rental_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("landlord_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("proposed_rent_xaf", sa.BigInteger(), nullable=True),
        sa.Column("proposed_deposit_xaf", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("offer_count", sa.Integer(), nullable=False),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_rental_tenant_status", "rental_applications", ["tenant_id", "status", "created_at"]
    )
    op.create_index(
        "ix_rental_landlord_status",
        "rental_applications",
        ["landlord_id", "status", "created_at"],
    )
    op.create_index(
        "ix_rental_property_status", "rental_applications", ["property_id", "status"]
    )

    op.create_table(
        "visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("property_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("landlord_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("preferred_date", sa.Date(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_visits_tenant_status", "visits", ["tenant_id", "status", "scheduled_for"]
    )
    op.create_index(
        "ix_visits_landlord_status", "visits", ["landlord_id", "status", "scheduled_for"]
    )
    op.create_index("ix_visits_property", "visits", ["property_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_visits_property", table_name="visits")
    op.drop_index("ix_visits_landlord_status", table_name="visits")
    op.drop_index("ix_visits_tenant_status", table_name="visits")
    op.drop_table("visits")
    op.drop_index("ix_rental_property_status", table_name="rental_applications")
    op.drop_index("ix_rental_landlord_status", table_name="rental_applications")
    op.drop_index("ix_rental_tenant_status", table_name="rental_applications")
    op.drop_table("rental_applications")