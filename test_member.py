"""
Member module test suite. Zero external dependencies -- pure Python,
in-memory repositories, same pattern as Auth/KYC/RBAC test suites.
"""
import unittest

from app.modules.member.core import (
    MemberAccountStatus, InMemoryMemberRepository, InMemoryMemberStatusHistoryRepository,
    EmailAlreadyRegisteredError, InvalidCredentialsError, AccountNotActiveError,
    MemberNotFoundError, InvalidStatusTransitionError,
)
from app.modules.member.service import MemberService


def build_service():
    member_repo = InMemoryMemberRepository()
    status_repo = InMemoryMemberStatusHistoryRepository()
    service = MemberService(member_repo, status_repo)
    return service, member_repo, status_repo


class TestRegistration(unittest.TestCase):
    def test_new_member_starts_pending(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!", phone="01700000000")
        self.assertEqual(result.member.account_status, MemberAccountStatus.PENDING)
        self.assertNotIn("Password1!", result.member.password_hash)

    def test_no_status_history_row_on_registration(self):
        """PENDING is Office-System-only, never Engine-recognized -- no
        history row should exist until KYC creates the first one."""
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        self.assertEqual(status_repo.list_for_member(result.member.id), [])

    def test_duplicate_email_rejected(self):
        service, repo, status_repo = build_service()
        service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        with self.assertRaises(EmailAlreadyRegisteredError):
            service.register("Karim Two", "karim@ygl.example", "Password2!")

    def test_empty_password_rejected(self):
        service, repo, status_repo = build_service()
        with self.assertRaises(ValueError):
            service.register("Karim Rahman", "karim@ygl.example", "")


class TestLogin(unittest.TestCase):
    def test_login_blocked_while_pending(self):
        """A newly registered (PENDING, pre-KYC) member cannot log in yet."""
        service, repo, status_repo = build_service()
        service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        with self.assertRaises(AccountNotActiveError) as ctx:
            service.login("karim@ygl.example", "Password1!")
        self.assertEqual(ctx.exception.status, MemberAccountStatus.PENDING)

    def test_login_succeeds_once_active(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        # Simulate KYC approval directly setting ACTIVE (real activation
        # goes through YGLKYCService/SQLAlchemyMemberActivationPort; this
        # test only needs the resulting state).
        member = repo.get_by_id(result.member.id)
        member.account_status = MemberAccountStatus.ACTIVE
        repo.save(member)

        login_result = service.login("karim@ygl.example", "Password1!")
        self.assertEqual(login_result.identity.member_id, result.member.id)

    def test_wrong_password_rejected(self):
        service, repo, status_repo = build_service()
        service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        with self.assertRaises(InvalidCredentialsError):
            service.login("karim@ygl.example", "wrong-password")

    def test_unknown_email_raises_same_error_as_wrong_password(self):
        service, repo, status_repo = build_service()
        service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        with self.assertRaises(InvalidCredentialsError) as ctx1:
            service.login("nobody@ygl.example", "anything")
        with self.assertRaises(InvalidCredentialsError) as ctx2:
            service.login("karim@ygl.example", "wrong-password")
        self.assertEqual(str(ctx1.exception), str(ctx2.exception))

    def test_suspended_member_cannot_login(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        member = repo.get_by_id(result.member.id)
        member.account_status = MemberAccountStatus.ACTIVE
        repo.save(member)
        service.change_status(result.member.id, MemberAccountStatus.SUSPENDED, changed_by="admin-1")
        with self.assertRaises(AccountNotActiveError) as ctx:
            service.login("karim@ygl.example", "Password1!")
        self.assertEqual(ctx.exception.status, MemberAccountStatus.SUSPENDED)


class TestProfile(unittest.TestCase):
    def test_get_profile_returns_member(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        profile = service.get_profile(result.member.id)
        self.assertEqual(profile.full_name, "Karim Rahman")

    def test_get_profile_unknown_id_raises(self):
        service, repo, status_repo = build_service()
        with self.assertRaises(MemberNotFoundError):
            service.get_profile("nonexistent-id")

    def test_update_profile_changes_name_and_phone_only(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!", phone="01700000000")
        updated = service.update_profile(result.member.id, full_name="Karim R. Rahman", phone="01800000000")
        self.assertEqual(updated.full_name, "Karim R. Rahman")
        self.assertEqual(updated.phone, "01800000000")
        self.assertEqual(updated.email, "karim@ygl.example")  # unchanged, not editable here

    def test_update_profile_partial_update_leaves_other_field_unchanged(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!", phone="01700000000")
        updated = service.update_profile(result.member.id, full_name="New Name")
        self.assertEqual(updated.full_name, "New Name")
        self.assertEqual(updated.phone, "01700000000")  # untouched


class TestStatusManagement(unittest.TestCase):
    def test_active_to_suspended_allowed(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        member = repo.get_by_id(result.member.id)
        member.account_status = MemberAccountStatus.ACTIVE
        repo.save(member)

        updated = service.change_status(result.member.id, MemberAccountStatus.SUSPENDED,
                                         changed_by="admin-1", reason="policy violation")
        self.assertEqual(updated.account_status, MemberAccountStatus.SUSPENDED)

        history = status_repo.list_for_member(result.member.id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].from_status, MemberAccountStatus.ACTIVE)
        self.assertEqual(history[0].to_status, MemberAccountStatus.SUSPENDED)
        self.assertEqual(history[0].reason, "policy violation")

    def test_suspended_to_active_reinstatement_allowed(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        member = repo.get_by_id(result.member.id)
        member.account_status = MemberAccountStatus.SUSPENDED
        repo.save(member)

        updated = service.change_status(result.member.id, MemberAccountStatus.ACTIVE, changed_by="admin-1")
        self.assertEqual(updated.account_status, MemberAccountStatus.ACTIVE)

    def test_terminated_is_a_true_terminal_state(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        member = repo.get_by_id(result.member.id)
        member.account_status = MemberAccountStatus.TERMINATED
        repo.save(member)
        with self.assertRaises(InvalidStatusTransitionError):
            service.change_status(result.member.id, MemberAccountStatus.ACTIVE, changed_by="admin-1")

    def test_pending_to_active_via_change_status_is_blocked(self):
        """The critical guard: admin-driven change_status() must NEVER be
        usable as a KYC bypass. PENDING -> ACTIVE is exclusively
        YGLKYCService's job."""
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        with self.assertRaises(InvalidStatusTransitionError) as ctx:
            service.change_status(result.member.id, MemberAccountStatus.ACTIVE, changed_by="admin-1")
        self.assertIn("KYC", str(ctx.exception))

    def test_pending_to_terminated_allowed(self):
        """An admin can still reject/close a registration before KYC even
        starts -- this is a legitimate PENDING transition, unlike PENDING->ACTIVE."""
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        updated = service.change_status(result.member.id, MemberAccountStatus.TERMINATED, changed_by="admin-1")
        self.assertEqual(updated.account_status, MemberAccountStatus.TERMINATED)

    def test_change_status_unknown_member_raises(self):
        service, repo, status_repo = build_service()
        with self.assertRaises(MemberNotFoundError):
            service.change_status("nonexistent-id", MemberAccountStatus.SUSPENDED, changed_by="admin-1")

    def test_status_history_is_append_only_and_ordered(self):
        service, repo, status_repo = build_service()
        result = service.register("Karim Rahman", "karim@ygl.example", "Password1!")
        member = repo.get_by_id(result.member.id)
        member.account_status = MemberAccountStatus.ACTIVE
        repo.save(member)

        service.change_status(result.member.id, MemberAccountStatus.SUSPENDED, changed_by="admin-1")
        service.change_status(result.member.id, MemberAccountStatus.ACTIVE, changed_by="admin-2")

        history = status_repo.list_for_member(result.member.id)
        self.assertEqual(len(history), 2)
        self.assertEqual([h.to_status for h in history], [MemberAccountStatus.SUSPENDED, MemberAccountStatus.ACTIVE])


if __name__ == "__main__":
    unittest.main()


class TestStaticDbRepositoryInterfaceCompliance(unittest.TestCase):
    """[STATIC] SQLAlchemyMemberRepository/SQLAlchemyMemberStatusHistoryRepository
    implement every abstract method their interfaces require -- sqlalchemy
    is not importable in this sandbox, so this is AST-based, not a runtime import."""

    def test_sqlalchemy_member_repository_implements_all_abstract_methods(self):
        import ast
        import os
        path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..",
                             "app", "modules", "member", "db_repository.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        classes = {n.name: {m.name for m in n.body if isinstance(m, ast.FunctionDef)}
                   for n in tree.body if isinstance(n, ast.ClassDef)}
        self.assertEqual({"get_by_email", "get_by_id", "save"} - classes["SQLAlchemyMemberRepository"], set())
        self.assertEqual({"append", "list_for_member"} - classes["SQLAlchemyMemberStatusHistoryRepository"], set())
