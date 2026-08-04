# app/domains/rebalancing/ — rebalancing runs, trades, fund rows, subgroup summaries, warnings

## Entry / contract
- `rebalancing_module_service` is the gateway on the chat path — the brain calls its `run(turn, ctx, prior)`. One other live caller bypasses it: the debug / frontend-driven route `POST /api/v1/ai-modules/rebalancing/compute` (`app/domains/ai_engine/routers/rebalancing_router.py`) calls `rebal_engine.service.compute_rebalancing_result` directly and commits the session itself. The AI bridge that *produces* the source allocation lives in `app/domains/ai_engine`. The source allocation a run rebalances toward is the latest `AssetAllocationRun` (plus its IDEAL `PortfolioAllocationSnapshot`) written by the `asset_allocation` domain, read cache-first with a 90-day TTL and, on a miss, recomputed inline via `asset_allocation`'s `aa_engine.compute_allocation_result` — nothing in `app/domains/ai_engine` produces it.

## Layers
- **models/** — `RebalancingRun` and its children `RebalancingTrade` / `RebalancingFundRow` / `RebalancingSubgroupSummary` / `RebalancingWarning`.
- **schemas/** — the run-API contract (pydantic views over the run tables) in `__init__.py` — including the read-time computed `summary` and `asset_class_breakdown` — plus `readiness` (payload for `GET /rebalancing/readiness`).
- **routers/** — the `/rebalancing` router.
- **services/** — `rebalancing_persist_service` (the write surface, called at the end of a run by `rebal_engine/service.py`); `rebalancing_read_service` (read-side helper over persisted runs — `additional_investment`'s SIP path mirrors the latest run's BUY trades through it); `rebalancing_module_service` (the gateway above); `rebalancing_summary.py` (pure, DB-free headline — title/subtitle/reason — behind the run-detail `summary` computed field); `asset_class_breakdown.py` (Invest-page current-vs-target Equity/Debt/Others split; the engine's generic `multi_asset` sleeve is split 72.5/12.5/15 rather than by the funds picked to fill it); `rebal_engine/` — the compute engine, documented in its own `rebal_engine/CLAUDE.md`.

## Gotchas & invariants
- A run ALWAYS rebalances toward a persisted asset-allocation run — `source_allocation_run_id` is required (`services/rebalancing_persist_service.py`).
- The engine reuses a cached allocation for up to 90 days (`ALLOCATION_TTL_DAYS`); a chat override such as `additional_cash_inr` sets `force_fresh_allocation=True`, which skips that cache and re-runs allocation inline (`rebal_engine/service.py`). Miss this and chat returns a stale plan.

## Don't read
- `__pycache__/`.
