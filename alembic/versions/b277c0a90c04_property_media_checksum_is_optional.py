"""property media checksum is optional

Revision ID: b277c0a90c04
Revises: b49b366fa7a1
Create Date: 2026-10-06 04:23:22.722779
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b277c0a90c04"
down_revision: str | None = "b49b366fa7a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "property_media", "checksum", existing_type=sa.VARCHAR(length=64), nullable=True
    )


def downgrade() -> None:
    op.alter_column(
        "property_media", "checksum", existing_type=sa.VARCHAR(length=64), nullable=False
    )
