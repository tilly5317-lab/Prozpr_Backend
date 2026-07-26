# app/domains/rebalancing/services/rebal_engine/ — the rebalancing compute engine

Cache-first orchestration → engine inputs → trade list → chat markdown. Reached through the domain's `rebalancing_module_service` on the chat path, directly by `POST /api/v1/ai-modules/rebalancing/compute` (`ai_engine/routers/rebalancing_router.py`), and `fund_rank` alone is also imported by `additional_investment`'s `ainv_engine`.

## Files
- `service.py` — orchestrator entry: cache-first allocation lookup, runs the engine, persists **only when `persist=True`** (the default). `chat.py`'s `counterfactual_explore` path passes `persist=False`, so a hypothetical writes neither a `RebalancingRun` row nor a `record_ai_module_run` telemetry row and comes back with `recommendation_id=None` — otherwise the what-if would land at the top of the user's newest-first run list and become the `last_agent_runs["rebalancing"]` that follow-up narrate/educate turns describe (`service.py:552,682`; `chat.py:521`).
- `input_builder.py` — materialises the engine request from `TurnContext` + allocation + DB.
- `formatter.py` — sectioned chat markdown.
- `fund_rank.py` — static fund-rank CSV loader (`get_fund_ranking`, `get_rejection_reasons`).
- `holdings_ledger.py` — FIFO remaining-lot ledger built from transactions.
- `cached_allocation.py` — lightweight view over the allocation subgroup JSON.
- `overrides.py` — per-turn chat override allow-list.
- `readiness.py` — engine-side readiness check.
- `tax_aging.py` — per-lot ST/LT aging.
- `chat.py` — the REBALANCING-intent chat handler.
- `_disk_cache.py` — CSV-backed NAV/metadata disk cache (`_NAV_CSV`/`_META_CSV`); the engine's only price/metadata source.
- `tests/` — pytest suite (per-module + e2e).

## Gotchas & invariants
- **Prices off CSVs, not the DB.** NAV and fund metadata are read from `latest_nav_active.csv` / `mf_subgroup_mapped.csv` under `MF_Logics/Mututal_Funds_data_extraction/` — the *only* NAV/metadata path the engine uses (`_disk_cache.py`, `_NAV_CSV`/`_META_CSV`). Known prod-migration debt; do not assume `mf_nav_history`.
- **Import `chat` lazily.** It is deliberately not re-exported from `__init__.py` — eager import triggers a circular import via `chat_core.turn_context` (`__init__.py`).
- **FIFO redemption sign.** CAS stores redemption units as negative; use the magnitude, or the `while remaining > 0` loop never runs and sold lots stay on the books (`holdings_ledger.py`).
- **`target_amount_pre_cap` written here is advisory.** `input_builder.py` still emits the goal amount on rank-1 and `0` on ranks 2+, but since engine 1.3.0 `Rebalancing/pipeline.py::_assign_subgroup_targets` overwrites it for every ranked row using holdings-aware floors. Target-sizing bugs belong in the engine — changing this builder will not move the plan.
- **`fund_rating` is hardcoded** (`_DEFAULT_FUND_RATING = 10`, `input_builder.py`). The engine's `fund_rating < EXIT_FLOOR_RATING` exit carve-out therefore never fires in production; force-exits arrive via `FORCE_EXIT_RANK` from the ranking CSV instead. Wiring real ratings through will switch that path on.

## Don't read
- `__pycache__/`, `tests/`.
