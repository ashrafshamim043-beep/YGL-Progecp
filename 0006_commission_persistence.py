"""Commission persistence tables

Creates: plan_versions (metadata-only pointer), commission_processing_records,
plan_version_locks, commission_line_items, ledger_entries,
undistributed_amounts -- per Specification v3 Fixes A1, A2, A3, A5.

Partial unique indexes (PostgreSQL-specific, applied here as raw SQL since
SQLAlchemy/Alembic's portable UniqueConstraint cannot express a WHERE
clause):
  - plan_versions: at most one row with is_published=true at any time
  - ledger_entries: at most one COMMISSION-type entry per commission_line_item_id
    (duplicate-credit protection, Fix A2/B1)

This file has NOT been executed against any database in this environment.

Revision ID: 0006_commission_persistence
Revises: 0005_refresh_tokens
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006_commission_persistence"
down_revision = "0005_refresh_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("plan_version_id", sa.String(64), nullable=False, unique=True),
        sa.Column("is_published", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_plan_versions_single_published "
        "ON plan_versions ((true)) WHERE is_published = true"
    )

    op.create_table(
        "commission_processing_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("plan_version_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("result_reference", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "plan_version_id", "event_type",
                             name="uq_commission_processing_records_key"),
    )

    op.create_table(
        "plan_version_locks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("locked_plan_version_id", sa.String(64), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "event_type", name="uq_plan_version_locks_key"),
    )

    op.create_table(
        "commission_line_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("plan_version_id", sa.String(64), nullable=False),
        sa.Column("commission_type", sa.String(32), nullable=False),
        sa.Column("payee_member_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("generation", sa.Integer),
        sa.Column("rank", sa.String(32)),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("source_event_type", sa.String(64), nullable=False, server_default="PAYMENT_CONFIRMED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("admins.id")),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "ledger_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("reference_type", sa.String(32), nullable=False),
        sa.Column("reference_id", sa.String(64), nullable=False),
        sa.Column("commission_line_item_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("commission_line_items.id")),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("fiscal_year", sa.Integer),
        sa.Column("plan_version_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_ledger_entries_commission_line_item "
        "ON ledger_entries (commission_line_item_id) WHERE reference_type = 'COMMISSION'"
    )
    # Append-only: no updated_at column defined above (deliberate). DB-role
    # UPDATE/DELETE grants for ledger_entries, commission_line_items,
    # undistributed_amounts, commission_processing_records, and
    # plan_version_locks are revoked via a separate, environment-specific
    # DB-provisioning script -- not inline DDL in this migration.

    op.create_table(
        "undistributed_amounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("plan_version_id", sa.String(64), nullable=False),
        sa.Column("commission_type", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("fiscal_year", sa.Integer, nullable=False),
        sa.Column("generation", sa.Integer),
        sa.Column("rank", sa.String(32)),
        sa.Column("source_event_type", sa.String(64), nullable=False, server_default="PAYMENT_CONFIRMED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("undistributed_amounts")
    op.execute("DROP INDEX IF EXISTS uq_ledger_entries_commission_line_item")
    op.drop_table("ledger_entries")
    op.drop_table("commission_line_items")
    op.drop_table("plan_version_locks")
    op.drop_table("commission_processing_records")
    op.execute("DROP INDEX IF EXISTS uq_plan_versions_single_published")
    op.drop_table("plan_versions")
