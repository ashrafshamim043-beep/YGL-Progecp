#!/usr/bin/env python3
"""
CI Gate — Commission Engine 79-Test Permanent Regression Gate.

Runs the full, unmodified Commission Engine test suite (79 tests) from the
pinned commission_engine_tests_backup_reference/ directory. This must be run
AFTER verify_commission_engine_integrity.py passes, and BEFORE any Office
System test. A failure here blocks the build unconditionally -- these tests
are never edited, skipped, or reasoned around.

Exit code 0 = all tests passed. Any other exit code = build must fail.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "commission_engine_pinned" / "commission_engine_tests_backup_reference"
ENGINE_PARENT_DIR = REPO_ROOT / "commission_engine_pinned"


def main() -> int:
    print("=" * 70)
    print("Running Commission Engine permanent regression gate (79 tests)")
    print("=" * 70)

    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover",
         "-s", str(TESTS_DIR), "-p", "test_*.py", "-v"],
        cwd=str(ENGINE_PARENT_DIR),
    )

    if result.returncode != 0:
        print()
        print("COMMISSION ENGINE 79-TEST GATE: FAILED")
        print("This is a permanent regression gate. Do NOT edit these tests")
        print("to make them pass. Investigate the root cause.")
        return 1

    print()
    print("COMMISSION ENGINE 79-TEST GATE: PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
