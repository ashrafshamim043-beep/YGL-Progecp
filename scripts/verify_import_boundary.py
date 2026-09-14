#!/usr/bin/env python3
"""
CI Gate — Commission Engine Import-Boundary Enforcement.

Scans backend/app/ for any `import commission_engine` or
`from commission_engine ...` statement OUTSIDE of
backend/app/services/commission_service/. This is the static-analysis
enforcement of the architecture principle: "Office System never calculates
commission" -- only the commission_service module is permitted to touch the
Commission Engine at all.

Fails (exit 1) if a violation is found.
"""
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "backend" / "app"
ALLOWED_DIR = APP_DIR / "services" / "commission_service"


def imports_commission_engine(pyfile: Path) -> bool:
    try:
        tree = ast.parse(pyfile.read_text())
    except SyntaxError as e:
        print(f"WARNING: could not parse {pyfile}: {e}")
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "commission_engine" or alias.name.startswith("commission_engine."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "commission_engine" or node.module.startswith("commission_engine.")):
                return True
    return False


def main() -> int:
    violations = []
    for pyfile in APP_DIR.rglob("*.py"):
        if ALLOWED_DIR in pyfile.parents or pyfile.parent == ALLOWED_DIR:
            continue
        if imports_commission_engine(pyfile):
            violations.append(pyfile.relative_to(REPO_ROOT))

    if violations:
        print("=" * 70)
        print("IMPORT-BOUNDARY CHECK: FAILED")
        print("=" * 70)
        print("Only backend/app/services/commission_service/ may import")
        print("commission_engine. Violations found in:")
        for v in violations:
            print(f"  - {v}")
        print()
        print("This is a hard architecture rule: Office System modules must")
        print("never calculate commission themselves, even indirectly by")
        print("importing Commission Engine internals from the wrong place.")
        return 1

    print("IMPORT-BOUNDARY CHECK: PASSED (no violations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
