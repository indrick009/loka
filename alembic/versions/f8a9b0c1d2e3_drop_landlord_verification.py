"""Landlord identity verification is gone, and its tables with it.

Decided 27 October: no step of the listing flow asks for a CNI, so nothing
writes these tables any more. The downgrade recreates them empty — the
evidence they held is not restored.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8a9b0c1d2e3"
down_revision: str | None = "e7f8a9b0c1d2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("verification_documents")
    op.drop_table("verification_requests")


def downgrade() -> None:
    op.create_table(
        "verification_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("landlord_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("review_mode", sa.String(length=16), nullable=False),
        sa.Column("rejection_reason", sa.String(length=48), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=True),
        sa.Column("selfie_object_key", sa.String(length=255), nullable=True),
        sa.Column("selfie_checksum", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by", sa.UUID(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_requests")),
    )
    op.create_index(
        "ix_verification_landlord_status",
        "verification_requests",
        ["landlord_id", "status"],
    )
    op.create_index("ix_verification_queue", "verification_requests", ["status", "submitted_at"])
    op.create_index(
        op.f("ix_verification_requests_landlord_id"), "verification_requests", ["landlord_id"]
    )
    op.create_index(op.f("ix_verification_requests_user_id"), "verification_requests", ["user_id"])
    op.create_table(
        "verification_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("encrypted", sa.Boolean(), nullable=False),
        sa.Column("ocr_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_documents")),
    )
    op.create_index(
        "ix_verification_doc_request", "verification_documents", ["request_id", "kind"]
    )
    op.create_index(
        op.f("ix_verification_documents_request_id"), "verification_documents", ["request_id"]
    )
