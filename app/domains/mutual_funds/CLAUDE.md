# app/domains/mutual_funds/ — MF metadata, NAV, transactions, SIPs, snapshots, watchlists, AA imports, mfapi.in fetcher + scheduler

## Layers

- **models/** — fund metadata/rating, NAV history, SIP mandates, transactions, AA imports, allocation + latest snapshots, investment lists, legacy `fund`. (Benchmark index data moved to the `benchmarks` domain.)
- **schemas/** — per-resource pydantic plus the mfapi DTOs and AA-import payloads.
- **routers/** — the `/mf` router, one sub-router per resource (fund metadata/rating, NAV, transactions, SIP mandates, holding detail, snapshots, investment lists, AA imports).
- **services/** — per-resource CRUD plus the fetch/ingest/scheduler stacks and the cross-cutting helpers called out below (classifier, resolver, XIRR, holding-detail read path). Reuse `xirr_service` — don't rebuild XIRR.

## Gotchas & invariants

- `scheme_classification.py` is the single canonical fund classifier for the whole app; the rebalancing CSV `mf_subgroup_mapped.csv` is a FROZEN snapshot of it, so this module is the live source. Name-match ORDER matters — specific vocabulary before generic (`services/scheme_classification.py`, `classify_sub_category` + the `_classify_*` helpers ~:380-463).
- `scheme_resolver.build_isin_to_amfi_map` MUST run at ingest (ISIN/RTA code → numeric AMFI code) or a holding can never be priced or refreshed — both `mf_nav_history` and mfapi.in are keyed by AMFI code (`services/scheme_resolver.py`; callers `ingestion/.../cams_cas_ingest.py`, `mf_aa_normalizer.py`).
- - The mfapi NAV/master scheduler is env-gated (`MFAPI_SCHEDULER_ENABLED`) and runs three times a day (00:05 / 13:05 / 22:05 IST), serialized across uvicorn workers by a Postgres advisory lock. `start_scheduler()` also registers a SECOND, independent job on the same scheduler — the portfolio net-worth refresh (`portfolio_networth_daily`, 06:00 / 14:00 / 23:00 IST) — staggered to land just after each NAV run so net worth revalues against fresh NAV (`services/mfapi_scheduler.py:46-48`, `:222-257`; `app/core/config.py`). The Nifty/benchmark scheduler now lives in the `benchmarks` domain. (`services/mfapi_scheduler.py`, `app/core/config.py`). The Nifty/benchmark scheduler now lives in the `benchmarks` domain.
- NAV bulk inserts are idempotent via `ON CONFLICT ... DO NOTHING`, so re-runs never duplicate (`services/nav_history_service.py:225`).
- The holding-detail read path self-heals: a plain GET runs `normalize_pending_imports` so received-but-unnormalized CAS imports surface without hitting the ingest route (`services/holding_detail_service.py:399`).

## Don't read

- `__pycache__/`, `tests/`.
