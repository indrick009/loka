"""reviews and complaints

The Avis et plaintes context: one table for the internal ranking marks, one for
the complaints a human reads before sanctioning, one for whether a provider is
still put forward.

No backfill. There is nothing to carry over: Kora has never collected either,
which is the problem the context exists to fix.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("provider_kind", sa.String(length=24), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("offer_id", sa.UUID(), nullable=False),
        sa.Column("introduction_id", sa.UUID(), nullable=False),
        sa.Column("author_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.SmallInteger(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_reviews")),
        sa.UniqueConstraint(
            "domain", "introduction_id", name="uq_provider_reviews_domain_introduction"
        ),
    )
    op.create_index("ix_provider_reviews_provider", "provider_reviews", ["provider_id"])
    op.create_index("ix_provider_reviews_offer", "provider_reviews", ["offer_id"])

    op.create_table(
        "provider_complaints",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("provider_kind", sa.String(length=24), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("offer_id", sa.UUID(), nullable=False),
        sa.Column("introduction_id", sa.UUID(), nullable=False),
        sa.Column("complainant_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("account", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewer_id", sa.UUID(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_complaints")),
        sa.UniqueConstraint(
            "domain", "introduction_id", name="uq_provider_complaints_domain_introduction"
        ),
    )
    op.create_index(
        "ix_provider_complaints_provider_status",
        "provider_complaints",
        ["provider_id", "status"],
    )
    op.create_index(
        "ix_provider_complaints_queue", "provider_complaints", ["status", "filed_at"]
    )

    op.create_table(
        "provider_standings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("provider_kind", sa.String(length=24), nullable=False),
        sa.Column("provider_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("pending_complaints", sa.Integer(), nullable=False),
        sa.Column("upheld_complaints", sa.Integer(), nullable=False),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_provider_standings")),
        sa.UniqueConstraint(
            "provider_kind", "provider_id", name="uq_provider_standings_kind_provider"
        ),
    )
    op.create_index("ix_provider_standings_status", "provider_standings", ["status"])


def downgrade() -> None:
    op.drop_index("ix_provider_standings_status", table_name="provider_standings")
    op.drop_table("provider_standings")
    op.drop_index("ix_provider_complaints_queue", table_name="provider_complaints")
    op.drop_index(
        "ix_provider_complaints_provider_status", table_name="provider_complaints"
    )
    op.drop_table("provider_complaints")
    op.drop_index("ix_provider_reviews_offer", table_name="provider_reviews")
    op.drop_index("ix_provider_reviews_provider", table_name="provider_reviews")
    op.drop_table("provider_reviews")
