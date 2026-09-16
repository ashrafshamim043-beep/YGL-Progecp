"""
Audit module test suite.

HONESTY NOTE (read before interpreting results): app/modules/audit/db_repository.py
imports `sqlalchemy` at module level (for AuditLogRow's ORM mapping). This
sandbox has no network access and cannot install sqlalchemy -- confirmed by
directly attempting the import, which raises ModuleNotFoundError. This means
SQLAlchemyAuditLogWriter CANNOT be runtime-imported, instantiated, or
behaviorally unit-tested in this environment, at all.

This test file is therefore split into two explicitly-labeled parts:

  1. STATIC verification (genuinely executed here, real assertions, real
     PASS/FAIL) -- AST-based structural checks: does AuditLogRow's column
     set match migration 0004 exactly, does SQLAlchemyAuditLogWriter
     implement the AuditLogWriter interface's write() method, do all
     modules that depend on it (kyc/router.py, member_activation_adapter.py)
     reference names that actually exist.

  2. BEHAVIORAL CONTRACT verification (via the interface's IN-MEMORY
     implementation) -- AuditLogWriter (the abstract interface
     SQLAlchemyAuditLogWriter implements) is already exercised by
     InMemoryAuditLogWriter across all 79 existing Office System tests
     (Auth/KYC/RBAC). This proves the CONTRACT (write(event_type, payload)
     accepts arbitrary data, multiple writes accumulate, etc.) is sound.
     It does NOT prove SQLAlchemyAuditLogWriter's actual SQL/ORM behavior
     against a real database -- that remains unverified pending real
     PostgreSQL (same disclosed status as every other DB adapter in this
     project).
"""
import ast
import os
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", ".."))
# NOTE: os.path.realpath(__file__) is essential here, not just __file__ --
# this test file may be reached via the repo-root `tests -> backend/tests`
# symlink (the documented canonical test command). A raw __file__ then
# reflects the SYMLINKED path, which is one directory level shallower than
# the physical path, so a fixed four-".." traversal overshoots REPO_ROOT
# by one level. realpath() resolves to the true physical location first,
# making the traversal depth correct regardless of how the file was reached.
AUDIT_DB_REPO_PATH = os.path.join(REPO_ROOT, "backend", "app", "modules", "audit", "db_repository.py")
MIGRATION_0004_PATH = os.path.join(
    REPO_ROOT, "backend", "alembic", "versions", "0004_audit_log.py"
)
KYC_ROUTER_PATH = os.path.join(REPO_ROOT, "backend", "app", "modules", "kyc", "router.py")
MEMBER_ACTIVATION_ADAPTER_PATH = os.path.join(
    REPO_ROOT, "backend", "app", "modules", "kyc", "member_activation_adapter.py"
)


def get_class_columns(filepath, class_name):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            cols = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    cols.append(item.target.id)
            return cols
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
    return cols


def get_class_method_names(filepath, class_name):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {n.name for n in node.body if isinstance(n, ast.FunctionDef)}
    return None


def get_imported_names_from(filepath, module_substring):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and module_substring in node.module:
            for alias in node.names:
                names.append((node.module, alias.name))
    return names


def get_defined_names(filepath):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
    return names


class TestStaticSchemaConsistency(unittest.TestCase):
    """[STATIC] AuditLogRow ORM model columns vs migration 0004 columns."""

    def test_auditlogrow_columns_match_migration_0004_exactly(self):
        model_cols = set(get_class_columns(AUDIT_DB_REPO_PATH, "AuditLogRow"))
        migration_cols = set(get_migration_columns(MIGRATION_0004_PATH, "audit_logs"))
        self.assertEqual(model_cols, migration_cols,
                          f"Mismatch -- model only: {model_cols - migration_cols}, "
                          f"migration only: {migration_cols - model_cols}")


class TestStaticInterfaceCompliance(unittest.TestCase):
    """[STATIC] SQLAlchemyAuditLogWriter implements the AuditLogWriter
    interface's required method (write)."""

    def test_sqlalchemy_audit_log_writer_defines_write_method(self):
        methods = get_class_method_names(AUDIT_DB_REPO_PATH, "SQLAlchemyAuditLogWriter")
        self.assertIn("write", methods)

    def test_sqlalchemy_audit_log_writer_defines_init(self):
        methods = get_class_method_names(AUDIT_DB_REPO_PATH, "SQLAlchemyAuditLogWriter")
        self.assertIn("__init__", methods)


class TestStaticDependencyResolution(unittest.TestCase):
    """[STATIC] Every module that imports from audit.db_repository
    references a name that genuinely exists there."""

    def test_kyc_router_audit_import_resolves(self):
        imports = get_imported_names_from(KYC_ROUTER_PATH, "audit.db_repository")
        defined = get_defined_names(AUDIT_DB_REPO_PATH)
        missing = [(m, n) for m, n in imports if n not in defined]
        self.assertTrue(imports, "Expected kyc/router.py to import from audit.db_repository")
        self.assertEqual(missing, [], f"Unresolved imports in kyc/router.py: {missing}")

    def test_member_activation_adapter_audit_import_resolves(self):
        imports = get_imported_names_from(MEMBER_ACTIVATION_ADAPTER_PATH, "audit.db_repository")
        defined = get_defined_names(AUDIT_DB_REPO_PATH)
        missing = [(m, n) for m, n in imports if n not in defined]
        self.assertTrue(imports, "Expected member_activation_adapter.py to import from audit.db_repository")
        self.assertEqual(missing, [], f"Unresolved imports in member_activation_adapter.py: {missing}")


class TestBehavioralContractViaInMemoryImplementation(unittest.TestCase):
    """
    [BEHAVIORAL CONTRACT, via InMemoryAuditLogWriter -- NOT
    SQLAlchemyAuditLogWriter itself] Proves the AuditLogWriter interface
    contract that SQLAlchemyAuditLogWriter also promises to implement:
    write() accepts an event_type string + arbitrary payload dict, and
    multiple writes accumulate without overwriting each other.
    """

    def test_write_accepts_event_type_and_arbitrary_payload(self):
        import sys
        sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
        from app.modules.kyc.core import InMemoryAuditLogWriter

        writer = InMemoryAuditLogWriter()
        writer.write("TEST_EVENT", {"actor_id": "a1", "action": "TEST", "result": "SUCCESS"})
        self.assertEqual(len(writer.entries), 1)
        self.assertEqual(writer.entries[0]["event_type"], "TEST_EVENT")
        self.assertEqual(writer.entries[0]["payload"]["actor_id"], "a1")

    def test_multiple_writes_accumulate_independently(self):
        import sys
        sys.path.insert(0, os.path.join(REPO_ROOT, "backend"))
        from app.modules.kyc.core import InMemoryAuditLogWriter

        writer = InMemoryAuditLogWriter()
        writer.write("EVENT_A", {"n": 1})
        writer.write("EVENT_B", {"n": 2})
        writer.write("EVENT_A", {"n": 3})
        self.assertEqual(len(writer.entries), 3)
        self.assertEqual([e["payload"]["n"] for e in writer.entries], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
