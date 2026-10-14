"""Keep what a tenant asked for, so Kora can come back to him.

Kora answers no search in the thread. It records the demand and says so —
"votre demande est enregistrée, si un logement correspond je reviens vers vous"
— whether a lodging matches or not, because a direct match, a neighbouring area
or a price a little above all need preparing, and §8's negotiation with a
landlord cannot happen in the two seconds a reply takes.

Without this table the promise would be empty: a tenant told "je reviens vers
vous" by a system that kept nothing of his request is a tenant nobody can come
back to.

The partial unique index is the business rule. One open demand per tenant: he
refines his search as he talks, and five rows for one request would mean five
answers owed to one person.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_demands",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="OPEN"),
        sa.Column("property_type", sa.String(length=24), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column(
            "neighbourhoods",
            postgresql.ARRAY(sa.String(length=96)),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("max_rent_xaf", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="XAF"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_tenant_demand_open",
        "tenant_demands",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )
    op.create_index(
        "ix_tenant_demand_open_age", "tenant_demands", ["status", "received_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_demand_open_age", table_name="tenant_demands")
    op.drop_index("uq_tenant_demand_open", table_name="tenant_demands")
    op.drop_table("tenant_demands")
