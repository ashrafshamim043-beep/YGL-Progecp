# Y.G.L — Production Verification Environment Requirement

## এই sandbox-এ কী নেই (definitively re-confirmed, ৩০/০৮/২০২৬)

```
$ which psql postgres pg_ctl        -> কিছুই পাওয়া যায়নি
$ pip install psycopg2-binary       -> "No matching distribution found" (কোনো package index পৌঁছানো যায় না)
$ socket.connect(('8.8.8.8', 53))   -> timed out (raw network-level সংযোগও নেই)
```

অর্থাৎ এটা শুধু "PostgreSQL installed নেই" না — এই sandbox-এর কোনো বাইরের নেটওয়ার্ক সংযোগই নেই। কোনো pip install, apt install, বা কোনো external service-এ সংযোগ — কোনোটাই সম্ভব না।

## Real verification-এর জন্য যা প্রয়োজন

| উপাদান | ন্যূনতম প্রয়োজনীয়তা |
|---|---|
| PostgreSQL | ১৬.x, fresh/empty database |
| Python | ৩.১২, সাথে pip install -e ".[dev]" (backend/pyproject.toml অনুযায়ী) |
| Redis | ৭.x |
| Network | pip package ইনস্টল করার জন্য অন্তত এক-বারের internet access |

## প্রস্তাবিত ধাপ (operator যখন real environment-এ চালাবেন)

```
docker compose up -d postgres redis
cd backend
pip install -e ".[dev]"
alembic upgrade head
psql -d ygl_office_dev -c "\dt"
psql -d ygl_office_dev -c "\d+ ledger_entries"
uvicorn app.main:app --reload
```

## Migration verification (0001–0006, একে একে)

`alembic upgrade head` একবারেই সব ৬টা migration ক্রমানুসারে (0001→0002→0003→0004→0005→0006) চালায়। প্রতিটা আলাদাভাবে verify করতে চাইলে:
```
alembic upgrade 0001_phase_8_1_foundation
alembic upgrade 0002_kyc_state_machine
alembic upgrade 0003_rbac_permission_seed
alembic upgrade 0004_audit_log
alembic upgrade 0005_refresh_tokens
alembic upgrade 0006_commission_persistence
```
প্রতিটা কমান্ডের পরে exit code 0 এবং `psql -c "\dt"` দিয়ে প্রত্যাশিত নতুন টেবিল(গুলো) দেখা গেলে সেই migration সফল।

## Schema verification checklist

```
psql -d ygl_office_dev -c "\dt"                                  -- সব টেবিল তালিকা
psql -d ygl_office_dev -c "\d+ ledger_entries"                    -- partial unique index (commission_line_item_id) দেখা যাচ্ছে কিনা
psql -d ygl_office_dev -c "\d+ plan_versions"                      -- partial unique index (is_published) দেখা যাচ্ছে কিনা
psql -d ygl_office_dev -c "\d+ commission_processing_records"       -- UNIQUE(order_id, plan_version_id, event_type)
psql -d ygl_office_dev -c "\d+ plan_version_locks"                   -- UNIQUE(order_id, event_type)
psql -d ygl_office_dev -c "\d+ role_permissions"                      -- composite PK (role_id, permission_id)
psql -d ygl_office_dev -c "\d+ refresh_tokens"                         -- FK -> admins.id
```

## Application startup

```
uvicorn app.main:app --reload
curl http://localhost:8000/health          -- {"status": "ok", ...} প্রত্যাশিত
```
Startup fail করলে exact traceback + root cause report করা বাধ্যতামূলক (অনুমান করে "ঠিক আছে" বলা যাবে না)।

## API verification checklist (শুধু বর্তমানে wire করা router — Auth ও KYC)

```
POST /api/v1/admin/login              {email, password}          -> access_token বা mfa_required
POST /api/v1/admin/mfa/verify         {mfa_challenge_token, totp_code}
POST /api/v1/admin/refresh            {refresh_token_id}
POST /api/v1/admin/mfa/enroll         (Bearer token)               -> mfa_secret
POST /api/v1/admin/mfa/confirm        {totp_code} (Bearer token)
POST /api/v1/webhooks/sumsub          (raw payload + X-Payload-Digest header)
POST /api/v1/kyc/{id}/manual-review/approve   (Bearer token, kyc.review প্রয়োজন)
POST /api/v1/kyc/{id}/manual-review/reject
```
প্রতিটার জন্য success case + expected-failure case (ভুল password, invalid signature, missing permission ইত্যাদি) — দুই ধরনের response verify করতে হবে।

## Security verification checklist

- Bearer token ছাড়া protected endpoint কল -> `401`
- ভুল/expired token -> `401`
- সঠিক token কিন্তু ভুল role (যেমন Support Admin দিয়ে kyc-review চেষ্টা) -> `403`
- একই refresh_token_id দুইবার ব্যবহার -> দ্বিতীয়বার `401` (replay-protected)
- ভুল webhook signature -> silently reject, কোনো state change না, `audit_logs`-এ reject-entry
- একই webhook payload দুইবার পাঠানো -> দ্বিতীয়বার `duplicate_event`, কোনো নতুন commission_line_item/ledger_entry তৈরি না
- Manual-review endpoint-এ dual submission (দুইবার approve) -> দ্বিতীয়বার ৪০৯ (idempotent)

## Integration verification (fresh DB-তে, ক্রমানুসারে)

```
১. Migration upgrade head সফল
২. Application startup সফল, /health 200
৩. একটা Admin সরাসরি DB-তে manual INSERT করে (কোনো bootstrap script এখনো তৈরি হয়নি -- এটা নিজেই একটা remaining gap, নিচে নোট করা হলো) login করা
৪. একটা Member সরাসরি DB-তে seed করে KYC verification শুরু করা (start_verification সমতুল্য)
৫. webhook পাঠিয়ে VERIFICATION_IN_PROGRESS -> APPROVED
৬. Member-এর account_status সত্যিই ACTIVE হয়েছে কিনা DB-তে verify করা
৭. audit_logs-এ পুরো chain (KYC_VERIFICATION_STARTED থেকে MEMBER_ACTIVATED পর্যন্ত) verify করা
৮. ledger_entries/commission_line_items টেবিল খালি থাকার কথা এই flow-এ (Commission শুধু Payment-confirm-এ trigger হয়, যেটা এখনো wire করা হয়নি) -- এটাই expected, কোনো bug না
```

এই পুরো checklist এখনো এই sandbox-এ চালানো সম্ভব হয়নি (দেখো উপরের "কী নেই" section) — operator বাস্তব environment-এ চালিয়ে প্রতিটা ধাপের প্রকৃত ফলাফল (pass/fail, traceback, screenshot) সংরক্ষণ করবেন।

## Environment Variables (সব `.env`/secrets-manager থেকে, কখনো কোডে commit নয়)

| Variable | বাধ্যতামূলক? | Dev placeholder (DEV ONLY) | নোট |
|---|---|---|---|
| `DATABASE_URL` | ✅ হ্যাঁ, কোনো default নেই | `postgresql+psycopg://ygl_dev:dev_only_change_me@localhost:5432/ygl_office_dev` | Sync psycopg driver ব্যবহার করে (আগে ভুলবশত asyncpg স্কিম ছিল, এই ধাপে ঠিক করা হয়েছে — নিচে দেখো) |
| `JWT_SECRET_KEY` | ✅ হ্যাঁ, কোনো default নেই (app fail-fast করবে) | — (production-এ real secret অবশ্যই দিতে হবে) | কখনো `.env`-এও commit করা যাবে না |
| `SUMSUB_WEBHOOK_SECRET` | ✅ হ্যাঁ production-এ | `dev-only-placeholder-not-a-real-secret` (**DEV ONLY**) | শুধু webhook-signature-verification-এর জন্য, live API token না |
| `REDIS_URL` | না, default আছে | `redis://localhost:6379/0` | বর্তমান Auth/RBAC implementation DB-driven, Redis এখনো ব্যবহৃত হচ্ছে না directly |
| `ENVIRONMENT` | না, default `development` | `development` | `staging`/`production`-এ পরিবর্তন করতে হবে deploy-time-এ |
| `JWT_ACCESS_TOKEN_TTL_MINUTES` | না, default ২০ | ২০ | Business Owner session-policy decision অনুযায়ী চূড়ান্ত করা এখনো বাকি |
| `JWT_REFRESH_TOKEN_TTL_DAYS` | না, default ১৪ | ১৪ | একই |
| `ADMIN_LOGIN_FAILURE_THRESHOLD` | না, default ৩ | ৩ | Technical Lead configuration item |
| `ADMIN_LOGIN_LOCKOUT_MINUTES` | না, default ৩০ | ৩০ | Technical Lead configuration item |

## System Dependencies (OS-level, PostgreSQL/Python-এর বাইরে)

- `libpq-dev` (বা equivalent) — `psycopg[binary]` সাধারণত prebuilt wheel ব্যবহার করে, কিন্তু কোনো platform-এ binary wheel না থাকলে এটা লাগতে পারে
- `build-essential`/C compiler — শুধু যদি কোনো dependency source থেকে build করতে হয়

## ⚠️ এই ধাপে পাওয়া ও সংশোধিত একটা real bug: async/sync mismatch

`app/db/session.py` আগে `AsyncSession`/`create_async_engine` ব্যবহার করছিল, কিন্তু Auth/KYC/Audit/RBAC/Commission — সব repository/adapter file সরাসরি synchronous SQLAlchemy call (`self.db.execute(...)`, `self.db.get(...)`, কোনো `await` ছাড়া) ব্যবহার করে লেখা হয়েছিল। **এটা real environment-এ সত্যিই চললে ব্যর্থ হতো।** এই ধাপে ধরা পড়েছে ও ঠিক করা হয়েছে — `db/session.py` এখন synchronous (`create_engine`/`Session`/`sessionmaker`), `DATABASE_URL`-এর driver-scheme `postgresql+asyncpg://` থেকে `postgresql+psycopg://`-এ পরিবর্তন করা হয়েছে (docker-compose.yml, CI workflow, উপরের টেবিলেও), এবং এখন-অপ্রয়োজনীয় `asyncpg` dependency `pyproject.toml` থেকে সরানো হয়েছে। **কোনো adapter/router-এর business logic পরিবর্তন হয়নি — শুধু session layer।**

## Final Acceptance Gates (সবগুলো PASS না হওয়া পর্যন্ত "Production Ready" ঘোষণা করা যাবে না)

- [ ] `alembic upgrade head` — exit code 0, কোনো error/traceback ছাড়া
- [ ] `psql -c "\dt"` — সব ১৬টা টেবিল উপস্থিত (roles, permissions, role_permissions, admins, members, member_status_history, kyc_documents, sponsor_placements, placement_review_queue, kyc_verifications, kyc_state_transitions, audit_logs, refresh_tokens, plan_versions, commission_processing_records, plan_version_locks, commission_line_items, ledger_entries, undistributed_amounts — মোট ১৯টা)
- [ ] উপরের Schema verification checklist-এর সব constraint সত্যিই উপস্থিত (partial index-সহ)
- [ ] `uvicorn app.main:app` — কোনো traceback ছাড়া শুরু হয়, `/health` 200 রিটার্ন করে
- [ ] API verification checklist-এর প্রতিটা endpoint-এর success+failure case verified
- [ ] Security verification checklist-এর সব ৭টা scenario verified
- [ ] Integration verification-এর ৮-ধাপ সম্পূর্ণ, ধাপ ৬-৭-এ DB-তে সরাসরি query করে ফলাফল নিশ্চিত করা
- [ ] Commission Engine checksum (১৮/১৮), ৭৯-test, import-boundary — তিনটাই এই real environment-এও পুনরায় PASS
- [ ] Office System-এর ৭৯টা test এই real environment-এও (pytest দিয়ে, শুধু unittest discover না) PASS

**উপরের প্রতিটা আইটেমের প্রকৃত ফলাফল (pass/fail + exact output) ছাড়া কোনো একটাকেও "assumed pass" ধরা যাবে না।**



উপরের ধাপ ৩-এ "Admin bootstrap procedure"-এর কথা আগে একটা চ্যাট-রিপোর্টে আলোচনা হয়েছিল (one-time script + forced MFA-enrollment ডিজাইন), কিন্তু **প্রকৃতপক্ষে কোনো bootstrap script/migration কখনো লেখা হয়নি এই repo-তে** — শুধু concept আলোচনা হয়েছিল। তাই প্রথম Admin operator-কে আপাতত সরাসরি SQL `INSERT` (password argon2id/pbkdf2 দিয়ে হ্যাশ করে) করতে হবে। একটা real bootstrap script (CLI command বা Alembic data-migration) তৈরি করা এখনো বাকি — এটা এই sandbox-limitation-এর তালিকায় না, বরং একটা genuine, আলাদা implementation-gap হিসেবে এখানে honestly নোট করা হলো।
