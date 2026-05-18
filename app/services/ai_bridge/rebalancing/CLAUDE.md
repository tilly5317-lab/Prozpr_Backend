# app/services/ai_bridge/rebalancing/ — Rebalancing bridge

Bridges `AI_Agents/src/Rebalancing` into chat. Pulls the most recent goal
allocation (re-running it inline if absent or > 90 days old), materialises
engine inputs from User + holdings + tax state, runs the pipeline on a worker
thread, persists the trade list, and renders chat markdown.

## Files

- `service.py` — cache-first rebalancing orchestrator (allocation freshness check, input materialisation, thread offload, persistence, markdown render).
- `chat.py` — single chat handler for the `REBALANCING` intent.
- `input_builder.py` — `RebalancingComputeRequest` from `TurnContext` + `GoalAllocationOutput`.
- `formatter.py` — sectioned markdown for the chat reply. Voice: "financially-savvy friend, not advisor" — plain language, no compliance boilerplate. All copy lives here so tone iterations don't touch the structured data path.
- `tax_aging.py` — per-lot tax-aging and exit-load helpers; ST/LT cut-offs read from `Rebalancing/config` so builder and engine share one source of truth.
- `holdings_ledger.py` — per-ISIN list of remaining FIFO lots from `MfTransaction` rows (switches/dividend-reinvest not handled in v1).
- `fund_rank.py` — loader for the static `asset_subgroup → ranked funds` CSV consumed by the input builder. Cached as a frozen dict at import time; no DB calls.
- `overrides.py` — per-turn chat-override helpers for rebalancing; re-imports `with_chat_overrides` from `asset_allocation/overrides.py` (generic helper).
- `_disk_cache.py` — CSV-backed NAV + fund-metadata reader. **TODO(DB-backed):** currently reads from `MF_Logics/Mututal_Funds_data_extraction/`; production target is `mf_nav_history` + `mf_fund_metadata`.
- `__init__.py` — package marker.
- `tests/` — pytest suite for the bridge.
- `archive/` — retired earlier versions; not on active import paths.

## Depends on

- `AI_Agents/src/Rebalancing` — the rebalancing pipeline.
- `app/services/ai_bridge/asset_allocation` — for inline allocation refresh.
- `app/models/*` — User graph + MF transactions + persisted allocation/rebalancing rows.

## Don't read

- `__pycache__/`.
- `tests/` — fixtures, not source of truth.
- `archive/` — retired.
