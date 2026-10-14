"""Keep what the decision model would have decided, next to what Kora did.

The pilot's only output. One row per turn the model was asked about, including
the ones where it failed — a comparison that kept only successes would
overstate how dependable it is. The message text is not copied: it already
lives in inbound_messages and is joined by message_id.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2a3b4c5d6e7"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "decision_shadows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("flow_before", sa.String(length=32), nullable=False),
        sa.Column("step_before", sa.String(length=48), nullable=False),
        sa.Column("pending_question", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("answers", postgresql.JSONB(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 8), nullable=True),
        sa.Column("error", sa.String(length=200), nullable=True),
        sa.Column("pipeline_intent", sa.String(length=48), nullable=False),
        sa.Column("pipeline_accepted", sa.Boolean(), nullable=False),
        sa.Column("pipeline_used_llm", sa.Boolean(), nullable=False),
        sa.Column("pipeline_next_step", sa.String(length=48), nullable=True),
    )
    op.create_index("ix_decision_shadows_occurred_at", "decision_shadows", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_decision_shadows_occurred_at", table_name="decision_shadows")
    op.drop_table("decision_shadows")
