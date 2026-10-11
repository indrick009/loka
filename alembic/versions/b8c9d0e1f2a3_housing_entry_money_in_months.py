"""Count entry money in months, and tell "one lodging" from "never asked".

The business spec counts the caution and the advance in months of rent, not in
francs: "je paye 6 mois de loyer et j'ajoute deux mois comme caution
remboursable" (conception_metier/kora-housing-v1.md §4.7 and §4.8). An amount
stored instead silently decouples the day a landlord corrects his rent, and the
tenant's "j'ai quatre mois" needs converting before it can be compared with
what the landlord asks — which is the axis §8 negotiates on.

Expand step only. ``deposit_xaf`` stays, nullable and no longer written, so a
rollback of the application has somewhere to land; the contract step drops it.
No conversion is attempted: months would have to be derived by dividing a
stored amount by a rent that may itself be null, and a wrong number of months
is worse than a missing one — the landlord is simply asked again.

``quantity`` becomes nullable so that "the landlord was never asked" is
distinguishable from "one lodging". Without it, step 3 of the form is the one
step that can never be reported as missing.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("properties", sa.Column("deposit_months", sa.Integer(), nullable=True))
    op.add_column("properties", sa.Column("advance_months", sa.Integer(), nullable=True))
    op.alter_column("properties", "quantity", existing_type=sa.Integer(), nullable=True)
    op.alter_column(
        "properties", "quantity", existing_type=sa.Integer(), server_default=None
    )


def downgrade() -> None:
    # Rows written since the upgrade have no quantity, and the column is about
    # to be NOT NULL again: one lodging is the only safe reading.
    op.execute("UPDATE properties SET quantity = 1 WHERE quantity IS NULL")
    op.alter_column(
        "properties", "quantity", existing_type=sa.Integer(), server_default="1"
    )
    op.alter_column("properties", "quantity", existing_type=sa.Integer(), nullable=False)
    op.drop_column("properties", "advance_months")
    op.drop_column("properties", "deposit_months")
