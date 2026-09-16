"""
Commission Service -- the SOLE module in this repository permitted to import
commission_engine (see scripts/verify_import_boundary.py, enforced in CI).

Phase 8.1 scope note: this module is a placeholder in this sub-phase.
Real wiring (DB-backed adapters implementing IdempotencyStore, ImmutableLedger,
UndistributedFundTracker, UplineProvider, RankSnapshotProvider's public
method signatures -- per Specification Refinement v2 Section 1) is Sub-phase
8.3 work, not 8.1. It is scaffolded here only so the import-boundary and
checksum CI gates have something real to check from day one.

Nothing in this file modifies, wraps in a way that changes behavior, or
duplicates any Commission Engine business logic. It will only ever persist
what the Engine computes.
"""
import sys
from pathlib import Path

# The pinned Commission Engine lives outside the backend/ package (see repo
# root /commission_engine_pinned/), NOT inside backend/app/. In a real
# deployment this would instead be an installed, version-pinned package
# (see backend/pyproject.toml's comment on this) -- the path-based import
# here is a development-time convenience matching this repo's current layout.
_PINNED_ENGINE_ROOT = Path(__file__).resolve().parents[4] / "commission_engine_pinned"
if str(_PINNED_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PINNED_ENGINE_ROOT))

# Import-boundary check (scripts/verify_import_boundary.py) permits
# commission_engine imports only inside this directory. Do not move this
# import, and do not import commission_engine anywhere else in the codebase.
import commission_engine  # noqa: E402  (import after sys.path setup is intentional)

__all__ = ["commission_engine"]
