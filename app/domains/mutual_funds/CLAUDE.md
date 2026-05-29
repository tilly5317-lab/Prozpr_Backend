# app/domains/mutual_funds/ — MF metadata, NAV, transactions, SIPs, snapshots, watchlists, AA imports, mfapi.in fetcher + scheduler

Mf metadata, nav, transactions, sips, snapshots, watchlists, aa imports, mfapi.in fetcher + scheduler.

## Layers

- **models/** — mf_fund_metadata, mf_fund_rating, mf_nav_history, mf_sip_mandate, mf_transaction, mf_aa_import/summary/transaction, mf_allocation_snapshot, user_mf_latest_snapshot, user_investment_list, fund (legacy reference)
- **schemas/** — per-resource pydantic + mfapi DTOs + aa_import payloads
- **routers/** — /mf router with one sub-router per resource (fund_metadata, fund_rating, nav_history, transactions, sip_mandates, holding_detail, latest_snapshot, portfolio_snapshots, user_investment_lists, aa_imports)
- **services/** — per-resource services + mfapi_fetcher / mfapi_ingest_service / mfapi_scheduler / aa_access_service / aa_import_service / paging helpers

## Don't read

- `__pycache__/`.
