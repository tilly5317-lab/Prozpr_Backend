# app/domains/ingestion/ — external-data adapters: CAMS/KFintech CAS PDF, AA normalizer, SimBanks XML, (sidelined) Finvu

## Layers

- **(no models/)** — writes into other domains' tables (`mutual_funds`, `portfolio`); owns no ORM of its own.
- **schemas/** — cams / mf_aa / finvu / simbanks payloads.
- **routers/** — `/mf-ingest` (CAMS PDF upload, AA-import normalize, mfapi.in refresh/backfill), `/simbanks`.
- **services/** — `cams_cas_ingest`, `casparser_client` (CAS Parser API HTTP client), `casparser_adapter` (v4 JSON → legacy parsed dict), `mf_aa_normalizer`, `simbanks_service` (ConnectHub XML), `user_data_reset` (full per-user wipe for a clean CAMS re-ingest), `finvu_portfolio_sync` (DEPRECATED).

## Gotchas & invariants

- A successful CAMS upload fires a best-effort BACKGROUND net-worth backfill, gated: only if status != FAILED, txns inserted, and no job already running (idempotent); a failed kickoff never fails the upload (`routers/mf_ingest_router.py`).
- CAS parsing is REMOTE: the PDF is posted to the CAS Parser API (`https://api.casparser.in/v4/smart/parse`, key `CASPARSER_API_KEY`) and the v4 response is adapted back onto the legacy parsed-dict shape, so downstream persisters are unchanged (`services/casparser_client.py`, `services/casparser_adapter.py`, `cams_cas_ingest._parse_cas_via_api`). Both `/mf-ingest/cams-pdf` and `/mf-ingest/cams-request` are inert (503/parse error) without the key. The API also signals failures INSIDE a 200 body (`{"status": "failed"}`).
- `POST /mf-ingest/cams-request` triggers a CAS **mailback**: CAMS/KFintech emails the investor a full-history detailed CAS protected with a user-chosen password (validated: 8+ chars, letter+number+special); a 60 s per-user in-process cooldown stops double-taps from burning API credits (`routers/mf_ingest_router.py`).
- CAMS ingest writes across THREE areas: raw audit (`mf_aa_imports`/summaries/transactions), normalized `mf_transactions`, and bucketed `portfolio_allocations` (Cash/Debt/Equity/Other) (`services/cams_cas_ingest.py`).
- A transaction-type allow-list (`_TXN_TYPE_FLAG`) silently SKIPS non-holding-flow rows — DIVIDEND_PAYOUT, STAMP_DUTY_TAX, STT_TAX, TDS_TAX, etc. — so only unit-moving txns become `mf_transactions` (`services/cams_cas_ingest.py`).
- Portfolio numbers (`portfolio_holdings` MF rows, bucket `portfolio_allocations`, `total_value`) are TRANSACTION-DERIVED — per-scheme units/cost summed from the statement's txns × statement NAV (`_derive_scheme_snapshot`), NOT the CAS valuation block — so they always equal the `mf_transactions` roll-up (`mf_holdings` view, fund-detail page). Per-scheme fallback to CAS-stated close/valuation when zero unit-moving txns parsed OR when txn-derived units contradict the statement's opening→closing balance (>1% — incomplete extraction would corrupt the value) (`services/cams_cas_ingest.py`).
- `portfolio_holdings` MF rows are ONE PER FUND, not per folio — a fund held in several folios (bought via different platforms) is merged by canonical identity (AMFI code → ISIN → name) with summed units/value and units-weighted avg cost; multi-folio rows are named `… · Folios A, B` (frontend strips `· Folio.*`) (`services/cams_cas_ingest.py`, `_sync_mf_portfolio_holdings_from_cas`).
- CAS profile/identity back-fill FILLS BLANKS ONLY — email, PAN, address; **the investor name is NOT taken from the CAS** (the sign-up name is authoritative; the legal name is still kept in the `mf_aa_imports` audit row). Anything the user already set is never overwritten, and unique email/PAN are skipped on clash (`services/cams_cas_ingest.py`, `_backfill_user_profile`).
- `ingest_cams_pdf` REJECTS, before any DB write (and before a `replace_existing` reset wipes prior data), two statement variants — raising `CamsPdfParseError` → HTTP 422: a **Summary CAS** (`cas_type == "SUMMARY"`, holdings only / no txn history) and a statement with **no current holdings** (`_total_market_value(parsed) <= 0`) (`services/cams_cas_ingest.py`).
- Finvu is DEPRECATED (account-aggregator licensing), superseded by CAMS CAS upload; retained for reference, off all active paths (`services/finvu_portfolio_sync.py`).

## Don't read

- `__pycache__/`.
