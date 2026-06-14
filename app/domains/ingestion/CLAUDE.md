# app/domains/ingestion/ — external-data adapters: CAMS/KFintech CAS PDF, AA normalizer, SimBanks XML, (sidelined) Finvu

## Layers

- **(no models/)** — writes into other domains' tables (`mutual_funds`, `portfolio`); owns no ORM of its own.
- **schemas/** — cams / mf_aa / finvu / simbanks payloads.
- **routers/** — `/mf-ingest` (CAMS PDF upload, AA-import normalize, mfapi.in refresh/backfill), `/simbanks`.
- **services/** — `cams_cas_ingest`, `mf_aa_normalizer`, `simbanks_service` (ConnectHub XML), `finvu_portfolio_sync` (DEPRECATED).

## Gotchas & invariants

- A successful CAMS upload fires a best-effort BACKGROUND net-worth backfill, gated: only if status != FAILED, txns inserted, and no job already running (idempotent); a failed kickoff never fails the upload (`routers/mf_ingest_router.py`).
- `casparser` is a heavy optional dependency, imported LAZILY inside the parse function so the app boots without it installed (`services/cams_cas_ingest.py`, `_parse_cas_pdf`).
- CAMS ingest writes across THREE areas: raw audit (`mf_aa_imports`/summaries/transactions), normalized `mf_transactions`, and bucketed `portfolio_allocations` (Cash/Debt/Equity/Other) (`services/cams_cas_ingest.py`).
- A transaction-type allow-list (`_TXN_TYPE_FLAG`) silently SKIPS non-holding-flow rows — DIVIDEND_PAYOUT, STAMP_DUTY_TAX, STT_TAX, TDS_TAX, etc. — so only unit-moving txns become `mf_transactions` (`services/cams_cas_ingest.py`).
- CAS profile/identity back-fill FILLS BLANKS ONLY (name, email, PAN, address); anything the user already set is never overwritten, and unique email/PAN are skipped on clash (`services/cams_cas_ingest.py`, `_backfill_user_profile`).
- Finvu is DEPRECATED (account-aggregator licensing), superseded by CAMS CAS upload; retained for reference, off all active paths (`services/finvu_portfolio_sync.py`).

## Don't read

- `__pycache__/`.
