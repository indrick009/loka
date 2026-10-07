"""property search projection

The read model behind public search. Denormalised so a search page never joins
the write model, populated from ``property.events`` by the search projection
consumer. Existing listings are projected here once so search is not empty on
the deploy that introduces the table.

Revision ID: 06df634a650b
Revises: 8f3c1b9e2d04
Create Date: 2026-10-06 19:52:23.374206
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "06df634a650b"
down_revision: str | None = "8f3c1b9e2d04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "property_search_documents",
        sa.Column("property_id", sa.UUID(), nullable=False),
        sa.Column("landlord_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("property_type", sa.String(length=24), nullable=True),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("neighbourhood", sa.String(length=96), nullable=True),
        sa.Column("public_price_xaf", sa.BigInteger(), nullable=True),
        sa.Column("rent_xaf", sa.BigInteger(), nullable=True),
        sa.Column("charges_xaf", sa.BigInteger(), nullable=True),
        sa.Column("charging_policy", sa.String(length=16), nullable=False),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("bathrooms", sa.Integer(), nullable=True),
        sa.Column("surface_m2", sa.Integer(), nullable=True),
        sa.Column("minimum_duration_months", sa.Integer(), nullable=True),
        sa.Column("available_from", sa.Date(), nullable=True),
        sa.Column("amenities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("media_object_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("property_id", name=op.f("pk_property_search_documents")),
    )
    op.create_index(
        "ix_search_documents_city_price",
        "property_search_documents",
        ["status", "city", "public_price_xaf"],
    )
    op.create_index(
        "ix_search_documents_price",
        "property_search_documents",
        ["status", "public_price_xaf", "property_id"],
    )
    op.create_index(
        "ix_search_documents_recent",
        "property_search_documents",
        ["status", "source_created_at", "property_id"],
    )
    op.create_index(
        "ix_search_documents_type", "property_search_documents", ["status", "property_type"]
    )
    op.create_index(
        "ix_search_documents_verified", "property_search_documents", ["status", "is_verified"]
    )

    # Backfill: the projection must not start empty. One row per existing
    # property, media flattened in display order.
    op.execute(
        """
        INSERT INTO property_search_documents (
            property_id, landlord_id, status, property_type, city, neighbourhood,
            public_price_xaf, rent_xaf, charges_xaf, charging_policy,
            bedrooms, bathrooms, surface_m2, minimum_duration_months, available_from,
            amenities, media_object_keys, is_verified, published_at,
            source_created_at, source_version, projected_at
        )
        SELECT
            p.id, p.landlord_id, p.status, p.property_type, p.city, p.neighbourhood,
            p.public_price_xaf, p.rent_xaf, p.charges_xaf, p.charging_policy,
            p.bedrooms, p.bathrooms, p.surface_m2, p.minimum_duration_months,
            p.available_from,
            p.amenities,
            COALESCE(
                (
                    SELECT jsonb_agg(m.object_key ORDER BY m.position, m.id)
                    FROM property_media m
                    WHERE m.property_id = p.id AND m.status = 'UPLOADED'
                ),
                '[]'::jsonb
            ),
            p.is_verified, p.published_at,
            p.created_at, p.version, now()
        FROM properties p
        """
    )


def downgrade() -> None:
    op.drop_index("ix_search_documents_verified", table_name="property_search_documents")
    op.drop_index("ix_search_documents_type", table_name="property_search_documents")
    op.drop_index("ix_search_documents_recent", table_name="property_search_documents")
    op.drop_index("ix_search_documents_price", table_name="property_search_documents")
    op.drop_index("ix_search_documents_city_price", table_name="property_search_documents")
    op.drop_table("property_search_documents")
