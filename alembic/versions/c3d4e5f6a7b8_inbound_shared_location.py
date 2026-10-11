"""inbound shared location

A WhatsApp position pin arrives with no text, so the gateway dropped it as an
empty message and the four specifications' preferred way of saying where
something is never reached the platform. The columns are where it lands.

WGS84 decimal degrees, as ``locationMessage.degreesLatitude`` /
``degreesLongitude`` deliver them — the format is WhatsApp's, not ours.
``location_label`` is the place name the network attaches when the user shares
a place rather than dropping a pin.

All three are nullable: most messages are text. The pair is all-or-nothing, and
that is enforced in the domain rather than by a check constraint, because one
coordinate without the other is a validation failure worth a message, not a
driver error worth a retry.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-10-11 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inbound_messages", sa.Column("location_latitude", sa.Float(), nullable=True)
    )
    op.add_column(
        "inbound_messages", sa.Column("location_longitude", sa.Float(), nullable=True)
    )
    op.add_column(
        "inbound_messages", sa.Column("location_label", sa.String(160), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("inbound_messages", "location_label")
    op.drop_column("inbound_messages", "location_longitude")
    op.drop_column("inbound_messages", "location_latitude")
