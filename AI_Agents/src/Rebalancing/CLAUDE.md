# AI_Agents/src/Rebalancing — tax-aware rebalancing engine

Pure-Python. Takes a goal-based ideal allocation plus present holdings, emits per-fund target / buy / sell amounts under per-fund caps with tax-aware sell prioritisation (STCG offset budget + carryforward losses). Mirrors the layout of `asset_allocation_pydantic`.

## Entry / contract
- Entry `run_rebalancing(request) → RebalancingComputeResponse` (`pipeline.py`).
- Input `RebalancingComputeRequest`: corpus, tax state, and one homogeneous `FundRowInput` list. Recommended funds carry `rank ≥ 1` (rank-1 holds the goal-allocation amount); held-but-not-recommended ("BAD") funds carry `rank = 0`, `is_recommended = False`, `target_amount_pre_cap = 0`. The upstream input builder (`app/domains/rebalancing/services/`) materialises both.
- Output: rows after step 5, totals, trade list, warnings, metadata.

## Files
- `pipeline.py` — orchestrator; runs the practical allocation, then `steps/` 1–6 (`step1_cap_and_spill` … `step6_presentation`) with `step2b_suppress_debt_switch` between steps 2 and 3.
- `models.py`, `config.py` (env-overrideable knobs), `tables.py`, `utils.py`, `rationales.py` (customer-facing reason-code strings).
- `Reference_docs/` — design docs + source workbook (planning, not code).

## Gotchas & invariants
- **ELSS is scalar, not row.** ELSS exposure lives on `practical_allocation_input.elss_corpus`; ELSS rows are filtered out upstream. `step6` emits a frozen `SubgroupSummary` for `tax_efficient_equities` so the view shows the allocation, but never a BUY/SELL/EXIT trade for it (SEBI 3-year lock-in).
- **Non-MF equity is scalar, not row.** Direct-stock / PMS holdings live on `practical_allocation_input.non_mf_equity_corpus`. When the practical NFA-banded cap forces a trim, `step6` emits a single `SELL_DIRECT_STOCKS` action for `excess_direct_stocks_inr` — no per-stock trades.
- **`FORCE_EXIT_RANK = 9999` is duplicated** in app-side `fund_rank.py` (the CSV loader) and must stay in sync; the sentinel marks rows the input builder wants force-exited (`config.py`).
- **Sell-ordering is regulatory.** STCG is never realised on a recommended-fund trim (optional sells are LT-only — STCG only on force-exit); sells walk LT→ST first, LT being the cheaper bucket, under the STCG budget (`steps/step4_initial_trades_under_stcg_cap.py`).
- **Loss-offset uses SHORT-term losses only** — an LT capital loss may set off only LTCG, never STCG (`steps/step5_loss_offset_top_up.py`).
- **Per-fund cap is floored in rupees** (`steps/step1_cap_and_spill.py`, amendment 2026-07-06): `cap = max(cap_pct × corpus, FUND_CAP_FLOOR_INR)` (default ₹1L, env `REBAL_FUND_CAP_FLOOR_INR`) — small corpora neither fragment into sub-₹1L buys nor get trimmed to satisfy tiny percentage caps; `max_pct` on fund rows reports the EFFECTIVE cap when the floor wins, and the floor is stamped into `KnobSnapshot.fund_cap_floor_inr`.
- **Debt is never sold to buy debt** (`steps/step2b_suppress_debt_switch.py`, 2026-07-18): matched sell/buy *intents* across `{short_debt, arbitrage, arbitrage_plus_income}` are cancelled before step3, so the tax arithmetic runs once on the corrected picture — `scale`/`floor_to_step` in step4 are not invertible, so this cannot be done later. Carve-outs: `exit_flag` and `rank == 0` sells stay (a bad fund is still bad; off-list rows must still migrate), and force-exit proceeds are reserved out of the buy side. Consequence accepted by product: a debt fund may sit **above** its per-fund cap indefinitely — the cap governs deployment, not custody. Kill-switch `REBAL_DEBT_SWITCH_NETTING=0`.
- **Bump `ENGINE_VERSION` on any output-altering logic change** (`config.py`) — it is stamped into response metadata for cache/repro tracking.

## Depends on
- `pydantic`; and `practical_asset_allocation` (spec §B.1 peer-isolation exception) — `run_rebalancing` calls `run_practical_allocation` first and surfaces its output on `RebalancingComputeResponse.practical_allocation`.

## Testing
- `pytest AI_Agents/src/Rebalancing/Testing -v` (per-step + e2e; `test_e2e_workbook.py` skipped pending fixture extraction from the `Reference_docs/` workbook).
- `Testing/Master_testing/runner.py` — dev-only 5-profile sweep dumping `results/` for the UI; replaced by the real app route + input builder in production.

## Don't read
- `__pycache__/`, `Testing/`, `Reference_docs/` cached artifacts (`*.xlsx` is the e2e-fixture source-of-truth, not application data).
