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
- Finvu is DEPRECATED (account-aggregator licensing), superseded by CAMS CAS upload; retained for reference, off all active paths (`services/finvu_portfolio_sync.py`).

## Don't read

- `__pycache__/`.
