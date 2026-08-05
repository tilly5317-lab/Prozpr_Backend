# app/domains/ingestion/ — external-data adapters: CAMS/KFintech CAS PDF, AA normalizer, SimBanks XML, (sidelined) Finvu

## Layers

- **(no models/)** — writes into other domains' tables (`mutual_funds`, `portfolio`); owns no ORM of its own.
- **schemas/** — cams / mf_aa / finvu / simbanks payloads.
- **routers/** — `/mf-ingest` (CAMS PDF upload, AA-import normalize, mfapi.in refresh/backfill), `/simbanks`.
- Add a Gotchas bullet: "**Every CAS upload wipes and rebuilds.** A CAS is a complete snapshot, so `ingest_cams_pdf` runs `reset_user_financial_data` unconditionally before writing — portfolio, holdings, the MF ledger + audit trail, asset-allocation / practical-allocation / rebalancing runs, cashflow plans and inputs, net-worth history, advisory notes/IPS all go, and are rebuilt from this statement alone so no stale cached state can leak through. Profile/onboarding, goals, chats, notifications, account links and family survive; global reference data (fund metadata, NAV history, benchmarks) is never touched. `replace_existing` is retained for API compatibility only and no longer changes behaviour. A new table holding CAS-derived state must be added to the reset (`services/cams_cas_ingest.py`, `services/user_data_reset.py`)."

## Gotchas & invariants

- A successful CAMS upload fires a best-effort BACKGROUND net-worth backfill, gated: only if status != FAILED, txns inserted, and no job already running (idempotent); a failed kickoff never fails the upload (`routers/mf_ingest_router.py`).
- `casparser` is a heavy optional dependency, imported LAZILY inside the parse function so the app boots without it installed (`services/cams_cas_ingest.py`, `_parse_cas_pdf`).
- CAMS ingest writes across THREE areas: raw audit (`mf_aa_imports`/summaries/transactions), normalized `mf_transactions`, and bucketed `portfolio_allocations` (Cash/Debt/Equity/Other) (`services/cams_cas_ingest.py`).
- A transaction-type allow-list (`_TXN_TYPE_FLAG`) silently SKIPS non-holding-flow rows — DIVIDEND_PAYOUT, STAMP_DUTY_TAX, STT_TAX, TDS_TAX, etc. — so only unit-moving txns become `mf_transactions` (`services/cams_cas_ingest.py`).
- CAS profile/identity back-fill FILLS BLANKS ONLY — email, PAN, address; **the investor name is NOT taken from the CAS** (the sign-up name is authoritative; the legal name is still kept in the `mf_aa_imports` audit row). Anything the user already set is never overwritten, and unique email/PAN are skipped on clash (`services/cams_cas_ingest.py`, `_backfill_user_profile`).
- `ingest_cams_pdf` REJECTS, before any DB write (and before the unconditional reset wipes prior data), two statement variants — raising `CamsPdfParseError` → HTTP 422: a **Summary CAS** (`cas_type == "SUMMARY"`, holdings only / no txn history) and a statement with **no current holdings** (`_total_market_value(parsed) <= 0`) (`services/cams_cas_ingest.py`).
- The normalized `mf_transactions` bulk insert is CHUNKED (1500 rows/statement) — asyncpg caps a statement at 32767 bind params and each row binds 18, so one VALUES insert breaks past ~1820 txns (a 15y+ CAS). The normalizer's user-facing `error` is a fixed friendly string; raw exceptions go to the log + `failure_reason` audit column only (`services/mf_aa_normalizer.py`, `_INSERT_CHUNK_ROWS`).
- casparser.in caps MULTIPART bodies at ~1.8 MB (plan-based, their support 2026-08-04), but their `pdf_url` mode has NO size limit — so CAS PDFs over `CASPARSER_MULTIPART_MAX_BYTES` (default 1.7 MB) are staged in the private `CAMS_STAGE_S3_BUCKET` under an unguessable key, casparser fetches a ~10-min presigned GET, and the object is deleted right after the parse (`services/cams_pdf_stage.py`; branch in `cams_cas_ingest.py`, `_parse_cas_via_api`). boto3 imports lazily; bucket unset (local dev) → multipart fallback and an explicit "statement too large" `CamsPdfParseError` on the resulting 413. Bucket needs Block Public Access ON + a 1-day lifecycle expiry on `cams-stage/` as the crash backstop.
- Successfully parsed statements are KEPT: archived to S3 under `user-cas/{user_id}/{doc_id}.pdf` + recorded in `user_cas_documents` (owned by `mutual_funds/models/user_cas_document.py`), which backs the profile's "My CAS statements" list (`GET/DELETE /mf-ingest/cas-documents`, presigned 5-min downloads). That table deliberately SURVIVES `reset_user_financial_data` — never add it to the reset list. Any bucket lifecycle expiry must be scoped to the `cams-stage/` prefix ONLY, or archived statements get deleted (`services/cams_pdf_stage.py`).
- Finvu is DEPRECATED (account-aggregator licensing), superseded by CAMS CAS upload; retained for reference, off all active paths (`services/finvu_portfolio_sync.py`).

## Don't read

- `__pycache__/`.
