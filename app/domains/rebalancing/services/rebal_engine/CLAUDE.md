# app/domains/rebalancing/services/rebal_engine/ — the rebalancing compute engine

Cache-first orchestration → engine inputs → trade list → chat markdown. Reached only through the domain's `rebalancing_module_service`.

## Files
- `service.py` — orchestrator entry: cache-first allocation lookup, runs the engine, persists.
- `input_builder.py` — materialises the engine request from `TurnContext` + allocation + DB.
- `formatter.py` — sectioned chat markdown.
- `fund_rank.py` — static fund-rank CSV loader (`get_fund_ranking`, `get_rejection_reasons`).
- `holdings_ledger.py` — FIFO remaining-lot ledger built from transactions.
- `cached_allocation.py` — lightweight view over the allocation subgroup JSON.
- `overrides.py` — per-turn chat override allow-list.
- `readiness.py` — engine-side readiness check.
- `tax_aging.py` — per-lot ST/LT aging.
- `chat.py` — the REBALANCING-intent chat handler.
- `tests/` — pytest suite (per-module + e2e).

## Gotchas & invariants
- **Prices off CSVs, not the DB.** NAV and fund metadata are read from `latest_nav_active.csv` / `mf_subgroup_mapped.csv` under `MF_Logics/Mututal_Funds_data_extraction/` — the *only* NAV/metadata path the engine uses (`_disk_cache.py`, `_NAV_CSV`/`_META_CSV`). Known prod-migration debt; do not assume `mf_nav_history`.
- **Import `chat` lazily.** It is deliberately not re-exported from `__init__.py` — eager import triggers a circular import via `chat_core.turn_context` (`__init__.py`).
- **FIFO redemption sign.** CAS stores redemption units as negative; use the magnitude, or the `while remaining > 0` loop never runs and sold lots stay on the books (`holdings_ledger.py`).

## Don't read
- `__pycache__/`, `tests/`.
