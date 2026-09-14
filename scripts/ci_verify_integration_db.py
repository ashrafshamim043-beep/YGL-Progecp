#!/usr/bin/env python3
"""
CI-only integration verification -- runs the full Member -> KYC -> Webhook
-> Approval -> Activation flow against a REAL PostgreSQL database, using
the actual SQLAlchemy adapters (SQLAlchemyKYCVerificationRepository,
SQLAlchemyKYCStateTransitionRepository, SQLAlchemyAuditLogWriter,
SQLAlchemyMemberActivationPort) for the first time -- every prior test of
this flow (the 24 KYC tests) used in-memory repositories only. This is
the actual "did the DB adapters work" proof that a sandboxed environment
could never provide.

Never touches a production/staging database -- DATABASE_URL is expected
to point at the CI job's disposable service-container database.
"""
import os
import sys
import uuid
import hmac
import hashlib
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.modules.member.db_models import Member
from app.modules.kyc.core import KYCState
from app.modules.kyc.service import YGLKYCService
from app.modules.kyc.sumsub_adapter import SumsubAdapter
from app.modules.kyc.db_repositories import SQLAlchemyKYCVerificationRepository, SQLAlchemyKYCStateTransitionRepository
from app.modules.kyc.member_activation_adapter import SQLAlchemyMemberActivationPort
from app.modules.audit.db_repository import SQLAlchemyAuditLogWriter, AuditLogRow

SUMSUB_WEBHOOK_SECRET = os.environ.get("SUMSUB_WEBHOOK_SECRET", "dev-only-placeholder-not-a-real-secret")

results = []


def check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    results.append((label, status))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))


def main() -> int:
    database_url = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    database_url = "postgresql+psycopg://" + database_url.split("://", 1)[1]
    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    db = Session()

    member_id = uuid.uuid4()
    member = Member(
        id=member_id, full_name="CI Integration Test Member",
        email=f"ci-integration-{member_id}@ygl.example",
        password_hash="not-used-in-this-check", account_status="PENDING", role="MEMBER",
    )
    db.add(member)
    db.commit()
    check("Member row created in real PostgreSQL", True)

    provider = SumsubAdapter(webhook_secret=SUMSUB_WEBHOOK_SECRET)
    verification_repo = SQLAlchemyKYCVerificationRepository(db)
    transition_repo = SQLAlchemyKYCStateTransitionRepository(db)
    audit_log = SQLAlchemyAuditLogWriter(db)
    member_activation = SQLAlchemyMemberActivationPort(db)
    kyc_service = YGLKYCService(provider, verification_repo, transition_repo, audit_log, member_activation)

    verification = kyc_service.start_verification(str(member_id), "SUMSUB", f"ci-applicant-{member_id}")
    db.commit()
    check("KYC verification row persisted via SQLAlchemyKYCVerificationRepository (real DB)", verification.id is not None)

    kyc_service.submit_document(verification.id, str(member_id))
    db.commit()

    in_progress_payload = json.dumps({
        "applicantId": f"ci-applicant-{member_id}", "type": "applicantReviewed",
        "reviewStatus": "pending", "reviewResult": {}, "createdAtMs": "2026-08-30 09:00:00.000",
    }).encode()
    sig1 = hmac.new(SUMSUB_WEBHOOK_SECRET.encode(), in_progress_payload, hashlib.sha256).hexdigest()
    r1 = kyc_service.process_webhook(in_progress_payload, sig1)
    db.commit()
    check("Webhook 1 (in-progress) processed against real DB", r1.accepted, r1.reason)

    final_payload = json.dumps({
        "applicantId": f"ci-applicant-{member_id}", "type": "applicantReviewed",
        "reviewStatus": "completed", "reviewResult": {"reviewAnswer": "GREEN"},
        "createdAtMs": "2026-08-30 10:00:00.000",
    }).encode()
    sig2 = hmac.new(SUMSUB_WEBHOOK_SECRET.encode(), final_payload, hashlib.sha256).hexdigest()
    r2 = kyc_service.process_webhook(final_payload, sig2)
    db.commit()
    check("Webhook 2 (approval) processed, member activated (SQLAlchemyMemberActivationPort, real transaction)",
          r2.accepted and r2.member_activated, f"accepted={r2.accepted} activated={r2.member_activated}")

    db.expire_all()
    refreshed_member = db.get(Member, member_id)
    check("Member.account_status is ACTUALLY 'ACTIVE' in PostgreSQL after activation",
          refreshed_member.account_status == "ACTIVE", refreshed_member.account_status)

    audit_rows = db.execute(select(AuditLogRow).where(AuditLogRow.target_id == str(member_id))).scalars().all()
    audit_event_types = {row.event_type for row in audit_rows}
    check("MEMBER_ACTIVATED audit row genuinely persisted in PostgreSQL audit_logs table",
          "MEMBER_ACTIVATED" in audit_event_types, str(audit_event_types))

    # Duplicate webhook (Step: duplicate financial/KYC request idempotency, DB-level)
    r3 = kyc_service.process_webhook(final_payload, sig2)
    db.commit()
    check("Duplicate webhook against real DB is rejected as duplicate_event (idempotency-key UNIQUE constraint honored)",
          not r3.accepted and r3.reason == "duplicate_event", r3.reason)

    db.close()

    print()
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"TOTAL: {len(results)} checks, {len(results) - len(failed)} PASS, {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
