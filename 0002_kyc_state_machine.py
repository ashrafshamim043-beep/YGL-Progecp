"""KYC state machine tables (kyc_verifications, kyc_state_transitions)

Adds the tables needed for the corrected, provider-agnostic KYC state
machine (see YGL_Sumsub_Due_Diligence_Architecture_Gate.md Section D and
the Business Owner's webhook-security/state-machine corrections).

This file has NOT been executed against any database in this environment
(no network access to run PostgreSQL/Alembic here -- see the honesty
disclosure given earlier in this engagement). It is code, syntax-checked,
ready for an operator to run via `alembic upgrade head` against a real
provisioned database.

Revision ID: 0002_kyc_state_machine
Revises: 0001_phase_8_1_foundation
Create Date: 2026-08-30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_kyc_state_machine"
down_revision = "0001_phase_8_1_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kyc_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("members.id"), nullable=False),
        sa.Column("provider_name", sa.String(32), nullable=False),
        sa.Column("provider_applicant_id", sa.String(255), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Unique constraint on provider + provider_applicant_id, per Business
    # Owner directive.
    op.create_unique_constraint(
        "uq_kyc_verifications_provider_applicant",
        "kyc_verifications", ["provider_name", "provider_applicant_id"],
    )

    op.create_table(
        "kyc_state_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("sequence", sa.Integer, autoincrement=True, nullable=False),
        sa.Column(
            "kyc_verification_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("kyc_verifications.id"), nullable=False,
        ),
        sa.Column("from_state", sa.String(32)),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("valid_previous_state_check", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("provider_applicant_id", sa.String(255)),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("provider_native_event_id", sa.String(255)),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    # Idempotency/duplicate-webhook protection at the DB level.
    op.create_unique_constraint(
        "uq_kyc_state_transitions_idempotency_key",
        "kyc_state_transitions", ["idempotency_key"],
    )
    # Explicit index for the insertion-order chain-gaplessness check.
    op.create_index(
        "ix_kyc_state_transitions_verification_sequence",
        "kyc_state_transitions", ["kyc_verification_id", "sequence"],
    )

    # kyc_documents: add vendor applicant reference (per Section H of the
    # Due-Diligence doc). Raw biometric files are never stored here -- only
    # the provider's reference.
    op.add_column("kyc_documents", sa.Column("provider_name", sa.String(32)))
    op.add_column("kyc_documents", sa.Column("provider_applicant_id", sa.String(255)))

    # Append-only enforcement note: actual REVOKE UPDATE, DELETE grants for
    # kyc_state_transitions are applied via a separate, environment-specific
    # DB-provisioning script (not inline DDL here), matching the same
    # pattern already used for ledger_entries.


def downgrade() -> None:
    op.drop_column("kyc_documents", "provider_applicant_id")
    op.drop_column("kyc_documents", "provider_name")
    op.drop_index("ix_kyc_state_transitions_verification_sequence", table_name="kyc_state_transitions")
    op.drop_constraint("uq_kyc_state_transitions_idempotency_key", "kyc_state_transitions", type_="unique")
    op.drop_table("kyc_state_transitions")
    op.drop_constraint("uq_kyc_verifications_provider_applicant", "kyc_verifications", type_="unique")
    op.drop_table("kyc_verifications")
