"""
Auth module test suite. Zero external dependencies (no FastAPI/SQLAlchemy/
argon2-cffi/pyotp/pyjwt) -- runs against pure-Python core.py + in-memory
repositories, exactly like commission_engine's and the KYC module's own
test suites.
"""
import time
import unittest
from datetime import datetime, timedelta, timezone

from app.modules.auth.core import (
    AdminAccount, InMemoryAdminRepository, InMemoryRefreshTokenRepository,
    InvalidCredentialsError, AccountLockedError, AccountDisabledError,
    MfaRequiredError, InvalidMfaCodeError, MfaNotEnrolledError,
    RefreshTokenInvalidError,
)
from app.modules.auth.service import AuthService, AuthConfig
from app.modules.auth.password_hashing import PasswordHasher, InvalidHashFormatError
from app.modules.auth.totp import generate_secret, generate_totp, verify_totp
from app.modules.auth.jwt_utils import encode as jwt_encode, decode as jwt_decode, InvalidTokenError

JWT_SECRET = "test-jwt-secret-do-not-use-in-production"


def build_service(login_failure_threshold=3, lockout_duration_seconds=1800):
    admin_repo = InMemoryAdminRepository()
    refresh_repo = InMemoryRefreshTokenRepository()
    config = AuthConfig(
        jwt_secret=JWT_SECRET,
        login_failure_threshold=login_failure_threshold,
        lockout_duration_seconds=lockout_duration_seconds,
    )
    service = AuthService(admin_repo, refresh_repo, config)
    return service, admin_repo, refresh_repo


def create_admin(admin_repo, hasher, email="admin@ygl.example", password="CorrectHorseBatteryStaple1!",
                  role_id="role-super-admin", admin_id="admin-1", mfa_enrolled=False, mfa_secret=None):
    account = AdminAccount(
        id=admin_id, email=email, password_hash=hasher.hash_password(password),
        role_id=role_id, status="ACTIVE", mfa_enrolled=mfa_enrolled, mfa_secret=mfa_secret,
    )
    admin_repo.save(account)
    return account


class TestValidLogin(unittest.TestCase):
    def test_valid_login_without_mfa_returns_tokens(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        result = service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")
        self.assertEqual(result.identity.email, "admin@ygl.example")
        self.assertTrue(result.access_token)
        self.assertTrue(result.refresh_token_id)


class TestInvalidPassword(unittest.TestCase):
    def test_wrong_password_rejected(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        with self.assertRaises(InvalidCredentialsError):
            service.login("admin@ygl.example", "totally-wrong-password")


class TestUnknownUser(unittest.TestCase):
    def test_unknown_email_raises_same_error_as_wrong_password(self):
        """Security requirement: must not reveal whether an email exists."""
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        with self.assertRaises(InvalidCredentialsError) as ctx1:
            service.login("nobody@ygl.example", "any-password")
        with self.assertRaises(InvalidCredentialsError) as ctx2:
            service.login("admin@ygl.example", "wrong-password")
        self.assertEqual(str(ctx1.exception), str(ctx2.exception))


class TestPasswordHashingVerification(unittest.TestCase):
    def test_hash_never_equals_plaintext_and_verifies_correctly(self):
        hasher = PasswordHasher(iterations=10_000)  # lower iterations for fast tests
        plaintext = "MySecurePassword123!"
        hashed = hasher.hash_password(plaintext)
        self.assertNotEqual(hashed, plaintext)
        self.assertNotIn(plaintext, hashed)
        self.assertTrue(hasher.verify_password(plaintext, hashed))
        self.assertFalse(hasher.verify_password("WrongPassword", hashed))

    def test_same_password_produces_different_hashes_due_to_salt(self):
        hasher = PasswordHasher(iterations=10_000)
        h1 = hasher.hash_password("SamePassword1!")
        h2 = hasher.hash_password("SamePassword1!")
        self.assertNotEqual(h1, h2)  # different random salts

    def test_corrupted_hash_format_raises_not_silently_passes(self):
        hasher = PasswordHasher()
        with self.assertRaises(InvalidHashFormatError):
            hasher.verify_password("anything", "not-a-valid-hash-format")


class TestJwtGeneration(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        token = jwt_encode({"admin_id": "a1", "role_id": "r1"}, JWT_SECRET, expires_in_seconds=300)
        payload = jwt_decode(token, JWT_SECRET)
        self.assertEqual(payload["admin_id"], "a1")
        self.assertIn("exp", payload)
        self.assertIn("iat", payload)


class TestExpiredInvalidJwt(unittest.TestCase):
    def test_expired_token_rejected(self):
        token = jwt_encode({"admin_id": "a1"}, JWT_SECRET, expires_in_seconds=-5)
        with self.assertRaises(InvalidTokenError):
            jwt_decode(token, JWT_SECRET)

    def test_wrong_secret_rejected(self):
        token = jwt_encode({"admin_id": "a1"}, JWT_SECRET, expires_in_seconds=300)
        with self.assertRaises(InvalidTokenError):
            jwt_decode(token, "a-completely-different-secret")

    def test_tampered_payload_rejected(self):
        token = jwt_encode({"admin_id": "a1", "role_id": "viewer"}, JWT_SECRET, expires_in_seconds=300)
        header, payload, sig = token.split(".")
        # attacker flips a character in the payload segment
        tampered_payload = payload[:-1] + ("A" if payload[-1] != "A" else "B")
        tampered_token = f"{header}.{tampered_payload}.{sig}"
        with self.assertRaises(InvalidTokenError):
            jwt_decode(tampered_token, JWT_SECRET)

    def test_malformed_token_structure_rejected(self):
        with self.assertRaises(InvalidTokenError):
            jwt_decode("not-a-valid-jwt-at-all", JWT_SECRET)


class TestRefreshToken(unittest.TestCase):
    def test_refresh_issues_new_access_token_and_rotates(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        login_result = service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")

        refreshed = service.refresh_access_token(login_result.refresh_token_id)
        self.assertNotEqual(refreshed.access_token, login_result.access_token)
        self.assertNotEqual(refreshed.refresh_token_id, login_result.refresh_token_id)


class TestRevokedRefreshToken(unittest.TestCase):
    def test_revoked_token_cannot_be_used(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        login_result = service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")

        service.revoke_refresh_token(login_result.refresh_token_id)
        with self.assertRaises(RefreshTokenInvalidError):
            service.refresh_access_token(login_result.refresh_token_id)

    def test_rotated_old_token_cannot_be_replayed(self):
        """Replay protection: once a refresh token has been used (rotated),
        the OLD token id must never work again, even though it wasn't
        explicitly revoked by the caller -- rotation itself revokes it."""
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        login_result = service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")

        service.refresh_access_token(login_result.refresh_token_id)  # first, legitimate use
        with self.assertRaises(RefreshTokenInvalidError):
            service.refresh_access_token(login_result.refresh_token_id)  # replay attempt


class TestMfaEnrollment(unittest.TestCase):
    def test_enrollment_requires_confirmation_before_activation(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher, mfa_enrolled=False)

        secret = service.enroll_mfa_start("admin-1")
        account = admin_repo.get_by_id("admin-1")
        self.assertFalse(account.mfa_enrolled)  # not active until confirmed

        code = generate_totp(secret)
        confirmed = service.confirm_mfa_enrollment("admin-1", code)
        self.assertTrue(confirmed)
        self.assertTrue(admin_repo.get_by_id("admin-1").mfa_enrolled)

    def test_wrong_code_does_not_activate_enrollment(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher, mfa_enrolled=False)
        service.enroll_mfa_start("admin-1")
        with self.assertRaises(InvalidMfaCodeError):
            service.confirm_mfa_enrollment("admin-1", "000000")
        self.assertFalse(admin_repo.get_by_id("admin-1").mfa_enrolled)


class TestValidTotp(unittest.TestCase):
    def test_valid_totp_completes_login(self):
        service, admin_repo, refresh_repo = build_service()
        secret = generate_secret()
        create_admin(admin_repo, service.password_hasher, mfa_enrolled=True, mfa_secret=secret)

        with self.assertRaises(MfaRequiredError) as ctx:
            service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")
        challenge_token = ctx.exception.mfa_challenge_token

        code = generate_totp(secret)
        result = service.verify_mfa(challenge_token, code)
        self.assertTrue(result.access_token)


class TestInvalidTotp(unittest.TestCase):
    def test_invalid_totp_rejected(self):
        service, admin_repo, refresh_repo = build_service()
        secret = generate_secret()
        create_admin(admin_repo, service.password_hasher, mfa_enrolled=True, mfa_secret=secret)
        with self.assertRaises(MfaRequiredError) as ctx:
            service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")
        with self.assertRaises(InvalidMfaCodeError):
            service.verify_mfa(ctx.exception.mfa_challenge_token, "000000")

    def test_totp_rfc6238_official_vector(self):
        """Cross-check against the official RFC 6238 Appendix B test vector."""
        import base64
        secret_b32 = base64.b32encode(b"12345678901234567890").decode()
        code = generate_totp(secret_b32, at_time=59)
        self.assertEqual(code, "287082")  # last 6 digits of RFC's published 94287082


class TestRepeatedFailedLoginLockout(unittest.TestCase):
    def test_account_locks_after_threshold_failed_attempts(self):
        service, admin_repo, refresh_repo = build_service(login_failure_threshold=3)
        create_admin(admin_repo, service.password_hasher)

        for _ in range(2):
            with self.assertRaises(InvalidCredentialsError):
                service.login("admin@ygl.example", "wrong-password")
        # third failure crosses the threshold and locks the account
        with self.assertRaises(InvalidCredentialsError):
            service.login("admin@ygl.example", "wrong-password")

        # even the CORRECT password is now rejected while locked
        with self.assertRaises(AccountLockedError):
            service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")

    def test_successful_login_resets_failure_counter(self):
        service, admin_repo, refresh_repo = build_service(login_failure_threshold=3)
        create_admin(admin_repo, service.password_hasher)
        with self.assertRaises(InvalidCredentialsError):
            service.login("admin@ygl.example", "wrong-password")
        service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")
        self.assertEqual(admin_repo.get_by_id("admin-1").failed_login_count, 0)


class TestDisabledAccount(unittest.TestCase):
    def test_disabled_account_rejected_even_with_correct_password(self):
        service, admin_repo, refresh_repo = build_service()
        account = create_admin(admin_repo, service.password_hasher)
        account.status = "DISABLED"
        admin_repo.save(account)
        with self.assertRaises(AccountDisabledError):
            service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")


class TestUnauthorizedAccess(unittest.TestCase):
    def test_get_identity_rejects_invalid_access_token(self):
        service, admin_repo, refresh_repo = build_service()
        with self.assertRaises(InvalidTokenError):
            service.get_identity_from_access_token("not-a-real-token")

    def test_get_identity_extracts_correct_identity_from_valid_token(self):
        service, admin_repo, refresh_repo = build_service()
        create_admin(admin_repo, service.password_hasher)
        result = service.login("admin@ygl.example", "CorrectHorseBatteryStaple1!")
        identity = service.get_identity_from_access_token(result.access_token)
        self.assertEqual(identity.admin_id, "admin-1")
        self.assertEqual(identity.role_id, "role-super-admin")


class TestSecretConfigValidation(unittest.TestCase):
    def test_empty_jwt_secret_refused_at_encode_time(self):
        with self.assertRaises(ValueError):
            jwt_encode({"a": 1}, "", expires_in_seconds=60)

    def test_empty_jwt_secret_refused_at_decode_time(self):
        token = jwt_encode({"a": 1}, "real-secret", expires_in_seconds=60)
        with self.assertRaises(ValueError):
            jwt_decode(token, "")

    def test_empty_password_refused(self):
        hasher = PasswordHasher()
        with self.assertRaises(ValueError):
            hasher.hash_password("")


if __name__ == "__main__":
    unittest.main()
