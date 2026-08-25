# app/domains/rebalancing/ — rebalancing runs, trades, fund rows, subgroup summaries, warnings

## Entry / contract
- `rebalancing_module_service` is the gateway on the chat path — the brain calls its `run(turn, ctx, prior)`. One other live caller bypasses it: the debug / frontend-driven route `POST /api/v1/ai-modules/rebalancing/compute` (`app/domains/ai_engine/routers/rebalancing_router.py`) calls `rebal_engine.service.compute_rebalancing_result` directly and commits the session itself. The AI bridge that *produces* the source allocation lives in `app/domains/ai_engine`. The source allocation a run rebalances toward is the latest `AssetAllocationRun` (plus its IDEAL `PortfolioAllocationSnapshot`) written by the `asset_allocation` domain, read cache-first with a 90-day TTL and, on a miss, recomputed inline via `asset_allocation`'s `aa_engine.compute_allocation_result` — nothing in `app/domains/ai_engine` produces it.

## Layers
- **models/** — `RebalancingRun` and its children `RebalancingTrade` / `RebalancingFundRow` / `RebalancingSubgroupSummary` / `RebalancingWarning`.
- **schemas/** — the run-API contract (pydantic views over the run tables) in `__init__.py` — including the read-time computed `summary` and `asset_class_breakdown` — plus `readiness` (payload for `GET /rebalancing/readiness`).
- **routers/** — the `/rebalancing` router.
- **services/** — `rebalancing_persist_service` (the write surface, called at the end of a run by `rebal_engine/service.py`); `rebalancing_read_service` (read-side helper over persisted runs — `additional_investment`'s SIP path mirrors the latest run's BUY trades through it); `rebalancing_module_service` (the gateway above); `rebalancing_summary.py` (pure, DB-free headline — title/subtitle/reason — behind the run-detail `summary` computed field); `asset_class_breakdown.py` (THE current-vs-target Equity/Debt/Others rollup — `asset_class_mix_from_rows`, shared by the Invest-page bars AND the rebalancing chat facts pack, see Gotchas); `rebal_engine/` — the compute engine, documented in its own `rebal_engine/CLAUDE.md`.

## Gotchas & invariants
- **One asset-class rollup, two surfaces** — `asset_class_mix_from_rows` (`services/asset_class_breakdown.py`) feeds both the Invest bars and the chat facts pack; separate rollups once reported 98/1/0 vs 95/3/2 for the same holdings. CURRENT looks each row through on its own `sub_category`; TARGET does too EXCEPT the `multi_asset` sleeve, which keeps the engine's 65/25/10 composition — look it through and an equity-heavy pick (a Flexi Cap fund does land there) deletes the plan's debt.
- **Per-fund target is `present + buys − sells`, never `final_target_amount`** — that column is a per-candidate uncapped target and sums ~30% above the portfolio (`plan_rows_from_run`).
- **The pack ships current AND target AND ideal.** With only current present, the formatter answered "what is the plan moving me toward?" by citing it, then invented lock-ins to explain the mismatch (`rebal_engine/service.py` `build_rebal_facts_pack`).
- A run ALWAYS rebalances toward a persisted asset-allocation run — `source_allocation_run_id` is required (`services/rebalancing_persist_service.py`).
- The engine reuses a cached allocation for up to 90 days (`ALLOCATION_TTL_DAYS`); a chat override such as `additional_cash_inr` sets `force_fresh_allocation=True`, which skips that cache and re-runs allocation inline (`rebal_engine/service.py`). Miss this and chat returns a stale plan.
- **Preference turns are stateless** (`persist=False`; spec 2026-08-24): the audit trail is `constraint_impact.applied_preferences` (payload + which magnitude default fired), and every unserved ask emits the `preference_unserved` PostHog event carrying ids only, never chat text (`rebal_engine/chat.py`, `app/core/observability.py`). Tilt turns run the engine twice (recommended + requested) and the facts pack is dual-source — the `tilt_note` labels which figures belong to which plan; don't let the formatter blend them.

## Don't read
- `__pycache__/`.
