"""refresh_tokens table

Persists RefreshTokenRecord (auth/core.py) -- backing store for Step 1's
Auth refresh-token rotation/revocation mechanism.

This file has NOT been executed against any database in this environment.

Revision ID: 0005_refresh_tokens
Revises: 0004_audit_log
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_refresh_tokens"
down_revision = "0004_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("token_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("admin_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("admins.id"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("replaced_by_token_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_index("ix_refresh_tokens_admin_id", "refresh_tokens", ["admin_id"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_admin_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
