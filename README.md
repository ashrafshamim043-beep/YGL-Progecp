# Y.G.L (ওয়াইজিয়াল) Office System — Your Global Link

Phase 8.1 (Foundation) scaffold. Built per:
- Y.G.L Phase 8 FINAL Architecture Blueprint v4
- Y.G.L Phase 8 FINAL DB & API Specification v4
- Y.G.L Specification v3 Corrections
- Y.G.L Recommended Technical Stack (Technical Lead confirmed)
- Y.G.L Phase 8.1 Implementation-Start Checklist

## Architecture Boundary (never violate this)

Commission Engine is the financial calculation core. This repository:
- Vendors it, unmodified, under `/commission_engine_pinned/` (checksum-locked).
- Only `backend/app/services/commission_service/` may import it.
- Never recalculates, bypasses, or duplicates its business logic.

Both are enforced in CI (`.github/workflows/ci.yml`):
1. `scripts/verify_commission_engine_integrity.py` — checksum check
2. `scripts/run_commission_engine_tests.py` — the permanent 79-test gate
3. `scripts/verify_import_boundary.py` — static import-boundary check

## What's built in this Phase 8.1 scaffold

- Repository structure (backend, frontend, CI, pinned Commission Engine)
- Pinned Commission Engine (18 source files + 79 tests), verified byte-for-byte
  identical to the frozen baseline
- CI gate scripts (checksum, 79-test, import-boundary) — all tested, all pass
- FastAPI app skeleton (`backend/app/main.py`) with health check
- SQLAlchemy ORM models for Phase 8.1 scope: Identity (admins/roles/permissions),
  Member (members/kyc_documents/member_status_history), Sponsor
  (sponsor_placements/placement_review_queue)
- Alembic migration `0001_phase_8_1_foundation` (NOT executed against any
  database — code only, ready for an operator to run against a provisioned
  dev database)
- Docker Compose (Postgres + Redis + backend) for local development
- CI workflow with the mandatory gate order

## What is NOT yet built (explicitly out of this scaffold's scope)

- Actual API endpoints/business logic for Identity, Member, KYC, Sponsor,
  Genealogy modules (routers are stubbed with commented-out registration
  in `main.py`)
- Auth implementation (JWT issuance, MFA enrollment, login endpoints)
- Frontend beyond a placeholder page
- Anything from Sub-phase 8.2 onward (Commerce, Commission Engine
  integration, Governance, Reporting, Security hardening)
- No database has been created; no migration has been executed

## Running the test suite

**The canonical, exact command (works from the repository root):**
```
python3 -m unittest discover -s tests -p "test_*.py" -v
```
This works because `app` and `tests` at the repo root are symlinks into
`backend/`. If your unzip tool does not preserve symlinks (this affects
some Windows unzip tools, not standard Linux/macOS `unzip`), run this
instead from inside `backend/`:
```
cd backend
python3 -m unittest discover -s tests -p "test_*.py" -v
```
Both invocations run the identical 24 Office System tests (KYC module, as
of this delivery) plus any future Office System tests added under
`backend/tests/office_system/`.

**Commission Engine's own 79-test permanent gate is separate and unaffected
by this** -- run via `python3 scripts/run_commission_engine_tests.py` from
the repo root, as documented above.

## Running locally (once dependencies are installed)

```
docker compose up postgres redis
cd backend && alembic upgrade head   # applies 0001_phase_8_1_foundation
docker compose up backend
```

## Commission Engine — frozen, do not modify

See `/commission_engine_pinned/CHECKSUMS.lock`. Any change to those 18 files
requires an explicit, reviewed Business Owner decision (as with Business
Decision 1 and 2 in this project's history) — never a routine code change.
