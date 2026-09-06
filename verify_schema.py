#!/usr/bin/env python3
"""
CI-only verification script -- confirms migrations 0001-0006 produced the
expected tables, foreign keys, and unique constraints/indexes in a real
PostgreSQL database. Read-only (SELECT against information_schema/pg_catalog
only); never modifies schema or data. Used exclusively by
.github/workflows/ci.yml's "schema-verification" job.
"""
import os
import sys

import psycopg

EXPECTED_TABLES = {
    "roles", "permissions", "role_permissions", "admins", "members",
    "member_status_history", "kyc_documents", "sponsor_placements",
    "placement_review_queue", "kyc_verifications", "kyc_state_transitions",
    "audit_logs", "refresh_tokens", "plan_versions",
    "commission_processing_records", "plan_version_locks",
    "commission_line_items", "ledger_entries", "undistributed_amounts",
    "kyc_identities", "account_kyc_records",
}

EXPECTED_UNIQUE_INDEXES = {
    "uq_kyc_verifications_provider_applicant",
    "uq_kyc_state_transitions_idempotency_key",
    "uq_commission_processing_records_key",
    "uq_plan_version_locks_key",
    "uq_plan_versions_single_published",
    "uq_ledger_entries_commission_line_item",
    "uq_kyc_identities_document",
}


def main() -> int:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    conn = psycopg.connect(database_url)
    errors = []

    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
        actual_tables = {row[0] for row in cur.fetchall()}
        missing_tables = EXPECTED_TABLES - actual_tables
        if missing_tables:
            errors.append(f"Missing tables: {sorted(missing_tables)}")

        cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
        actual_indexes = {row[0] for row in cur.fetchall()}
        missing_indexes = EXPECTED_UNIQUE_INDEXES - actual_indexes
        if missing_indexes:
            errors.append(f"Missing unique indexes: {sorted(missing_indexes)}")

        # Foreign key spot-check: role_permissions -> roles/permissions,
        # ledger_entries -> commission_line_items, refresh_tokens -> admins.
        cur.execute("""
            SELECT tc.table_name, kcu.column_name, ccu.table_name AS foreign_table
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
        """)
        actual_fks = {(row[0], row[1], row[2]) for row in cur.fetchall()}
        expected_fks = {
            ("role_permissions", "role_id", "roles"),
            ("role_permissions", "permission_id", "permissions"),
            ("admins", "role_id", "roles"),
            ("refresh_tokens", "admin_id", "admins"),
            ("ledger_entries", "commission_line_item_id", "commission_line_items"),
            ("kyc_state_transitions", "kyc_verification_id", "kyc_verifications"),
            ("members", "kyc_identity_id", "kyc_identities"),
            ("account_kyc_records", "member_id", "members"),
            ("account_kyc_records", "kyc_identity_id", "kyc_identities"),
        }
        missing_fks = expected_fks - actual_fks
        if missing_fks:
            errors.append(f"Missing foreign keys: {sorted(missing_fks)}")

    conn.close()

    if errors:
        print("SCHEMA VERIFICATION: FAILED")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"SCHEMA VERIFICATION: PASSED ({len(EXPECTED_TABLES)} tables, "
          f"{len(EXPECTED_UNIQUE_INDEXES)} unique indexes, {len(expected_fks)} FKs spot-checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
