# AI_Agents/src/Rebalancing — tax-aware rebalancing engine

Pure-Python. Takes a goal-based ideal allocation plus present holdings, emits per-fund target / buy / sell amounts under per-fund caps with tax-aware sell prioritisation (STCG offset budget + carryforward losses). Mirrors the layout of `asset_allocation_pydantic`.

## Entry / contract
- Entry `run_rebalancing(request) → RebalancingComputeResponse` (`pipeline.py`).
- Input `RebalancingComputeRequest`: corpus, tax state, and one homogeneous `FundRowInput` list. Recommended funds carry `rank ≥ 1` (rank-1 holds the goal-allocation amount). Off-list held funds come in two flavours, both `is_recommended = False`: **force-exit** — `rank = FORCE_EXIT_RANK` (9999), `target_amount_pre_cap = 0`; step2 sets `exit_flag`, step4 liquidates regardless of tax — and **NEUTRAL** — `rank = 0`, `target_amount_pre_cap = st_value_inr` (the locked ST minimum), so `diff = -lt_value` and only the migratable LT portion reads as sellable. The upstream input builder (`app/domains/rebalancing/services/rebal_engine/input_builder.py`) materialises all three.
- Output: rows after step 5, totals, trade list, warnings, metadata.

## Files
- `pipeline.py` — orchestrator; runs the practical allocation, then `steps/` 1–6 (`step1_cap_and_spill` … `step6_presentation`).
- `models.py` — pydantic I/O; per-step `FundRowAfterStepN` models inherit down the chain so each step's added fields are non-Optional.
- `config.py` — knobs: per-fund caps, tax rates/thresholds (env-overrideable), plus hardcoded `FORCE_EXIT_RANK` and `ENGINE_VERSION`.
- `tables.py` — per-subgroup cap lookup (`cap_pct_for`, default `OTHERS_FUND_CAP_PCT`); also read cross-domain by the additional-investment input builder — do not rename blind.
- `utils.py` — Decimal rounding (`round_to_step`, `floor_to_step`), gross STCG/LTCG, and `estimate_tax`; no state, no I/O.
- `rationales.py` — customer-facing reason-code strings.
- `consolidation.py` — F3-B buy-side reshape over a `RebalancingComputeResponse`: redistributes only the buy budget across allowed funds (total buy + every sell preserved); pure/stateless, consumed app-side by `rebal_engine`. Preference constraints (2026-08 spec) add `excluded_categories` + `category_weight_targets`, composed filters→weights→count (count bumps up to the protected-category count) via `_reshape_extended`. `_reshape_legacy` handles the two paths free of exclusions/weights, and they no longer behave alike: `allowed_categories` keeps the original portfolio-wide "redeploy the whole budget into these categories" arithmetic byte-for-byte, but a bare `target_fund_count` is now SUBGROUP-AWARE — it preserves each `asset_subgroup`'s own buy total and floors the kept count at the number of subgroups with buys, so a fund-count trim can never undo a market-cap tilt.
- `Reference_docs/` — design docs + source workbook (planning, not code).

## Gotchas & invariants
- **Subgroup targets are lifted in the pipeline, not supplied.** `_assign_subgroup_targets` splits each MF subgroup's practical-engine total across its ranked rows, less (floored at 0) the ST value of that subgroup's NEUTRAL (`rank = 0`) rows — locked ST is already exposure, so skipping the offset double-allocates. Whatever the input builder set is discarded; frozen subgroups are exempt. Keep the rule here only, or the two builders drift (`pipeline.py`).
- **ELSS is scalar, not row.** ELSS exposure lives on `practical_allocation_input.elss_corpus`; ELSS rows are filtered out upstream. `step6` emits a frozen `SubgroupSummary` for `tax_efficient_equities` so the view shows the allocation, but never a BUY/SELL/EXIT trade for it (SEBI 3-year lock-in).
- **Non-MF equity is scalar, not row.** Direct-stock / PMS holdings live on `practical_allocation_input.non_mf_equity_corpus`. When the practical NFA-banded cap forces a trim, `step6` emits a single `SELL_DIRECT_STOCKS` action for `excess_direct_stocks_inr` — no per-stock trades.
- **`FORCE_EXIT_RANK = 9999` is duplicated** in app-side `fund_rank.py` (the CSV loader) and must stay in sync; the sentinel marks rows the input builder wants force-exited (`config.py`).
- **Sell-ordering is regulatory.** STCG is never realised on a recommended-fund trim (optional sells are LT-only — STCG only on force-exit); sells walk LT→ST first, LT being the cheaper bucket, under the STCG budget (`steps/step4_initial_trades_under_stcg_cap.py`).
- **Loss-offset uses SHORT-term losses only** — an LT capital loss may set off only LTCG, never STCG (`steps/step5_loss_offset_top_up.py`).
- **Bump `ENGINE_VERSION` on any output-altering logic change** (`config.py`) — it is stamped into response metadata for cache/repro tracking.

## Depends on
- `pydantic`; and `practical_asset_allocation` (spec §B.1 peer-isolation exception) — `run_rebalancing` calls `run_practical_allocation` first and surfaces its output on `RebalancingComputeResponse.practical_allocation`.

## Testing
- `pytest AI_Agents/src/Rebalancing/Testing -v` (per-step + e2e; `test_e2e_workbook.py` skipped pending fixture extraction from the `Reference_docs/` workbook).
- `Testing/Master_testing/runner.py` — dev-only 5-profile sweep dumping `results/` for the UI; replaced by the real app route + input builder in production.

## Don't read
- `__pycache__/`, `Testing/`, `Reference_docs/` cached artifacts (`*.xlsx` is the e2e-fixture source-of-truth, not application data).
