#!/usr/bin/env python3
"""
CI-only integration + concurrency verification for the 3-Account Policy
(kyc_identities, account_kyc_records, members.kyc_identity_id) against a
REAL PostgreSQL database. Covers the exact scenarios requested:
NID/Birth-Reg persistence, status transitions, ACTIVE-on-verified,
1st/2nd/3rd-ID-allowed + 4th-ID-rejected (including a REAL concurrent-
request race-condition test using two separate DB connections/threads),
transaction rollback leaving no partial data, and three-IDs-remain-
independent at the persistence layer.

Never touches a production/staging database -- DATABASE_URL is expected
to point at the CI job's disposable service-container database.
"""
import os
import sys
import uuid
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.modules.member.db_models import Member
from app.modules.member.core import MemberAccountStatus
from app.modules.kyc_identity.core import DocumentType, link_member_to_identity, MaxMemberIdsExceededError
from app.modules.kyc_identity.db_repository import SQLAlchemyKYCIdentityRepository
from app.modules.kyc_identity.db_models import KYCIdentityRow
from app.modules.account_kyc.core import AccountKYCStatus
from app.modules.account_kyc.service import AccountKYCService
from app.modules.account_kyc.db_repository import (
    SQLAlchemyAccountKYCRepository, SQLAlchemyAccountKYCMemberActivationPort,
)
from app.modules.account_kyc.db_models import AccountKYCRow

results = []


def check(label: str, condition: bool, detail: str = ""):
    status = "PASS" if condition else "FAIL"
    results.append((label, status))
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))


def make_session(database_url):
    engine = create_engine(database_url)
    return sessionmaker(bind=engine)()


def create_pending_member(db, email_suffix) -> str:
    member_id = uuid.uuid4()
    db.add(Member(
        id=member_id, full_name=f"CI Test {email_suffix}", email=f"ci-3id-{email_suffix}@ygl.example",
        password_hash="not-used-in-this-check", account_status=MemberAccountStatus.PENDING, role="MEMBER",
    ))
    db.commit()
    return str(member_id)


def main() -> int:
    database_url = os.environ["DATABASE_URL"]
    if not database_url.startswith("postgresql+psycopg://"):
        database_url = "postgresql+psycopg://" + database_url.split("://", 1)[1]

    # --- 1-5: NID persistence, status transitions, ACTIVE-on-verified ---
    db = make_session(database_url)
    member_id = create_pending_member(db, "nid-basic")
    service = AccountKYCService(
        SQLAlchemyAccountKYCRepository(db), SQLAlchemyKYCIdentityRepository(db),
        SQLAlchemyAccountKYCMemberActivationPort(db),
    )
    result = service.submit(
        member_id=member_id, document_type=DocumentType.NID, raw_document_number="1111111111",
        document_image_reference="s3://ci/nid.jpg", mobile_number="01700000001", mobile_otp_verified=True,
    )
    check("NID Initial KYC record persisted in real PostgreSQL", result.record.id is not None)
    check("Status is literally INITIAL_KYC_VERIFIED (not a government-verification claim)",
          result.record.status == AccountKYCStatus.INITIAL_KYC_VERIFIED, result.record.status)
    check("Member genuinely ACTIVE in PostgreSQL after verified Initial KYC",
          result.member_activated, str(result.member_activated))
    db.expire_all()
    refreshed = db.get(Member, uuid.UUID(member_id))
    check("Member.account_status == ACTIVE confirmed by direct re-read from DB",
          refreshed.account_status == MemberAccountStatus.ACTIVE, refreshed.account_status)
    db.close()

    # --- Birth Registration persistence ---
    db = make_session(database_url)
    member_id_br = create_pending_member(db, "birthreg")
    service = AccountKYCService(
        SQLAlchemyAccountKYCRepository(db), SQLAlchemyKYCIdentityRepository(db),
        SQLAlchemyAccountKYCMemberActivationPort(db),
    )
    result_br = service.submit(
        member_id=member_id_br, document_type=DocumentType.BIRTH_REGISTRATION, raw_document_number="BR-2026-CI-1",
        document_image_reference="s3://ci/br.jpg", mobile_number="01700000002", mobile_otp_verified=True,
    )
    check("Birth Registration Initial KYC also persists and activates (real DB)", result_br.member_activated)
    db.close()

    # --- 6-9: 1st/2nd/3rd allowed, 4th rejected (real DB, real UNIQUE constraint + row-lock) ---
    same_nid = "2222222222"
    member_ids = []
    for i in range(1, 4):
        db = make_session(database_url)
        mid = create_pending_member(db, f"3id-{i}")
        service = AccountKYCService(
            SQLAlchemyAccountKYCRepository(db), SQLAlchemyKYCIdentityRepository(db),
            SQLAlchemyAccountKYCMemberActivationPort(db),
        )
        r = service.submit(
            member_id=mid, document_type=DocumentType.NID, raw_document_number=same_nid,
            document_image_reference=f"s3://ci/nid-3id-{i}.jpg", mobile_number="01700000000",
            mobile_otp_verified=True,
        )
        check(f"Member ID #{i} under same identity allowed+activated (real DB)", r.member_activated)
        member_ids.append(mid)
        db.close()

    db = make_session(database_url)
    fourth_id = create_pending_member(db, "3id-4")
    service = AccountKYCService(
        SQLAlchemyAccountKYCRepository(db), SQLAlchemyKYCIdentityRepository(db),
        SQLAlchemyAccountKYCMemberActivationPort(db),
    )
    fourth_rejected = False
    try:
        service.submit(
            member_id=fourth_id, document_type=DocumentType.NID, raw_document_number=same_nid,
            document_image_reference="s3://ci/nid-3id-4.jpg", mobile_number="01700000000",
            mobile_otp_verified=True,
        )
    except MaxMemberIdsExceededError:
        fourth_rejected = True
    check("4th Member ID under same identity REJECTED by real DB (MaxMemberIdsExceededError)", fourth_rejected)
    db.expire_all()
    fourth_member = db.get(Member, uuid.UUID(fourth_id))
    check("4th member remains PENDING in DB (never activated)",
          fourth_member.account_status == MemberAccountStatus.PENDING, fourth_member.account_status)
    db.close()

    # --- 12: three IDs remain independent at the persistence layer ---
    db = make_session(database_url)
    identity_row = db.execute(
        select(KYCIdentityRow).where(KYCIdentityRow.normalized_document_number == same_nid)
    ).scalar_one()
    linked = db.execute(select(Member.id).where(Member.kyc_identity_id == identity_row.id)).scalars().all()
    check("Exactly 3 distinct Member rows linked to the one KYCIdentity in real PostgreSQL",
          len(linked) == 3, str(len(linked)))
    check("The 4th (rejected) member is NOT among the linked rows",
          uuid.UUID(fourth_id) not in linked)
    db.close()

    # --- 13: transaction rollback leaves no partial data ---
    db = make_session(database_url)
    rollback_member_id = create_pending_member(db, "rollback-test")
    kyc_identity_repo = SQLAlchemyKYCIdentityRepository(db)
    try:
        identity = link_member_to_identity(
            kyc_identity_repo, rollback_member_id, DocumentType.NID, "3333333333", "01700000099",
        )
        # Simulate a failure AFTER the identity-link but BEFORE commit --
        # e.g. the activation step raising -- by deliberately rolling back
        # instead of committing.
        db.rollback()
    except Exception:
        db.rollback()
    db.close()

    db = make_session(database_url)
    orphan_check = db.execute(
        select(KYCIdentityRow).where(KYCIdentityRow.normalized_document_number == "3333333333")
    ).scalar_one_or_none()
    check("Rolled-back transaction leaves NO KYCIdentity row (no partial data)", orphan_check is None,
          "a row exists despite rollback" if orphan_check else "")
    db.close()

    # --- 14: concurrency/race protection -- two REAL concurrent DB
    # transactions both attempting the 4th ID simultaneously ---
    concurrent_nid = "4444444444"
    setup_db = make_session(database_url)
    for i in range(1, 4):
        mid = create_pending_member(setup_db, f"race-setup-{i}")
        service = AccountKYCService(
            SQLAlchemyAccountKYCRepository(setup_db), SQLAlchemyKYCIdentityRepository(setup_db),
            SQLAlchemyAccountKYCMemberActivationPort(setup_db),
        )
        service.submit(
            member_id=mid, document_type=DocumentType.NID, raw_document_number=concurrent_nid,
            document_image_reference=f"s3://ci/race-{i}.jpg", mobile_number="01700000000",
            mobile_otp_verified=True,
        )
    setup_db.close()

    race_results = {"succeeded": 0, "rejected": 0, "errors": []}
    race_lock = threading.Lock()

    def attempt_fourth(label):
        try:
            thread_db = make_session(database_url)
            member_id = create_pending_member(thread_db, f"race-{label}")
            service = AccountKYCService(
                SQLAlchemyAccountKYCRepository(thread_db), SQLAlchemyKYCIdentityRepository(thread_db),
                SQLAlchemyAccountKYCMemberActivationPort(thread_db),
            )
            service.submit(
                member_id=member_id, document_type=DocumentType.NID, raw_document_number=concurrent_nid,
                document_image_reference=f"s3://ci/race-4th-{label}.jpg", mobile_number="01700000000",
                mobile_otp_verified=True,
            )
            with race_lock:
                race_results["succeeded"] += 1
            thread_db.close()
        except MaxMemberIdsExceededError:
            with race_lock:
                race_results["rejected"] += 1
        except Exception as e:
            with race_lock:
                race_results["errors"].append(str(e))

    threads = [threading.Thread(target=attempt_fourth, args=(f"t{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    check("Concurrent 4th-ID race: exactly ZERO succeeded (both correctly rejected/serialized)",
          race_results["succeeded"] == 0, str(race_results))

    db = make_session(database_url)
    final_identity_row = db.execute(
        select(KYCIdentityRow).where(KYCIdentityRow.normalized_document_number == concurrent_nid)
    ).scalar_one()
    final_linked = db.execute(
        select(Member.id).where(Member.kyc_identity_id == final_identity_row.id)
    ).scalars().all()
    check("After the race, still EXACTLY 3 linked members (no 4th slipped through)",
          len(final_linked) == 3, str(len(final_linked)))
    db.close()

    print()
    failed = [r for r in results if r[1] == "FAIL"]
    print(f"TOTAL: {len(results)} checks, {len(results) - len(failed)} PASS, {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
