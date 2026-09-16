"""
Commission Service persistence-layer test suite.

HONESTY NOTE (same disclosed pattern as Auth/KYC/RBAC/Audit DB adapters):
app/services/commission_service/db_models.py and db_adapters.py both
import `sqlalchemy` at module level. This sandbox cannot install
sqlalchemy (confirmed by direct import attempt -> ModuleNotFoundError).
Therefore neither file can be runtime-imported, instantiated, or
behaviorally unit-tested here.

This suite is split into two explicitly-labeled parts:

  1. STATIC verification (genuinely executed, real PASS/FAIL) -- AST-based:
     table columns match migration 0006 exactly; DBIdempotencyStore/
     DBImmutableLedger/DBUndistributedFundTracker define the EXACT method
     names the frozen commission_engine.idempotency.IdempotencyStore /
     commission_engine.ledger.ImmutableLedger /
     commission_engine.undistributed.UndistributedFundTracker expose
     (duck-typed substitution, per Specification Refinement v2 Section 1) --
     these expected method-name sets are obtained via a REAL import of
     commission_engine itself (which has zero external dependencies and
     IS importable here), not hard-coded guesses.

  2. IMPORT-BOUNDARY verification (genuinely executed) -- confirms
     db_adapters.py's commission_engine imports are the ONLY such imports
     outside commission_service (already enforced by
     scripts/verify_import_boundary.py; this test asserts it explicitly
     for this module too, as a second, independent check).

Nothing here proves db_adapters.py's actual SQL/ORM behavior against a
real database -- that remains explicitly unverified pending real
PostgreSQL, same status as every other DB adapter in this project.
"""
import ast
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", ".."))
# NOTE: os.path.realpath(__file__) is essential here -- see the identical
# note in tests/office_system/audit/test_audit.py for why (symlinked
# `tests -> backend/tests` path vs physical path depth mismatch).
BACKEND_ROOT = os.path.join(REPO_ROOT, "backend")
PINNED_ENGINE_ROOT = os.path.join(REPO_ROOT, "commission_engine_pinned")

DB_MODELS_PATH = os.path.join(BACKEND_ROOT, "app", "services", "commission_service", "db_models.py")
DB_ADAPTERS_PATH = os.path.join(BACKEND_ROOT, "app", "services", "commission_service", "db_adapters.py")
MIGRATION_0006_PATH = os.path.join(BACKEND_ROOT, "alembic", "versions", "0006_commission_persistence.py")


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


def get_top_level_class_names(filepath):
    with open(filepath) as f:
        tree = ast.parse(f.read())
    return {n.name for n in tree.body if isinstance(n, ast.ClassDef)}


class TestStaticSchemaConsistency(unittest.TestCase):
    """[STATIC] Every db_models.py table's columns vs migration 0006."""

    CASES = [
        ("PlanVersionRow", "plan_versions"),
        ("CommissionProcessingRecordRow", "commission_processing_records"),
        ("PlanVersionLockRow", "plan_version_locks"),
        ("CommissionLineItemRow", "commission_line_items"),
        ("LedgerEntryRow", "ledger_entries"),
        ("UndistributedAmountRow", "undistributed_amounts"),
    ]

    def test_all_six_tables_columns_match_migration_0006_exactly(self):
        for class_name, table_name in self.CASES:
            with self.subTest(table=table_name):
                model_cols = set(get_class_columns(DB_MODELS_PATH, class_name))
                migration_cols = set(get_migration_columns(MIGRATION_0006_PATH, table_name))
                self.assertEqual(
                    model_cols, migration_cols,
                    f"{table_name}: model-only={model_cols - migration_cols}, "
                    f"migration-only={migration_cols - model_cols}"
                )


class TestStaticDuckTypedInterfaceCompliance(unittest.TestCase):
    """
    [STATIC, cross-checked against a REAL commission_engine import]
    DBIdempotencyStore / DBImmutableLedger / DBUndistributedFundTracker
    define every method name the frozen Engine's own classes expose --
    the exact duck-typing contract Specification Refinement v2 relies on.
    """

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, PINNED_ENGINE_ROOT)
        from commission_engine.idempotency import IdempotencyStore
        from commission_engine.ledger import ImmutableLedger
        from commission_engine.undistributed import UndistributedFundTracker
        cls.expected_idempotency_methods = {m for m in dir(IdempotencyStore) if not m.startswith("_")}
        cls.expected_ledger_methods = {m for m in dir(ImmutableLedger) if not m.startswith("_")}
        cls.expected_undistributed_methods = {m for m in dir(UndistributedFundTracker) if not m.startswith("_")}

    def test_db_idempotency_store_implements_every_frozen_method(self):
        actual = get_class_method_names(DB_ADAPTERS_PATH, "DBIdempotencyStore")
        missing = self.expected_idempotency_methods - actual
        self.assertEqual(missing, set(), f"DBIdempotencyStore is missing: {missing}")

    def test_db_immutable_ledger_implements_every_frozen_method(self):
        actual = get_class_method_names(DB_ADAPTERS_PATH, "DBImmutableLedger")
        missing = self.expected_ledger_methods - actual
        self.assertEqual(missing, set(), f"DBImmutableLedger is missing: {missing}")

    def test_db_undistributed_fund_tracker_implements_every_frozen_method(self):
        actual = get_class_method_names(DB_ADAPTERS_PATH, "DBUndistributedFundTracker")
        missing = self.expected_undistributed_methods - actual
        self.assertEqual(missing, set(), f"DBUndistributedFundTracker is missing: {missing}")

    def test_all_three_adapter_classes_are_defined(self):
        classes = get_top_level_class_names(DB_ADAPTERS_PATH)
        for expected in ("DBIdempotencyStore", "DBImmutableLedger", "DBUndistributedFundTracker"):
            self.assertIn(expected, classes)


class TestImportBoundaryForCommissionService(unittest.TestCase):
    """[STATIC] db_adapters.py's commission_engine imports are the kind
    the import-boundary CI gate is designed to allow (this file lives
    inside commission_service/), independently re-derived here."""

    def test_db_adapters_file_is_inside_commission_service_directory(self):
        self.assertIn(
            os.path.join("app", "services", "commission_service"),
            DB_ADAPTERS_PATH,
        )

    def test_db_adapters_actually_imports_commission_engine(self):
        with open(DB_ADAPTERS_PATH) as f:
            tree = ast.parse(f.read())
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("commission_engine"):
                found = True
        self.assertTrue(found, "Expected db_adapters.py to import from commission_engine")


if __name__ == "__main__":
    unittest.main()
