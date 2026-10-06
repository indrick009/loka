"""payment callback signature and idempotency key

Revision ID: 8f3c1b9e2d04
Revises: b277c0a90c04
Create Date: 2026-10-06 08:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f3c1b9e2d04"
down_revision: str | None = "b277c0a90c04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "service_fee_payments",
        sa.Column("idempotency_key", sa.String(length=180), nullable=True),
    )
    op.add_column(
        "service_fee_payments",
        sa.Column("callback_signature", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_fee_payments", "callback_signature")
    op.drop_column("service_fee_payments", "idempotency_key")