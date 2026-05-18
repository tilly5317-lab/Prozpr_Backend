# app/services/mf/ — Mutual-fund domain services

User-scoped CRUD and ingest services for the MF tables (`mf_fund_metadata`,
`mf_nav_history`, `mf_transactions`, `mf_sip_mandates`,
`portfolio_allocation_snapshots`, `user_investment_lists`, AA import trees).
Called from `app/routers/mf/` and from chat bridges that need fund-detail or
NAV data.

## Files

- `aa_access.py` — ownership/permission checks for account-aggregator import trees.
- `aa_import_service.py` — CRUD for AA import batches plus nested summary/transaction rows.
- `fund_metadata_service.py` — CRUD for the global scheme catalog (`mf_fund_metadata`).
- `mf_investor_detail_service.py` — NAV-based return metrics and chart data for the fund detail (investor) page.
- `mfapi_fetcher.py` — async client for the public mfapi.in feed (`fetch_universe`, `fetch_scheme_detail`) with retry/backoff and bounded concurrency.
- `mfapi_ingest_service.py` — full + incremental ingest pipeline that upserts `mf_fund_metadata` and bulk-inserts NAV rows with conflict-do-nothing for idempotency.
- `mfapi_scheduler.py` — daily 00:05 IST cron job that runs incremental ingest; serialized across uvicorn workers via Postgres advisory lock; gated by `MFAPI_SCHEDULER_ENABLED`.
- `nav_history_service.py` — CRUD for `mf_nav_history`.
- `paging.py` — shared list pagination limits (`DEFAULT_LIMIT`, `MAX_LIMIT`).
- `portfolio_snapshot_service.py` — CRUD for `portfolio_allocation_snapshots` (scoped by user).
- `sip_mandate_service.py` — CRUD for `mf_sip_mandates` (scoped by user).
- `transaction_service.py` — CRUD for `mf_transactions` (scoped by user).
- `user_investment_list_service.py` — CRUD for `user_investment_lists` (one row per `list_kind`).
- `__init__.py` — package marker.

## Conventions

- Most services are thin async-SQLAlchemy CRUD wrappers; HTTP 4xx errors raise
  `HTTPException` directly so routers don't have to re-translate.
- `mfapi_*` modules are the only path that talks to external HTTP; everything
  else is DB-only.

## Don't read

- `__pycache__/`.
