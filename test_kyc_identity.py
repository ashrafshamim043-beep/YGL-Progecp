"""
KYCIdentity test suite -- proves the 3-Account Policy's max-3-Member-IDs-
per-identity rule, exactly as specified.
"""
import unittest

from app.modules.kyc_identity.core import (
    DocumentType, InMemoryKYCIdentityRepository, link_member_to_identity,
    normalize_document_number, MaxMemberIdsExceededError, InvalidDocumentTypeError,
)

SAME_NID = "1234 5678 9012"


class TestDocumentNormalization(unittest.TestCase):
    def test_whitespace_and_dashes_normalized_identically(self):
        self.assertEqual(
            normalize_document_number(DocumentType.NID, "1234-5678-9012"),
            normalize_document_number(DocumentType.NID, "1234 5678 9012"),
        )

    def test_empty_document_number_rejected(self):
        with self.assertRaises(ValueError):
            normalize_document_number(DocumentType.NID, "")


class TestThreeIdPolicy(unittest.TestCase):
    def test_first_member_id_creates_new_identity(self):
        repo = InMemoryKYCIdentityRepository()
        identity = link_member_to_identity(repo, "mofiz-id-1", DocumentType.NID, SAME_NID, "01700000000")
        self.assertEqual(identity.linked_member_ids, ["mofiz-id-1"])

    def test_second_member_id_same_document_links_to_same_identity(self):
        repo = InMemoryKYCIdentityRepository()
        first = link_member_to_identity(repo, "mofiz-id-1", DocumentType.NID, SAME_NID, "01700000000")
        second = link_member_to_identity(repo, "mofiz-id-2", DocumentType.NID, SAME_NID, "01700000000")
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.linked_member_ids, ["mofiz-id-1", "mofiz-id-2"])

    def test_third_member_id_same_document_allowed(self):
        repo = InMemoryKYCIdentityRepository()
        link_member_to_identity(repo, "mofiz-id-1", DocumentType.NID, SAME_NID, "01700000000")
        link_member_to_identity(repo, "mofiz-id-2", DocumentType.NID, SAME_NID, "01700000000")
        third = link_member_to_identity(repo, "mofiz-id-3", DocumentType.NID, SAME_NID, "01700000000")
        self.assertEqual(third.linked_member_ids, ["mofiz-id-1", "mofiz-id-2", "mofiz-id-3"])

    def test_fourth_member_id_same_document_rejected(self):
        repo = InMemoryKYCIdentityRepository()
        link_member_to_identity(repo, "mofiz-id-1", DocumentType.NID, SAME_NID, "01700000000")
        link_member_to_identity(repo, "mofiz-id-2", DocumentType.NID, SAME_NID, "01700000000")
        link_member_to_identity(repo, "mofiz-id-3", DocumentType.NID, SAME_NID, "01700000000")
        with self.assertRaises(MaxMemberIdsExceededError):
            link_member_to_identity(repo, "mofiz-id-4", DocumentType.NID, SAME_NID, "01700000000")
        # Confirm the 4th was NOT silently appended
        identity = repo.get_by_document(DocumentType.NID, normalize_document_number(DocumentType.NID, SAME_NID))
        self.assertEqual(len(identity.linked_member_ids), 3)

    def test_re_submitting_same_member_id_is_idempotent_not_a_new_link(self):
        repo = InMemoryKYCIdentityRepository()
        link_member_to_identity(repo, "mofiz-id-1", DocumentType.NID, SAME_NID, "01700000000")
        link_member_to_identity(repo, "mofiz-id-2", DocumentType.NID, SAME_NID, "01700000000")
        link_member_to_identity(repo, "mofiz-id-3", DocumentType.NID, SAME_NID, "01700000000")
        # Re-submitting mofiz-id-1 again must NOT count as a 4th slot / must not raise.
        identity = link_member_to_identity(repo, "mofiz-id-1", DocumentType.NID, SAME_NID, "01700000000")
        self.assertEqual(len(identity.linked_member_ids), 3)

    def test_different_document_numbers_create_separate_identities(self):
        repo = InMemoryKYCIdentityRepository()
        identity_a = link_member_to_identity(repo, "person-a-id-1", DocumentType.NID, "1111111111", "01711111111")
        identity_b = link_member_to_identity(repo, "person-b-id-1", DocumentType.NID, "2222222222", "01722222222")
        self.assertNotEqual(identity_a.id, identity_b.id)

    def test_birth_registration_document_type_works_identically(self):
        repo = InMemoryKYCIdentityRepository()
        first = link_member_to_identity(repo, "member-1", DocumentType.BIRTH_REGISTRATION, "BR-123456", "01700000000")
        second = link_member_to_identity(repo, "member-2", DocumentType.BIRTH_REGISTRATION, "BR-123456", "01700000000")
        self.assertEqual(first.id, second.id)

    def test_nid_and_birth_registration_are_never_conflated(self):
        """A NID number and a Birth-Reg number that happen to normalize to
        the same digits must NOT be treated as the same identity --
        document_type is part of the identity key."""
        repo = InMemoryKYCIdentityRepository()
        nid_identity = link_member_to_identity(repo, "member-1", DocumentType.NID, "999999999", "01700000000")
        br_identity = link_member_to_identity(repo, "member-2", DocumentType.BIRTH_REGISTRATION, "999999999", "01700000000")
        self.assertNotEqual(nid_identity.id, br_identity.id)

    def test_invalid_document_type_rejected(self):
        repo = InMemoryKYCIdentityRepository()
        with self.assertRaises(InvalidDocumentTypeError):
            link_member_to_identity(repo, "member-1", "PASSPORT", "X123456", "01700000000")


if __name__ == "__main__":
    unittest.main()


class TestStaticMigrationConsistency(unittest.TestCase):
    """[STATIC] KYCIdentityRow/AccountKYCRow columns vs migration 0007 --
    same AST-based cross-check pattern used for every other module's
    DB-adapter (sqlalchemy is not importable in this sandbox)."""

    def test_kyc_identity_row_columns_match_migration_0007(self):
        import ast
        import os

        repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", ".."))
        model_path = os.path.join(repo_root, "backend", "app", "modules", "kyc_identity", "db_models.py")
        migration_path = os.path.join(repo_root, "backend", "alembic", "versions", "0007_kyc_identity_and_account_kyc.py")

        def get_columns(filepath, class_name):
            with open(filepath) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    return {n.target.id for n in node.body if isinstance(n, ast.AnnAssign)}
            return None

        def get_migration_columns(filepath, table_name):
            with open(filepath) as f:
                tree = ast.parse(f.read())
            cols = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_table":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == table_name:
                        for arg in node.args[1:]:
                            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "Column":
                                if arg.args and isinstance(arg.args[0], ast.Constant):
                                    cols.append(arg.args[0].value)
            return set(cols)

        model_cols = get_columns(model_path, "KYCIdentityRow")
        migration_cols = get_migration_columns(migration_path, "kyc_identities")
        self.assertEqual(model_cols, migration_cols)

    def test_account_kyc_row_columns_match_migration_0007(self):
        import ast
        import os

        repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", ".."))
        model_path = os.path.join(repo_root, "backend", "app", "modules", "account_kyc", "db_models.py")
        migration_path = os.path.join(repo_root, "backend", "alembic", "versions", "0007_kyc_identity_and_account_kyc.py")

        def get_columns(filepath, class_name):
            with open(filepath) as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    return {n.target.id for n in node.body if isinstance(n, ast.AnnAssign)}
            return None

        def get_migration_columns(filepath, table_name):
            with open(filepath) as f:
                tree = ast.parse(f.read())
            cols = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create_table":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == table_name:
                        for arg in node.args[1:]:
                            if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute) and arg.func.attr == "Column":
                                if arg.args and isinstance(arg.args[0], ast.Constant):
                                    cols.append(arg.args[0].value)
            return set(cols)

        model_cols = get_columns(model_path, "AccountKYCRow")
        migration_cols = get_migration_columns(migration_path, "account_kyc_records")
        self.assertEqual(model_cols, migration_cols)

    def test_members_kyc_identity_id_column_added_in_migration(self):
        """Confirms the ADDITIVE members.kyc_identity_id column is really
        present in the migration (not just the ORM model)."""
        import ast
        import os

        repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", ".."))
        migration_path = os.path.join(repo_root, "backend", "alembic", "versions", "0007_kyc_identity_and_account_kyc.py")
        with open(migration_path) as f:
            source = f.read()
        self.assertIn('op.add_column("members"', source)
        self.assertIn("kyc_identity_id", source)
