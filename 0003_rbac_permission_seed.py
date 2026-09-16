"""RBAC permission/role seed (deterministic data migration)

Seeds the roles/permissions/role_permissions tables (schema already exists
from 0001_phase_8_1_foundation) with the exact, final Business Owner role
matrix from Phase 8.2 Step 2's authorization message. This is a DATA
migration only -- no schema change.

Duplicate-safety: `roles.name` and `permissions.code` both have UNIQUE
constraints (0001 migration), and `role_permissions` has a composite
PRIMARY KEY on (role_id, permission_id) -- the database itself structurally
prevents a duplicate role/permission/assignment from ever being created,
even if this migration were mistakenly run twice against non-empty tables
(the second run would fail on the unique/PK constraint rather than
silently duplicating rows).

This file has NOT been executed against any database in this environment
(no network access to run PostgreSQL here). It is code, syntax-checked,
ready for an operator to run via `alembic upgrade head`.

Revision ID: 0003_rbac_permission_seed
Revises: 0002_kyc_state_machine
Create Date: 2026-08-30
"""
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql import table, column

revision = "0003_rbac_permission_seed"
down_revision = "0002_kyc_state_machine"
branch_labels = None
depends_on = None

# Deterministic, fixed UUIDs (not random) so this migration's output is
# reproducible and referenceable -- re-generating with uuid4() on every
# run would defeat the point of a deterministic seed.
ROLE_IDS = {
    "SUPER_ADMIN": uuid.UUID("00000000-0000-0000-0000-000000000001"),
    "FINANCE_ADMIN": uuid.UUID("00000000-0000-0000-0000-000000000002"),
    "SUPPORT_ADMIN": uuid.UUID("00000000-0000-0000-0000-000000000003"),
    "COMPLIANCE_KYC_ADMIN": uuid.UUID("00000000-0000-0000-0000-000000000004"),
}

PERMISSION_CODES = [
    "member.read", "member.write", "commission.view", "commission.approve",
    "ledger.view", "adjustment.request", "adjustment.approve",
    "plan_version.publish", "kyc.review", "audit_log.read",
]
PERMISSION_IDS = {
    code: uuid.uuid5(uuid.NAMESPACE_URL, f"ygl-permission:{code}")
    for code in PERMISSION_CODES
}

# EXACT matrix per Business Owner Final Decision (Step 2 authorization).
ROLE_PERMISSIONS = {
    "SUPER_ADMIN": PERMISSION_CODES,  # all
    "FINANCE_ADMIN": [
        "member.read", "commission.view", "commission.approve",
        "ledger.view", "adjustment.request", "audit_log.read",
    ],
    "SUPPORT_ADMIN": ["member.read", "member.write"],
    "COMPLIANCE_KYC_ADMIN": ["member.read", "kyc.review", "audit_log.read"],
}


def upgrade() -> None:
    roles_table = table("roles", column("id", postgresql.UUID), column("name", sa.String), column("description", sa.String))
    permissions_table = table("permissions", column("id", postgresql.UUID), column("code", sa.String), column("description", sa.String))
    role_permissions_table = table("role_permissions", column("role_id", postgresql.UUID), column("permission_id", postgresql.UUID))

    op.bulk_insert(roles_table, [
        {"id": ROLE_IDS["SUPER_ADMIN"], "name": "SUPER_ADMIN", "description": "Full system access, all permissions."},
        {"id": ROLE_IDS["FINANCE_ADMIN"], "name": "FINANCE_ADMIN", "description": "Commission/ledger/adjustment-request operations."},
        {"id": ROLE_IDS["SUPPORT_ADMIN"], "name": "SUPPORT_ADMIN", "description": "Non-financial member support."},
        {"id": ROLE_IDS["COMPLIANCE_KYC_ADMIN"], "name": "COMPLIANCE_KYC_ADMIN", "description": "KYC review authority."},
    ])

    op.bulk_insert(permissions_table, [
        {"id": PERMISSION_IDS[code], "code": code, "description": None}
        for code in PERMISSION_CODES
    ])

    assignments = []
    for role_name, perms in ROLE_PERMISSIONS.items():
        for perm_code in perms:
            assignments.append({"role_id": ROLE_IDS[role_name], "permission_id": PERMISSION_IDS[perm_code]})
    op.bulk_insert(role_permissions_table, assignments)


def downgrade() -> None:
    op.execute("DELETE FROM role_permissions")
    op.execute("DELETE FROM permissions")
    op.execute(
        "DELETE FROM roles WHERE name IN "
        "('SUPER_ADMIN', 'FINANCE_ADMIN', 'SUPPORT_ADMIN', 'COMPLIANCE_KYC_ADMIN')"
    )
