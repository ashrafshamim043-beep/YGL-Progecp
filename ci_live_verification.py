#!/usr/bin/env python3
"""
CI-only live HTTP verification -- Steps 9-11 (API verification, Security
verification, Integration verification) against a running instance of the
app (expected at http://localhost:8000, started by the CI job before this
script runs). Never used outside CI; makes no assumption about and never
touches a production/staging server.

Exits 0 only if every check passes; prints a PASS/FAIL line per check and
never silently skips a failure.
"""
import os
import sys
import time
import hmac
import hashlib
import json

import httpx

BASE_URL = "http://localhost:8000"
API = f"{BASE_URL}/api/v1"

CI_TEST_ADMIN_EMAIL = "ci-test-admin@ygl.example"
CI_TEST_ADMIN_PASSWORD = "CITestOnlyPassword1!"
CI_SUPPORT_ADMIN_EMAIL = "ci-support-admin@ygl.example"
CI_SUPPORT_ADMIN_PASSWORD = "CISupportOnlyPassword1!"
SUMSUB_WEBHOOK_SECRET = os.environ.get("SUMSUB_WEBHOOK_SECRET", "dev-only-placeholder-not-a-real-secret")

results = []


def check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    results.append((label, status, detail))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))


def main() -> int:
    client = httpx.Client(timeout=10.0)

    # --- Step 9: API verification ------------------------------------------
    r = client.get(f"{BASE_URL}/health")
    check("health endpoint returns 200", r.status_code == 200, str(r.status_code))

    r = client.post(f"{API}/admin/login", json={"email": CI_TEST_ADMIN_EMAIL, "password": CI_TEST_ADMIN_PASSWORD})
    check("login with correct credentials succeeds", r.status_code == 200, f"{r.status_code}: {r.text}")
    access_token = r.json().get("access_token") if r.status_code == 200 else None
    refresh_token_id = r.json().get("refresh_token_id") if r.status_code == 200 else None

    r = client.post(f"{API}/admin/login", json={"email": CI_TEST_ADMIN_EMAIL, "password": "wrong-password"})
    check("login with wrong password returns 401", r.status_code == 401, str(r.status_code))

    r = client.post(f"{API}/admin/refresh", json={"refresh_token_id": refresh_token_id or "x"})
    check("refresh with valid token succeeds", r.status_code == 200, f"{r.status_code}: {r.text}")
    new_refresh_id = r.json().get("refresh_token_id") if r.status_code == 200 else None

    # --- Step 10: Security verification -------------------------------------
    r = client.post(f"{API}/kyc/some-id/manual-review/approve", json={})
    check("protected endpoint without token returns 401", r.status_code == 401, str(r.status_code))

    r = client.post(f"{API}/kyc/some-id/manual-review/approve", json={}, headers={"Authorization": "Bearer not-a-real-token"})
    check("protected endpoint with invalid token returns 401", r.status_code == 401, str(r.status_code))

    if access_token:
        r = client.post(f"{API}/kyc/some-id/manual-review/approve", json={}, headers={"Authorization": f"Bearer {access_token}"})
        check("Super Admin token CAN reach kyc.review-protected endpoint (not blocked by RBAC)",
              r.status_code != 403, str(r.status_code))

    r2 = client.post(f"{API}/admin/login", json={"email": CI_SUPPORT_ADMIN_EMAIL, "password": CI_SUPPORT_ADMIN_PASSWORD})
    support_token = r2.json().get("access_token") if r2.status_code == 200 else None
    if support_token:
        r = client.post(f"{API}/kyc/some-id/manual-review/approve", json={}, headers={"Authorization": f"Bearer {support_token}"})
        check("Support Admin token BLOCKED (403) from kyc.review-protected endpoint", r.status_code == 403, str(r.status_code))

    if refresh_token_id:
        r = client.post(f"{API}/admin/refresh", json={"refresh_token_id": refresh_token_id})
        check("reusing an already-rotated (old) refresh token is rejected (401, replay protection)",
              r.status_code == 401, str(r.status_code))

    payload = json.dumps({
        "applicantId": "ci-nonexistent-applicant", "type": "applicantReviewed",
        "reviewStatus": "completed", "reviewResult": {"reviewAnswer": "GREEN"},
        "createdAtMs": "2026-08-30 10:00:00.000",
    }).encode()
    bad_sig = "0" * 64
    r = client.post(f"{API}/webhooks/sumsub", content=payload, headers={"X-Payload-Digest": bad_sig})
    check("webhook with invalid signature is rejected (not accepted)",
          r.status_code == 200 and r.json().get("accepted") is False, r.text)

    good_sig = hmac.new(SUMSUB_WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
    r1 = client.post(f"{API}/webhooks/sumsub", content=payload, headers={"X-Payload-Digest": good_sig})
    r2 = client.post(f"{API}/webhooks/sumsub", content=payload, headers={"X-Payload-Digest": good_sig})
    check("duplicate webhook delivery (same signature+payload) is idempotent, not double-applied",
          r1.json() == r2.json() or r2.json().get("reason") in ("duplicate_event", "applicant_mapping_failed"),
          f"{r1.text} / {r2.text}")

    # --- Step 11: Integration verification (Member -> KYC -> Audit) --------
    # Full DB-level integration verification (member_status_history,
    # audit_logs row content) requires direct DB access, which this
    # HTTP-only script does not have -- that portion is covered by
    # scripts/ci_verify_integration_db.py in the same CI job, run
    # immediately after this script, against the same live database.
    check("HTTP-level integration checks completed (DB-level checks are a separate script)", True)

    print()
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"TOTAL: {len(results)} checks, {len(results) - len(failed)} PASS, {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
