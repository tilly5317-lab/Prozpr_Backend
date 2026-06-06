# app/domains/mutual_funds/ — MF metadata, NAV, transactions, SIPs, snapshots, watchlists, AA imports, mfapi.in fetcher + scheduler

Mf metadata, nav, transactions, sips, snapshots, watchlists, aa imports, mfapi.in fetcher + scheduler.

## Layers

- **models/** — mf_fund_metadata, mf_fund_rating, mf_nav_history, mf_sip_mandate, mf_transaction, mf_aa_import/summary/transaction, mf_allocation_snapshot, user_mf_latest_snapshot, user_investment_list, fund (legacy reference), index_tri_history (NSE index TRI, e.g. NIFTY 50)
- **schemas/** — per-resource pydantic + mfapi DTOs + aa_import payloads
- **routers/** — /mf router with one sub-router per resource (fund_metadata, fund_rating, nav_history, transactions, sip_mandates, holding_detail, latest_snapshot, portfolio_snapshots, user_investment_lists, aa_imports)
- **services/** — per-resource services + mfapi_fetcher / mfapi_ingest_service / mfapi_scheduler / aa_access_service / aa_import_service / paging helpers + xirr_service (portfolio/scheme XIRR; reuse, don't rebuild) + niftyindices_fetcher / index_tri_service / index_tri_scheduler (Nifty 50 TRI scrape + daily 20:30 IST refresh)

## Don't read

- `__pycache__/`.
