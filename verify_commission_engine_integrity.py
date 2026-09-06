#!/usr/bin/env python3
"""
CI Gate — Commission Engine Integrity Verification.

Fails the build (exit code 1) if ANY file under commission_engine_pinned/commission_engine/
does not match the checksum recorded in CHECKSUMS.lock. This is the mandatory
first CI step for the Y.G.L Office System (see Recommended Technical Stack §7,
§9): no commit may merge if this check fails.

This script performs READ-ONLY verification. It never modifies any Commission
Engine file. It does not run the 79-test suite (see run_commission_engine_tests.py
for that) -- it only verifies file integrity.
"""
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = REPO_ROOT / "commission_engine_pinned" / "commission_engine"
LOCK_FILE = REPO_ROOT / "commission_engine_pinned" / "CHECKSUMS.lock"


def load_lock_file(path: Path) -> dict:
    expected = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        checksum, filepath = line.split(None, 1)
        expected[filepath.strip()] = checksum.strip()
    return expected


def md5_of(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    if not LOCK_FILE.exists():
        print(f"FATAL: checksum lock file not found at {LOCK_FILE}")
        return 1
    if not ENGINE_DIR.exists():
        print(f"FATAL: Commission Engine directory not found at {ENGINE_DIR}")
        return 1

    expected = load_lock_file(LOCK_FILE)
    actual_files = sorted(ENGINE_DIR.glob("*.py"))

    errors = []

    # Every file that SHOULD exist per the lock file must exist with a matching checksum.
    for rel_path, expected_checksum in expected.items():
        full_path = REPO_ROOT / rel_path
        if not full_path.exists():
            errors.append(f"MISSING FILE (was in lock file, not found on disk): {rel_path}")
            continue
        actual_checksum = md5_of(full_path)
        if actual_checksum != expected_checksum:
            errors.append(
                f"CHECKSUM MISMATCH: {rel_path}\n"
                f"    expected: {expected_checksum}\n"
                f"    actual:   {actual_checksum}"
            )

    # No unexpected NEW .py files should appear in the pinned directory either --
    # any addition must go through an explicit lock-file update (a reviewed,
    # intentional Commission Engine version bump), not silently.
    expected_relpaths = set(expected.keys())
    for f in actual_files:
        rel = str(f.relative_to(REPO_ROOT))
        if rel not in expected_relpaths:
            errors.append(f"UNEXPECTED NEW FILE (not in lock file): {rel}")

    if errors:
        print("=" * 70)
        print("COMMISSION ENGINE INTEGRITY CHECK: FAILED")
        print("=" * 70)
        for e in errors:
            print(f"  - {e}")
        print()
        print("The Commission Engine's 18 frozen source files must remain")
        print("byte-for-byte unchanged. If this failure is unexpected, DO NOT")
        print("update CHECKSUMS.lock to make it pass -- investigate first.")
        return 1

    print(f"COMMISSION ENGINE INTEGRITY CHECK: PASSED ({len(expected)} files verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
