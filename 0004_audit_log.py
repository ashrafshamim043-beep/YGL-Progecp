"""Audit log table

Adds audit_logs -- previously referenced conceptually (Architecture
Blueprint v4 Section L, RBAC/KYC AuditLogWriter interface) but never
actually created as a table until now.

Append-only: no updated_at column, no UPDATE/DELETE grant for the
application role (same policy as ledger_entries/kyc_state_transitions --
applied via a separate, environment-specific DB-provisioning script).

This file has NOT been executed against any database in this environment.

Revision ID: 0004_audit_log
Revises: 0003_rbac_permission_seed
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_audit_log"
down_revision = "0003_rbac_permission_seed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor_id", sa.String(255)),
        sa.Column("actor_type", sa.String(32)),
        sa.Column("action", sa.String(128)),
        sa.Column("target_entity", sa.String(128)),
        sa.Column("target_id", sa.String(255)),
        sa.Column("result", sa.String(32)),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_table("audit_logs")
