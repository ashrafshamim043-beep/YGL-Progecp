# Y.G.L (ওয়াইজিয়াল) — WORK LOG
তারিখ: ২ সেপ্টেম্বর, ২০২৬ | Status: **PERSISTENCE PHASE — NOT COMPLETE (Real PostgreSQL CI evidence pending)**

এই document ভবিষ্যতে যেকোনো নতুন session/continuity-এর জন্য single-source-of-truth।

---

## বর্তমান Implementation Status

Local sandbox-এ (network/PostgreSQL-বিহীন) সব logic **কোড করা ও 174/174 test দিয়ে verified**। কিন্তু **real PostgreSQL-এ এখনো কখনো চালানো হয়নি** — এই sandbox-এ network egress-allowlist `github.com` ও PostgreSQL install উভয়ই ব্লক করে (প্রমাণিত, exact error message সহ)। তাই:

- **PERSISTENCE PHASE = NOT COMPLETE**
- **Commerce module শুরু হয়নি, হবেও না যতক্ষণ না এই phase real CI-তে PASS প্রমাণিত হয়**

---

## Changed/New Files (এই ও আগের কয়েকটা session মিলিয়ে, সম্পূর্ণ তালিকা)

**Migration 0007 ও সংশ্লিষ্ট:**
- `backend/alembic/versions/0007_kyc_identity_and_account_kyc.py` (নতুন)
- `backend/app/modules/kyc_identity/{core,db_models,db_repository}.py` (নতুন)
- `backend/app/modules/account_kyc/{core,service,db_models,db_repository}.py` (নতুন)
- `backend/app/modules/member/db_models.py` (**additive change**: `kyc_identity_id` নতুন nullable column, existing কিছু পরিবর্তন হয়নি)
- `scripts/ci_verify_three_account_policy_db.py` (নতুন — real PostgreSQL+concurrency CI script)
- `scripts/verify_schema.py` (আপডেট — নতুন ২টা table + constraint যোগ)
- `.github/workflows/ci.yml` (আপডেট — migration downgrade/re-upgrade round-trip ধাপ + নতুন script call)

**Test files (নতুন):**
- `backend/tests/office_system/kyc_identity/test_kyc_identity.py` (১৪ test — max-3 policy + migration-consistency)
- `backend/tests/office_system/account_kyc/test_account_kyc.py` (৬ test)
- `backend/tests/office_system/withdrawal_kyc/` ও সংশ্লিষ্ট withdrawal eligibility logic
- `backend/tests/office_system/integration/test_three_account_policy_integration.py`

---

## Migration 0007 — বিস্তারিত

**তৈরি করে:** `kyc_identities` (unique: document_type+normalized_number), `account_kyc_records`, `members.kyc_identity_id` (nullable FK, **additive-only**)
**Upgrade ও Downgrade** — দুটোই লেখা, destructive না
**Max-3 enforcement** — application-layer (`link_member_to_identity`, row-lock সহ concurrency-safe design), DB-level trigger যোগ করা হয়নি (Technical-Lead-decision হিসেবে flagged)

---

## কোন Tests Locally Verified (এই sandbox-এ সত্যিই চালানো, evidence-সহ)

- ✅ ১৭৪/১৭৪ Office System test (in-memory/pure-logic/static-AST-based)
- ✅ Commission Engine ১৮/১৮ checksum, ৭৯/৭৯ test
- ✅ Import Boundary
- ✅ Auth(27)/KYC(24)/RBAC(19)/Audit(7)/Member(20)/Sponsor(12) — প্রতিটা individually re-confirmed
- ✅ Migration↔Model column-consistency (static/AST-based, sqlalchemy ছাড়াই)

## কোন Tests শুধু GitHub Actions-এ verify হওয়ার অপেক্ষায় (এখনো "NOT YET VERIFIED")

- ❌ Real PostgreSQL-এ NID/BirthReg persistence
- ❌ Real concurrency/row-lock (২টা সত্যিকারের সমান্তরাল request দিয়ে ৪র্থ-ID রেস-কন্ডিশন টেস্ট)
- ❌ Migration ০০৭ upgrade
- ❌ Migration downgrade
- ❌ Downgrade-এর পর আবার upgrade-to-head
- ❌ Transaction rollback (real DB)
- ❌ CI-এর সামগ্রিক ফলাফল

---

## Exact পরবর্তী ধাপ

1. **Manual GitHub push** (এই ZIP-এর নতুন/পরিবর্তিত ফাইলগুলো — উপরের তালিকা — GitHub repo-তে বসানো; Claude নিজে push করতে পারে না, network-allowlist-এ `github.com` ব্লকড, প্রমাণিত)
2. **GitHub Actions-এ real run** (Postgres+Redis service-container-সহ)
3. **Actions log/evidence Claude-কে দেওয়া** — তবেই Real PostgreSQL CI section-এর প্রতিটা item PASS/FAIL হিসেবে চূড়ান্ত করা যাবে
4. তারপরই Persistence Phase = COMPLETE ঘোষণা, এবং Commerce module শুরুর অনুমতি বিবেচনা

---

**Commission Engine-এর ১৮টা frozen file — পুরো এই কাজ জুড়ে একবারও স্পর্শ করা হয়নি।**
