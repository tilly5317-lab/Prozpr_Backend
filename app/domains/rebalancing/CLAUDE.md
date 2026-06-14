# app/domains/rebalancing/ — rebalancing runs, trades, fund rows, subgroup summaries, warnings

## Entry / contract
- `rebalancing_module_service` is the ONLY gateway to the rebalancing AI module — the brain calls its `run(turn, ctx, prior)`. The AI bridge that *produces* the source allocation lives in `app/domains/ai_engine`.

## Layers
- **models/** — `RebalancingRun` and its children `RebalancingTrade` / `RebalancingFundRow` / `RebalancingSubgroupSummary` / `RebalancingWarning`.
- **schemas/** — `rebalancing_flat` (the run-API contract — pydantic views over the run tables) + `readiness` (payload for `GET /rebalancing/readiness`).
- **routers/** — the `/rebalancing` router.
- **services/** — `rebalancing_persist_service` (the write surface, called by the ai_engine bridge); `rebalancing_module_service` (the gateway above); `rebal_engine/` — the compute engine, documented in its own `rebal_engine/CLAUDE.md`.

## Gotchas & invariants
- A run ALWAYS rebalances toward a persisted asset-allocation run — `source_allocation_run_id` is required (`services/rebalancing_persist_service.py`).
- The engine reuses a cached allocation for up to 90 days (`ALLOCATION_TTL_DAYS`); a chat override such as `additional_cash_inr` sets `force_fresh_allocation=True`, which skips that cache and re-runs allocation inline (`rebal_engine/service.py`). Miss this and chat returns a stale plan.

## Don't read
- `__pycache__/`.
