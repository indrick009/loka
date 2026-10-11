"""unlock keyed on the tenant and the listing

The 1 000 FCFA unlock was keyed on ``application_id`` — a rental application —
and there is no application in Kora V1. A tenant pays to unlock one landlord's
number *and* one lodging's location, so the pair the grant expresses is
(tenant, listing): the same tenant returning to the same listing owes nothing,
a second listing from the same landlord is a second unlock, and another tenant
wanting that listing pays his own.

The backfill is the reason this migration runs *before* the rental tables are
dropped: ``rental_applications`` is the only place that maps an application to
its property, and a payment keyed on a concept already removed cannot be
migrated from.

``application_id`` is kept, nullable, for the rollback window. It is dropped
with the rental tables in the migration that removes that context, once no
reader refers to it.

A row whose application has already been deleted cannot be re-anchored, so it
is retired rather than guessed at: ``EXPIRED`` for a live payment, ``REVOKED``
for a grant. Inventing a property id would hand someone a listing they never
paid for, which is the one outcome worse than an expired attempt.

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-10-11 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPEN_STATUSES = "status IN ('CREATED', 'INITIATED', 'PENDING_CONFIRMATION')"


def upgrade() -> None:
    op.add_column(
        "service_fee_payments", sa.Column("property_id", sa.UUID(), nullable=True)
    )
    op.add_column(
        "service_access_grants", sa.Column("property_id", sa.UUID(), nullable=True)
    )

    op.execute(
        """
        UPDATE service_fee_payments AS p
           SET property_id = a.property_id
          FROM rental_applications AS a
         WHERE a.id = p.application_id
        """
    )
    op.execute(
        """
        UPDATE service_access_grants AS g
           SET property_id = a.property_id
          FROM rental_applications AS a
         WHERE a.id = g.application_id
        """
    )

    # Orphans: the application is gone, so the pair cannot be reconstructed.
    # The column is filled with the old id only to satisfy NOT NULL; the
    # retired status is what stops the row being read as an entitlement.
    op.execute(
        """
        UPDATE service_fee_payments
           SET status = 'EXPIRED', property_id = application_id
         WHERE property_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE service_access_grants
           SET status = 'REVOKED',
               revocation_reason = 'listing could not be resolved on re-anchoring',
               property_id = application_id
         WHERE property_id IS NULL
        """
    )

    op.alter_column("service_fee_payments", "property_id", nullable=False)
    op.alter_column("service_access_grants", "property_id", nullable=False)
    op.alter_column("service_fee_payments", "application_id", nullable=True)
    op.alter_column("service_access_grants", "application_id", nullable=True)

    op.create_index(
        op.f("ix_service_fee_payments_property_id"),
        "service_fee_payments",
        ["property_id"],
        unique=False,
    )
    op.drop_index(
        "uq_payment_open_application",
        table_name="service_fee_payments",
        postgresql_where=_OPEN_STATUSES,
    )
    op.create_index(
        "uq_payment_open_listing",
        "service_fee_payments",
        ["tenant_id", "property_id"],
        unique=True,
        postgresql_where=_OPEN_STATUSES,
    )
    op.drop_index(
        "uq_grant_active_application",
        table_name="service_access_grants",
        postgresql_where="status = 'ACTIVE'",
    )
    op.create_index(
        "uq_grant_active_listing",
        "service_access_grants",
        ["tenant_id", "property_id"],
        unique=True,
        postgresql_where="status = 'ACTIVE'",
    )


def downgrade() -> None:
    op.drop_index(
        "uq_grant_active_listing",
        table_name="service_access_grants",
        postgresql_where="status = 'ACTIVE'",
    )
    op.create_index(
        "uq_grant_active_application",
        "service_access_grants",
        ["application_id"],
        unique=True,
        postgresql_where="status = 'ACTIVE'",
    )
    op.drop_index(
        "uq_payment_open_listing",
        table_name="service_fee_payments",
        postgresql_where=_OPEN_STATUSES,
    )
    op.create_index(
        "uq_payment_open_application",
        "service_fee_payments",
        ["application_id"],
        unique=True,
        postgresql_where=_OPEN_STATUSES,
    )
    op.drop_index(
        op.f("ix_service_fee_payments_property_id"), table_name="service_fee_payments"
    )

    # A row re-anchored while application_id was already NULL cannot go back.
    op.execute("DELETE FROM service_access_grants WHERE application_id IS NULL")
    op.execute("DELETE FROM service_fee_payments WHERE application_id IS NULL")
    op.alter_column("service_access_grants", "application_id", nullable=False)
    op.alter_column("service_fee_payments", "application_id", nullable=False)
    op.drop_column("service_access_grants", "property_id")
    op.drop_column("service_fee_payments", "property_id")
