"""Keep the provider's checkout link.

A hosted checkout URL is minted by the provider and cannot be recomputed from
the transaction id. Without the column, a payer who lost the WhatsApp message
has no way back to the page they were about to pay on, and the only remedy is
a second payment link for the same unlock.

Revision ID: a7b8c9d0e1f2
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # Nullable with no default: payments created before this revision never had
    # a link, and inventing one would be a lie a support agent could act on.
    op.add_column(
        "service_fee_payments",
        sa.Column("provider_checkout_url", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("service_fee_payments", "provider_checkout_url")
