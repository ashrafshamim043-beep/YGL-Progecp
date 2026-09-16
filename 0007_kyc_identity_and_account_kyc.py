"""3-Account Policy: kyc_identities, account_kyc_records, members.kyc_identity_id

Y.G.L Phase 8 Business Decision ("Member KYC, 3-Account Policy and
Commission Withdrawal", CONFIRMED follow-up). Adds:
  - kyc_identities (Master KYC Identity, unique per document)
  - account_kyc_records (Initial/lightweight KYC submissions)
  - members.kyc_identity_id (new NULLABLE column, additive only -- no
    existing members column/behavior is touched)

The max-3-Member-IDs-per-identity rule is enforced at the APPLICATION
layer (kyc_identity/core.py's link_member_to_identity, already tested --
11 tests). This migration does not itself enforce max-3 via a DB
constraint (there is no simple portable SQL CHECK for "count of rows
referencing this FK <= 3" in PostgreSQL without a trigger); a
TECHNICAL LEAD DECISION on whether to add a DB-level trigger as defense-
in-depth is flagged here, not decided unilaterally.

This file has NOT been executed against any database in this environment
(no network access to run PostgreSQL here -- same disclosed sandbox
limitation as every other migration in this project).

Revision ID: 0007_kyc_identity_and_account_kyc
Revises: 0006_commission_persistence
Create Date: 2026-09-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_kyc_identity_and_account_kyc"
down_revision = "0006_commission_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kyc_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("normalized_document_number", sa.String(64), nullable=False),
        sa.Column("mobile_number", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("document_type", "normalized_document_number",
                             name="uq_kyc_identities_document"),
    )

    # Additive-only: members table's EXISTING columns are untouched.
    op.add_column("members", sa.Column(
        "kyc_identity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("kyc_identities.id"), nullable=True,
    ))

    op.create_table(
        "account_kyc_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("kyc_identity_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("kyc_identities.id"), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("document_image_reference", sa.Text, nullable=False),
        sa.Column("mobile_number", sa.String(32), nullable=False),
        sa.Column("mobile_otp_verified", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(32), nullable=False, server_default="INITIAL_KYC_SUBMITTED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_account_kyc_records_member_id", "account_kyc_records", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_account_kyc_records_member_id", table_name="account_kyc_records")
    op.drop_table("account_kyc_records")
    op.drop_column("members", "kyc_identity_id")
    op.drop_table("kyc_identities")
