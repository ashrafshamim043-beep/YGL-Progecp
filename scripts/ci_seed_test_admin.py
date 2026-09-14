#!/usr/bin/env python3
"""
CI-ONLY test-data seeding script. NEVER use this against a production or
staging database -- it directly inserts a disposable admin row with a
hard-coded test password, bypassing any real bootstrap/provisioning
process (a real Admin bootstrap procedure remains a documented,
not-yet-implemented gap -- see docs/HANDOFF_STATUS.md).

Used exclusively by .github/workflows/ci.yml's "api-verification" and
later jobs, against the ephemeral, disposable CI database created fresh
for that workflow run. The seeded credentials are test-only and are
never used against a real database in this repository or its CI config.
"""
import os
import sys
import uuid

import psycopg

CI_TEST_ADMIN_EMAIL = "ci-test-admin@ygl.example"
CI_TEST_ADMIN_PASSWORD = "CITestOnlyPassword1!"
CI_SUPPORT_ADMIN_EMAIL = "ci-support-admin@ygl.example"
CI_SUPPORT_ADMIN_PASSWORD = "CISupportOnlyPassword1!"


def seed_admin(cur, email: str, password: str, role_name: str, hasher) -> None:
    cur.execute("SELECT id FROM roles WHERE name = %s", (role_name,))
    role_row = cur.fetchone()
    if role_row is None:
        raise RuntimeError(f"Role '{role_name}' not found -- did migration 0003 run?")
    role_id = role_row[0]
    admin_id = uuid.uuid4()
    cur.execute(
        "INSERT INTO admins (id, name, email, password_hash, role_id, status, "
        "mfa_enrolled, failed_login_count) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (str(admin_id), f"CI Test Admin ({role_name})", email, hasher.hash_password(password),
         str(role_id), "ACTIVE", False, 0),
    )
    print(f"CI test admin seeded: {email} (role={role_name}, id={admin_id})")


def main() -> int:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
    from app.modules.auth.password_hashing import PasswordHasher

    hasher = PasswordHasher(iterations=10_000)  # low iteration count -- CI speed, not security

    conn = psycopg.connect(database_url)
    with conn.cursor() as cur:
        seed_admin(cur, CI_TEST_ADMIN_EMAIL, CI_TEST_ADMIN_PASSWORD, "SUPER_ADMIN", hasher)
        seed_admin(cur, CI_SUPPORT_ADMIN_EMAIL, CI_SUPPORT_ADMIN_PASSWORD, "SUPPORT_ADMIN", hasher)
        conn.commit()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
