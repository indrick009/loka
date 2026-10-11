"""drop rental and visit

Neither concept exists in Kora V1. Kora introduces a tenant to a landlord for
1 000 FCFA and the relationship ends there: there is no application to decide
on, no negotiation to track and no viewing to schedule, because arranging one
would put Kora back in the middle of a relationship it has been paid to leave.

``application_id`` goes with them. It was kept nullable through the re-anchoring
migration so the unlock could be rolled back; the readers are switched now, so
the column is the last thing pointing at a context that no longer exists.

This is destructive and deliberately irreversible in substance: the downgrade
rebuilds the tables and the columns, empty. Anything that was in them is gone,
which is the point — a half-restored application is worse than none.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-11 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        op.f("ix_service_fee_payments_application_id"),
        table_name="service_fee_payments",
    )
    op.drop_column("service_fee_payments", "application_id")
    op.drop_column("service_access_grants", "application_id")

    op.drop_index("ix_visits_property", table_name="visits")
    op.drop_index("ix_visits_landlord_status", table_name="visits")
    op.drop_index("ix_visits_tenant_status", table_name="visits")
    op.drop_table("visits")

    op.drop_index("ix_rental_property_status", table_name="rental_applications")
    op.drop_index("ix_rental_landlord_status", table_name="rental_applications")
    op.drop_index("ix_rental_tenant_status", table_name="rental_applications")
    op.drop_table("rental_applications")


def downgrade() -> None:
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
        "ix_rental_tenant_status",
        "rental_applications",
        ["tenant_id", "status", "created_at"],
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

    # Nullable on the way back: the applications they pointed at are gone, so
    # there is no id to restore and a NOT NULL column could not be filled.
    op.add_column(
        "service_access_grants", sa.Column("application_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "service_fee_payments", sa.Column("application_id", sa.UUID(), nullable=True)
    )
    op.create_index(
        op.f("ix_service_fee_payments_application_id"),
        "service_fee_payments",
        ["application_id"],
        unique=False,
    )
