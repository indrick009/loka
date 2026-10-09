"""property standing

Adds the nullable ``standing`` column (MODERN / NON_MODERN) to the property
write model. The standing is collected by the WhatsApp listing flow and
describes the listing; it does not gate publication.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("standing", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("properties", "standing")
