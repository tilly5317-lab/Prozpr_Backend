# app/domains/mutual_funds/ — MF metadata, NAV, transactions, SIPs, snapshots, watchlists, AA imports, mfapi.in fetcher + scheduler

## Layers

- **models/** — fund metadata/rating, NAV history, SIP mandates, transactions, AA imports, allocation + latest snapshots, investment lists, legacy `fund`, and `index_tri_history` (NSE index TRI, e.g. NIFTY 50).
- **schemas/** — per-resource pydantic plus the mfapi DTOs and AA-import payloads.
- **routers/** — the `/mf` router, one sub-router per resource (fund metadata/rating, NAV, transactions, SIP mandates, holding detail, snapshots, investment lists, AA imports).
- **services/** — per-resource CRUD plus the fetch/ingest/scheduler stacks and the cross-cutting helpers called out below (classifier, resolver, XIRR, holding-detail read path). Reuse `xirr_service` — don't rebuild XIRR.

## Gotchas & invariants

- `scheme_classification.py` is the single canonical fund classifier for the whole app; the rebalancing CSV `mf_subgroup_mapped.csv` is a FROZEN snapshot of it, so this module is the live source. Name-match ORDER matters — specific vocabulary before generic (`services/scheme_classification.py`, `_infer_*` near line 474).
- `scheme_resolver.build_isin_to_amfi_map` MUST run at ingest (ISIN/RTA code → numeric AMFI code) or a holding can never be priced or refreshed — both `mf_nav_history` and mfapi.in are keyed by AMFI code (`services/scheme_resolver.py`; callers `ingestion/.../cams_cas_ingest.py`, `mf_aa_normalizer.py`).
- Two schedulers are env-gated and serialized across uvicorn workers by Postgres advisory locks: daily NAV/master (`MFAPI_SCHEDULER_ENABLED`, 00:00 IST) and Nifty TRI (`INDEX_TRI_SCHEDULER_ENABLED`, 20:30 IST). The daily portfolio net-worth backfill (`portfolio_networth_daily`) is registered inside the mfapi job. (`services/mfapi_scheduler.py`, `services/index_tri_scheduler.py`, `app/core/config.py`).
- NAV and TRI bulk inserts are idempotent via `ON CONFLICT ... DO NOTHING`, so re-runs never duplicate (`services/nav_history_service.py:228`, `services/index_tri_service.py:56`).
- The holding-detail read path self-heals: a plain GET runs `normalize_pending_imports` so received-but-unnormalized CAS imports surface without hitting the ingest route (`services/holding_detail_service.py:370`).

## Don't read

- `__pycache__/`.
