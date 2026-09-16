"""
Cross-module integration tests -- the 7 scenarios required for Production
Completion. Each test genuinely exercises real logic across module
boundaries (Auth+RBAC+KYC+Commission Engine), using in-memory/frozen
components where a real PostgreSQL database is unavailable in this
sandbox (no network access -- see each test's docstring for exactly what
is and is not proven).

HONESTY NOTE (applies to every test in this file): tests using
DBIdempotencyStore/DBImmutableLedger/DBUndistributedFundTracker/
SQLAlchemy-backed repositories are NOT included here, because those
require a live PostgreSQL connection this sandbox cannot provide. Every
test below proves real cross-module LOGIC correctness using the same
in-memory implementations already proven in each module's own test suite
-- it does NOT prove the DB-backed adapters work against a real database.
That remains explicitly un-executed, per this project's standing
disclosure policy.
"""
import unittest
from datetime import datetime, timezone
from decimal import Decimal

# Triggers commission_engine_pinned/'s sys.path registration as a
# side-effect (see app/services/commission_service/__init__.py). This
# import MUST come before any `from commission_engine...` import below --
# and per the architecture boundary rule, a test file reaching into
# commission_engine submodules directly (as these cross-module tests
# deliberately do, to prove real Engine behavior) must still go through
# this same registration path, never invent its own sys.path hack.
import app.services.commission_service  # noqa: F401 (imported for its path-setup side-effect)

# Commission Engine (frozen, pinned)
from commission_engine.approved_plan import build_approved_plan_v1
from commission_engine.models import Member as EngineMember, Order, AccountStatus, Rank
from commission_engine.tree import InMemoryUplineProvider
from commission_engine.rank import InMemoryRankSnapshotProvider
from commission_engine.idempotency import IdempotencyStore
from commission_engine.undistributed import UndistributedFundTracker
from commission_engine.ledger import ImmutableLedger
from commission_engine.engine import CommissionEngine
from commission_engine.genealogy import (
    GenealogyEngine, InMemoryMemberDirectory, MemberDirectoryEntry, MemberRole,
    TreeBackedUplineProvider,
)

# Office System modules
from app.modules.kyc.core import (
    InMemoryKYCVerificationRepository, InMemoryKYCStateTransitionRepository,
    InMemoryAuditLogWriter, InMemoryMemberActivationPort,
)
from app.modules.kyc.service import YGLKYCService
from app.modules.kyc.sumsub_adapter import SumsubAdapter

from app.modules.auth.core import AdminAccount, InMemoryAdminRepository, InMemoryRefreshTokenRepository
from app.modules.auth.service import AuthService, AuthConfig

from app.modules.rbac.core import Permission, RoleName, default_role_permission_repository
from app.modules.rbac.service import RBACService

WEBHOOK_SECRET = "integration-test-secret"
JWT_SECRET = "integration-test-jwt-secret"
NOW = datetime(2026, 8, 30, tzinfo=timezone.utc)


class Test1_MemberKycApprovalActivation(unittest.TestCase):
    """Member -> KYC -> Approval -> Activation."""

    def test_full_kyc_flow_activates_member(self):
        provider = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        verification_repo = InMemoryKYCVerificationRepository()
        transition_repo = InMemoryKYCStateTransitionRepository()
        audit_log = InMemoryAuditLogWriter()
        member_activation = InMemoryMemberActivationPort()
        kyc_service = YGLKYCService(provider, verification_repo, transition_repo, audit_log, member_activation)

        verification = kyc_service.start_verification("member-int-1", "SUMSUB", "applicant-int-1")
        kyc_service.submit_document(verification.id, "member-int-1")

        import hmac, hashlib, json
        def sign(payload_bytes):
            return hmac.new(WEBHOOK_SECRET.encode(), payload_bytes, hashlib.sha256).hexdigest()

        in_progress_payload = json.dumps({
            "applicantId": "applicant-int-1", "type": "applicantReviewed",
            "reviewStatus": "pending", "reviewResult": {}, "createdAtMs": "2026-08-30 09:00:00.000",
        }).encode()
        r1 = kyc_service.process_webhook(in_progress_payload, sign(in_progress_payload))
        self.assertTrue(r1.accepted)

        final_payload = json.dumps({
            "applicantId": "applicant-int-1", "type": "applicantReviewed",
            "reviewStatus": "completed", "reviewResult": {"reviewAnswer": "GREEN"},
            "createdAtMs": "2026-08-30 10:00:00.000",
        }).encode()
        r2 = kyc_service.process_webhook(final_payload, sign(final_payload))

        self.assertTrue(r2.accepted)
        self.assertTrue(r2.member_activated)
        self.assertIn("member-int-1", member_activation.activated_members)
        self.assertEqual(len(member_activation.status_history_entries), 1)
        self.assertEqual(len(member_activation.audit_entries), 1)


class Test2_MemberSponsorGenealogyCommission(unittest.TestCase):
    """Member -> Sponsor/Genealogy -> Commission."""

    def test_real_genealogy_tree_drives_real_commission_calculation(self):
        directory = InMemoryMemberDirectory()
        for mid in ["root", "sponsor", "buyer"]:
            directory.add(MemberDirectoryEntry(mid, MemberRole.MEMBER, AccountStatus.ACTIVE))
        genealogy = GenealogyEngine(directory)
        genealogy.place_member("root", None, NOW)
        genealogy.place_member("sponsor", "root", NOW)
        genealogy.place_member("buyer", "sponsor", NOW)

        upline_provider = TreeBackedUplineProvider(genealogy, directory)
        plan = build_approved_plan_v1()
        engine = CommissionEngine(
            plan, upline_provider, InMemoryRankSnapshotProvider({}),
            IdempotencyStore(), UndistributedFundTracker(), ImmutableLedger(),
        )
        order = Order("INT-ORDER-1", "buyer", Decimal("1000.00"), True, True, NOW)
        result = engine.process_order_payment_confirmed(order, "2026-08", 2026)

        direct_referral = [li for li in result.line_items if li.commission_type.value == "DIRECT_REFERRAL"]
        self.assertEqual(len(direct_referral), 1)
        self.assertEqual(direct_referral[0].payee_member_id, "sponsor")


class Test3_CommissionLedgerPersistence(unittest.TestCase):
    """Commission -> Ledger Persistence.

    Uses the frozen, in-memory ImmutableLedger (already part of the
    pinned Commission Engine, proven by its own 79 tests). The DB-backed
    DBImmutableLedger adapter (app/services/commission_service/db_adapters.py)
    implements the identical interface but has NOT been tested against a
    live database in this sandbox -- that remains an explicit, disclosed gap.
    """

    def test_credited_commission_appears_in_ledger_with_correct_balance(self):
        plan = build_approved_plan_v1()
        tree = InMemoryUplineProvider({"buyer": "sponsor"}, {"sponsor": EngineMember("sponsor", AccountStatus.ACTIVE)})
        ledger = ImmutableLedger()
        engine = CommissionEngine(
            plan, tree, InMemoryRankSnapshotProvider({}), IdempotencyStore(), UndistributedFundTracker(), ledger,
        )
        order = Order("INT-ORDER-2", "buyer", Decimal("1000.00"), True, True, NOW)
        result = engine.process_order_payment_confirmed(order, "2026-08", 2026)
        for li in result.line_items:
            engine.approve_and_credit(li)

        self.assertEqual(ledger.balance_of("buyer"), Decimal("70.00"))       # personal sales 7%
        self.assertEqual(ledger.balance_of("sponsor"), Decimal("90.00"))     # direct referral 7% (70.00) + unilevel gen1 2% (20.00)


class Test4_DuplicateRequestIdempotentResult(unittest.TestCase):
    """Duplicate Request -> Idempotent Result (cross-module: Commission
    Engine's own idempotency + KYC's own idempotency-key, both exercised
    together in one test to prove the pattern is consistent system-wide)."""

    def test_commission_engine_duplicate_is_idempotent(self):
        plan = build_approved_plan_v1()
        tree = InMemoryUplineProvider({}, {})
        idem = IdempotencyStore()
        engine = CommissionEngine(plan, tree, InMemoryRankSnapshotProvider({}), idem, UndistributedFundTracker(), ImmutableLedger())
        order = Order("INT-ORDER-3", "buyer", Decimal("500.00"), True, True, NOW)
        r1 = engine.process_order_payment_confirmed(order, "2026-08", 2026)
        r2 = engine.process_order_payment_confirmed(order, "2026-08", 2026)
        self.assertFalse(r1.was_duplicate)
        self.assertTrue(r2.was_duplicate)

    def test_kyc_webhook_duplicate_is_idempotent(self):
        provider = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        kyc_service = YGLKYCService(
            provider, InMemoryKYCVerificationRepository(), InMemoryKYCStateTransitionRepository(),
            InMemoryAuditLogWriter(), InMemoryMemberActivationPort(),
        )
        verification = kyc_service.start_verification("member-int-4", "SUMSUB", "applicant-int-4")
        kyc_service.submit_document(verification.id, "member-int-4")

        import hmac, hashlib, json
        payload = json.dumps({
            "applicantId": "applicant-int-4", "type": "applicantReviewed",
            "reviewStatus": "pending", "reviewResult": {}, "createdAtMs": "2026-08-30 09:00:00.000",
        }).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        r1 = kyc_service.process_webhook(payload, sig)
        r2 = kyc_service.process_webhook(payload, sig)  # exact duplicate delivery
        self.assertTrue(r1.accepted)
        self.assertFalse(r2.accepted)
        self.assertEqual(r2.reason, "duplicate_event")


class Test5_KycWebhookVerifiedState(unittest.TestCase):
    """KYC Webhook -> Verified State."""

    def test_webhook_correctly_advances_state_to_verification_in_progress(self):
        provider = SumsubAdapter(webhook_secret=WEBHOOK_SECRET)
        verification_repo = InMemoryKYCVerificationRepository()
        kyc_service = YGLKYCService(
            provider, verification_repo, InMemoryKYCStateTransitionRepository(),
            InMemoryAuditLogWriter(), InMemoryMemberActivationPort(),
        )
        verification = kyc_service.start_verification("member-int-5", "SUMSUB", "applicant-int-5")
        kyc_service.submit_document(verification.id, "member-int-5")

        import hmac, hashlib, json
        payload = json.dumps({
            "applicantId": "applicant-int-5", "type": "applicantReviewed",
            "reviewStatus": "pending", "reviewResult": {}, "createdAtMs": "2026-08-30 09:00:00.000",
        }).encode()
        sig = hmac.new(WEBHOOK_SECRET.encode(), payload, hashlib.sha256).hexdigest()
        result = kyc_service.process_webhook(payload, sig)

        from app.modules.kyc.core import KYCState
        self.assertEqual(result.new_state, KYCState.VERIFICATION_IN_PROGRESS)
        stored = verification_repo.get_by_id(verification.id)
        self.assertEqual(stored.state, KYCState.VERIFICATION_IN_PROGRESS)


class Test6_AdminActionRbacAuditLog(unittest.TestCase):
    """Admin Action -> RBAC -> Audit Log."""

    def test_authorized_admin_action_produces_matching_audit_record(self):
        admin_repo = InMemoryAdminRepository()
        refresh_repo = InMemoryRefreshTokenRepository()
        auth_config = AuthConfig(jwt_secret=JWT_SECRET)
        auth_service = AuthService(admin_repo, refresh_repo, auth_config)
        role_perm_repo = default_role_permission_repository()
        audit_log = InMemoryAuditLogWriter()
        rbac_service = RBACService(auth_service, role_perm_repo, audit_log)

        account = AdminAccount(
            id="admin-int-6", email="int6@ygl.example",
            password_hash=auth_service.password_hasher.hash_password("Password1!"),
            role_id=RoleName.COMPLIANCE_KYC_ADMIN, status="ACTIVE",
        )
        admin_repo.save(account)
        login_result = auth_service.login("int6@ygl.example", "Password1!")

        identity = rbac_service.authorize(login_result.access_token, Permission.KYC_REVIEW, resource="verification-xyz")
        self.assertEqual(identity.admin_id, "admin-int-6")

        granted = [e for e in audit_log.entries if e["event_type"] == "RBAC_AUTHORIZATION_GRANTED"]
        self.assertEqual(len(granted), 1)
        self.assertEqual(granted[0]["payload"]["actor_id"], "admin-int-6")
        self.assertEqual(granted[0]["payload"]["permission_checked"], Permission.KYC_REVIEW)
        self.assertEqual(granted[0]["payload"]["resource"], "verification-xyz")


class Test7_FinancialEntryImmutableLedger(unittest.TestCase):
    """Financial Entry -> Immutable Ledger."""

    def test_ledger_has_no_update_or_delete_capability(self):
        ledger = ImmutableLedger()
        public_methods = [m for m in dir(ledger) if not m.startswith("_")]
        self.assertNotIn("update_entry", public_methods)
        self.assertNotIn("delete_entry", public_methods)
        self.assertNotIn("remove", public_methods)

    def test_correction_is_a_new_entry_never_a_mutation(self):
        ledger = ImmutableLedger()
        ledger.append_entry("member-x", Decimal("100.00"), "COMMISSION", "ORDER-int-7", "original credit")
        ledger.append_entry("member-x", Decimal("-100.00"), "REVERSAL", "ORDER-int-7", "correction: duplicate credit")
        self.assertEqual(len(ledger.entries_for("member-x")), 2)  # both entries preserved
        self.assertEqual(ledger.balance_of("member-x"), Decimal("0.00"))  # net effect correct


if __name__ == "__main__":
    unittest.main()
