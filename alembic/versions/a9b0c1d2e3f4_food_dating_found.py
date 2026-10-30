"""Food, Dating and Found: their tables.

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a9b0c1d2e3f4"
down_revision: str | None = "f8a9b0c1d2e3"
branch_labels: str | None = None
depends_on: str | None = None

_UUID = postgresql.UUID(as_uuid=True)
_WHEN = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "food_restaurants",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("owner_user_id", _UUID, nullable=False, unique=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("place", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", _WHEN, nullable=False),
    )
    op.create_table(
        "food_daily_menus",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("restaurant_id", _UUID, nullable=False, index=True),
        sa.Column("day", sa.Date(), nullable=False, index=True),
        sa.Column("dishes", postgresql.JSONB(), nullable=False),
        sa.Column("published_at", _WHEN, nullable=False),
        sa.Column("closed_at", _WHEN, nullable=True),
        sa.UniqueConstraint("restaurant_id", "day", name="uq_food_menu_day"),
    )
    op.create_table(
        "food_requests",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("restaurant_id", _UUID, nullable=False, index=True),
        sa.Column("client_user_id", _UUID, nullable=False),
        sa.Column("dish", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", _WHEN, nullable=False),
    )
    op.create_table(
        "dating_profiles",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("first_name", sa.String(60), nullable=False),
        sa.Column("age", sa.Integer(), nullable=False),
        sa.Column("sex", sa.String(8), nullable=False),
        sa.Column("profession", sa.String(80), nullable=False),
        sa.Column("country", sa.String(60), nullable=False),
        sa.Column("city", sa.String(60), nullable=False),
        sa.Column("photo_key", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("looking", postgresql.JSONB(), nullable=False),
        sa.Column("seeking", sa.String(8), nullable=True),
        sa.Column("created_at", _WHEN, nullable=False),
    )
    op.create_table(
        "dating_answers",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("from_profile", _UUID, nullable=False),
        sa.Column("to_profile", _UUID, nullable=False),
        sa.Column("liked", sa.Boolean(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("created_at", _WHEN, nullable=False),
        sa.UniqueConstraint("from_profile", "to_profile", name="uq_dating_answer"),
    )
    op.create_table(
        "found_declarations",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("finder_user_id", _UUID, nullable=False, index=True),
        sa.Column("document_type", sa.String(24), nullable=False, index=True),
        sa.Column("place", postgresql.JSONB(), nullable=True),
        sa.Column("found_on", sa.String(60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("photos", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", _WHEN, nullable=False),
    )
    op.create_table(
        "found_claims",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("owner_user_id", _UUID, nullable=False, index=True),
        sa.Column("document_type", sa.String(24), nullable=False, index=True),
        sa.Column("full_name", sa.String(120), nullable=False),
        sa.Column("place", postgresql.JSONB(), nullable=True),
        sa.Column("lost_on", sa.String(60), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("proofs", postgresql.JSONB(), nullable=False),
        sa.Column("declaration_id", _UUID, nullable=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_at", _WHEN, nullable=True),
        sa.Column("created_at", _WHEN, nullable=False),
    )


def downgrade() -> None:
    for table in (
        "found_claims",
        "found_declarations",
        "dating_answers",
        "dating_profiles",
        "food_requests",
        "food_daily_menus",
        "food_restaurants",
    ):
        op.drop_table(table)
