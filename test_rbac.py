"""
RBAC test suite. Zero external dependencies -- pure Python, in-memory
repositories, exactly like Auth's and KYC's own test suites.
"""
import unittest

from app.modules.auth.core import AdminAccount, InMemoryAdminRepository, InMemoryRefreshTokenRepository
from app.modules.auth.service import AuthService, AuthConfig
from app.modules.auth.jwt_utils import encode as jwt_encode

from app.modules.rbac.core import (
    Permission, RoleName, default_role_permission_repository,
    InMemoryRolePermissionRepository, UnauthenticatedError, PermissionDeniedError,
)
from app.modules.rbac.service import RBACService
from app.modules.kyc.core import InMemoryAuditLogWriter

JWT_SECRET = "test-rbac-secret"


def build_rbac_service():
    admin_repo = InMemoryAdminRepository()
    refresh_repo = InMemoryRefreshTokenRepository()
    auth_config = AuthConfig(jwt_secret=JWT_SECRET)
    auth_service = AuthService(admin_repo, refresh_repo, auth_config)
    role_perm_repo = default_role_permission_repository()
    audit_log = InMemoryAuditLogWriter()
    rbac_service = RBACService(auth_service, role_perm_repo, audit_log)
    return rbac_service, auth_service, admin_repo, audit_log


def create_admin_and_login(auth_service, admin_repo, role_id, admin_id="a1", email=None):
    email = email or f"{admin_id}@ygl.example"
    account = AdminAccount(
        id=admin_id, email=email,
        password_hash=auth_service.password_hasher.hash_password("Password1!"),
        role_id=role_id, status="ACTIVE",
    )
    admin_repo.save(account)
    result = auth_service.login(email, "Password1!")
    return result.access_token


class TestSuperAdminAllowed(unittest.TestCase):
    def test_super_admin_has_every_permission(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        token = create_admin_and_login(auth, admin_repo, RoleName.SUPER_ADMIN)
        for perm in Permission.ALL:
            identity = rbac.authorize(token, perm)
            self.assertEqual(identity.admin_id, "a1")


class TestFinanceAdminMatrix(unittest.TestCase):
    def setUp(self):
        self.rbac, self.auth, self.admin_repo, self.audit = build_rbac_service()
        self.token = create_admin_and_login(self.auth, self.admin_repo, RoleName.FINANCE_ADMIN)

    def test_allowed_permissions(self):
        for perm in [Permission.MEMBER_READ, Permission.COMMISSION_VIEW, Permission.COMMISSION_APPROVE,
                     Permission.LEDGER_VIEW, Permission.ADJUSTMENT_REQUEST, Permission.AUDIT_LOG_READ]:
            self.rbac.authorize(self.token, perm)  # must not raise

    def test_denied_permissions(self):
        for perm in [Permission.MEMBER_WRITE, Permission.ADJUSTMENT_APPROVE,
                     Permission.PLAN_VERSION_PUBLISH, Permission.KYC_REVIEW]:
            with self.assertRaises(PermissionDeniedError):
                self.rbac.authorize(self.token, perm)


class TestSupportAdminMatrix(unittest.TestCase):
    def setUp(self):
        self.rbac, self.auth, self.admin_repo, self.audit = build_rbac_service()
        self.token = create_admin_and_login(self.auth, self.admin_repo, RoleName.SUPPORT_ADMIN)

    def test_allowed_permissions(self):
        self.rbac.authorize(self.token, Permission.MEMBER_READ)
        self.rbac.authorize(self.token, Permission.MEMBER_WRITE)

    def test_denied_kyc_review_and_financial(self):
        for perm in [Permission.KYC_REVIEW, Permission.COMMISSION_APPROVE,
                     Permission.ADJUSTMENT_APPROVE, Permission.LEDGER_VIEW]:
            with self.assertRaises(PermissionDeniedError):
                self.rbac.authorize(self.token, perm)


class TestComplianceKycAdminMatrix(unittest.TestCase):
    def setUp(self):
        self.rbac, self.auth, self.admin_repo, self.audit = build_rbac_service()
        self.token = create_admin_and_login(self.auth, self.admin_repo, RoleName.COMPLIANCE_KYC_ADMIN)

    def test_allowed_permissions(self):
        self.rbac.authorize(self.token, Permission.MEMBER_READ)
        self.rbac.authorize(self.token, Permission.KYC_REVIEW)
        self.rbac.authorize(self.token, Permission.AUDIT_LOG_READ)

    def test_kyc_review_does_not_imply_financial_permission(self):
        """Direct test of the Business Owner's explicit directive: 'KYC
        review-এর সঙ্গে financial permission automatically দেওয়া যাবে না'."""
        for perm in [Permission.COMMISSION_APPROVE, Permission.COMMISSION_VIEW,
                     Permission.ADJUSTMENT_APPROVE, Permission.ADJUSTMENT_REQUEST,
                     Permission.LEDGER_VIEW, Permission.PLAN_VERSION_PUBLISH]:
            with self.assertRaises(PermissionDeniedError):
                self.rbac.authorize(self.token, perm)


class TestKycReviewOnlyAuthorizedRoles(unittest.TestCase):
    """Cross-cutting test: exactly which roles CAN and CANNOT perform
    kyc.review, per the Business Owner's Final Decision #1."""

    def test_kyc_review_permission_matches_business_owner_decision(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        expectations = {
            RoleName.SUPER_ADMIN: True,           # oversight/emergency authority
            RoleName.COMPLIANCE_KYC_ADMIN: True,
            RoleName.FINANCE_ADMIN: False,
            RoleName.SUPPORT_ADMIN: False,
        }
        for i, (role, should_allow) in enumerate(expectations.items()):
            token = create_admin_and_login(auth, admin_repo, role, admin_id=f"kyc-test-{i}")
            if should_allow:
                rbac.authorize(token, Permission.KYC_REVIEW)  # must not raise
            else:
                with self.assertRaises(PermissionDeniedError):
                    rbac.authorize(token, Permission.KYC_REVIEW)


class TestMissingInvalidExpiredToken(unittest.TestCase):
    def test_missing_token_is_unauthenticated(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        with self.assertRaises(UnauthenticatedError):
            rbac.authorize("", Permission.MEMBER_READ)

    def test_invalid_token_is_unauthenticated(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        with self.assertRaises(UnauthenticatedError):
            rbac.authorize("not-a-real-jwt", Permission.MEMBER_READ)

    def test_expired_token_is_unauthenticated(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        expired_token = jwt_encode(
            {"admin_id": "a1", "role_id": RoleName.SUPER_ADMIN, "email": "a@ygl.example"},
            JWT_SECRET, expires_in_seconds=-10,
        )
        with self.assertRaises(UnauthenticatedError):
            rbac.authorize(expired_token, Permission.MEMBER_READ)


class TestValidTokenMissingPermission(unittest.TestCase):
    def test_valid_token_but_no_permission_is_403_not_401(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        token = create_admin_and_login(auth, admin_repo, RoleName.SUPPORT_ADMIN)
        with self.assertRaises(PermissionDeniedError):
            rbac.authorize(token, Permission.COMMISSION_APPROVE)
        # explicitly NOT UnauthenticatedError -- the token itself is valid


class TestPermissionLookup(unittest.TestCase):
    def test_role_permission_lookup_resolves_correctly(self):
        repo = default_role_permission_repository()
        finance_perms = repo.get_permissions_for_role(RoleName.FINANCE_ADMIN)
        self.assertIn(Permission.COMMISSION_APPROVE, finance_perms)
        self.assertNotIn(Permission.KYC_REVIEW, finance_perms)

    def test_unknown_role_resolves_to_empty_permission_set_not_error(self):
        repo = default_role_permission_repository()
        perms = repo.get_permissions_for_role("SOME_UNKNOWN_ROLE")
        self.assertEqual(perms, set())


class TestDuplicatePermissionAssignment(unittest.TestCase):
    def test_granting_same_permission_twice_does_not_duplicate(self):
        repo = InMemoryRolePermissionRepository({RoleName.SUPPORT_ADMIN: {Permission.MEMBER_READ}})
        repo.grant_permission(RoleName.SUPPORT_ADMIN, Permission.MEMBER_READ)
        repo.grant_permission(RoleName.SUPPORT_ADMIN, Permission.MEMBER_READ)  # duplicate grant
        perms = repo.get_permissions_for_role(RoleName.SUPPORT_ADMIN)
        self.assertEqual(perms, {Permission.MEMBER_READ})  # still just one entry, set semantics

    def test_seed_repository_itself_has_no_duplicate_effect(self):
        """The module-level _ROLE_PERMISSIONS seed is built from set
        literals -- confirms no accidental duplicate-permission bugs in
        the seed data itself."""
        repo = default_role_permission_repository()
        for role in [RoleName.SUPER_ADMIN, RoleName.FINANCE_ADMIN, RoleName.SUPPORT_ADMIN,
                     RoleName.COMPLIANCE_KYC_ADMIN]:
            perms = repo.get_permissions_for_role(role)
            self.assertIsInstance(perms, set)  # a set cannot contain duplicates by construction


class TestAuditEventOnAllow(unittest.TestCase):
    def test_audit_event_emitted_on_successful_authorization(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        token = create_admin_and_login(auth, admin_repo, RoleName.SUPER_ADMIN)
        rbac.authorize(token, Permission.MEMBER_READ, resource="member-123")

        granted_events = [e for e in audit.entries if e["event_type"] == "RBAC_AUTHORIZATION_GRANTED"]
        self.assertEqual(len(granted_events), 1)
        payload = granted_events[0]["payload"]
        self.assertEqual(payload["actor_id"], "a1")
        self.assertEqual(payload["permission_checked"], Permission.MEMBER_READ)
        self.assertEqual(payload["resource"], "member-123")
        self.assertEqual(payload["result"], "ALLOW")


class TestAuditEventOnDeny(unittest.TestCase):
    def test_audit_event_emitted_on_permission_denied(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        token = create_admin_and_login(auth, admin_repo, RoleName.SUPPORT_ADMIN)
        with self.assertRaises(PermissionDeniedError):
            rbac.authorize(token, Permission.KYC_REVIEW)

        denied_events = [e for e in audit.entries if e["event_type"] == "RBAC_AUTHORIZATION_DENIED"]
        self.assertEqual(len(denied_events), 1)
        payload = denied_events[0]["payload"]
        self.assertEqual(payload["actor_id"], "a1")
        self.assertEqual(payload["reason"], "INSUFFICIENT_PERMISSION")
        self.assertEqual(payload["result"], "DENY")

    def test_audit_event_emitted_on_unauthenticated_denial(self):
        rbac, auth, admin_repo, audit = build_rbac_service()
        with self.assertRaises(UnauthenticatedError):
            rbac.authorize("garbage-token", Permission.MEMBER_READ)

        denied_events = [e for e in audit.entries if e["event_type"] == "RBAC_AUTHORIZATION_DENIED"]
        self.assertEqual(len(denied_events), 1)
        self.assertEqual(denied_events[0]["payload"]["reason"], "UNAUTHENTICATED")
        self.assertIsNone(denied_events[0]["payload"]["actor_id"])  # no identity available yet


if __name__ == "__main__":
    unittest.main()
