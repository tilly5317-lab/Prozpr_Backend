# Rebalancing Module — Input Parameter Spec

## Context

We are designing a new **Rebalancing module** in the Prozpr_Backend repo, modelled on the spreadsheet `Local_logics/Rebalancing_logic/goal_based_allocation_model_latest.xlsx`, sheet **"Allocation 2"**, section starting at row **279 ("MF allocation & Rebalancing")**.

The engine takes the goal-based ideal allocation (already computed upstream) and walks through six steps. Full step contracts are in [`logical_flow.md`](logical_flow.md).

1. **`step1_cap_and_spill`** — per-fund target sizing under per-fund caps; overflow spills rank-1 → 2 → 3 (sheet cols F–K).
2. **`step2_compare_and_decide`** — joins present holdings, computes `diff`, `exit_flag`, `worth_to_change` (cols L–P).
3. **`step3_tax_classification`** — for each sell candidate, splits ST/LT and computes exit-load impact (cols S–W).
4. **`step4_initial_trades_under_stcg_cap`** — first trade pass under the STCG offset budget; emits `holding_after_initial_trades` (cols X–AC + AD–AL).
5. **`step5_loss_offset_top_up`** — uses carryforward losses to enable extra sells; emits `final_holding_amount` (cols AM–AP).
6. **`step6_presentation`** — assembles the response (no spreadsheet col; engine output).

**Note on `allocation_4_amount` (manual step) — out of scope for v1.** Col Q in the sheet is the advisor's manual override of the engine's auto target (e.g. row 322 partial-exits a "BAD fund" instead of full liquidation). v1 sets `allocation_4 = allocation_3` and surfaces no override input. A future version can add an optional `{isin: override_amount}` map to `RebalancingComputeRequest`.

This plan covers **only the input contract**. Stage-by-stage logic will be planned in a follow-up.

---

## What exists vs what we'll build

### Reuse as-is (do not modify)

| Need | File / location |
| --- | --- |
| Goal-based allocation output (target per asset_subgroup, **rank-1** recommended fund) | [GoalAllocationOutput.aggregated_subgroups](../../asset_allocation_pydantic/models.py:193) → `AggregatedSubgroupRow.fund_mapping` ([models.py:121](../../asset_allocation_pydantic/models.py:121)) is a **single** `SubgroupFundMapping` ([models.py:92](../../asset_allocation_pydantic/models.py:92)), not a list |
| Raw transactions | [MfTransaction](../../../../app/models/mf/mf_transaction.py:34) |
| Fund metadata (sub_category, asset_subgroup, exit_load_percent, exit_load_months, our_rating_parameter_*) | [MfFundMetadata](../../../../app/models/mf/mf_fund_metadata.py:33) |
| Existing CRUD router on saved recommendations | [app/routers/rebalancing.py](../../../../app/routers/rebalancing.py) (already wired to `RebalancingRecommendation`) |
| Persistence model for recommendations | [app/models/rebalancing.py](../../../../app/models/rebalancing.py) (`recommendation_data` JSONB) |
| Pattern for module config (env-var overrides) | [goal_allocation_input_builder.py:21](../../../../app/services/ai_bridge/goal_allocation_input_builder.py:21) |
| Per-(scheme_code, folio) aggregation reference logic | [simbanks_service.py:457–506](../../../../app/services/simbanks_service.py:457) |

### Extend (existing file, additive only)

| File | What we add |
| --- | --- |
| [app/schemas/rebalancing.py](../../../../app/schemas/rebalancing.py) | New `RebalancingComputeRequest`, `RebalancingComputeResponse`, and supporting per-fund row schemas |

### Create new

The engine itself lives **inside `AI_Agents/src/Rebalancing/`** (mirroring the `asset_allocation_pydantic` package — see [AI_Agents/src/CLAUDE.md](../../CLAUDE.md)). Only the I/O layer lives under `app/`.

| New file / module | Purpose |
| --- | --- |
| `AI_Agents/src/Rebalancing/config.py` | Module-level defaults + `os.getenv` overrides for buckets A and C (Decision 2) |
| `AI_Agents/src/Rebalancing/models.py` | Pydantic: `RebalancingComputeRequest`, `FundRowInput`…`FundRowAfterStep5`, `RebalancingComputeResponse` |
| `AI_Agents/src/Rebalancing/pipeline.py` | Orchestrator: `run_rebalancing(request) → response` |
| `AI_Agents/src/Rebalancing/steps/step{1..6}_*.py` | One step per file; full list and contracts live in `logical_flow.md` |
| `AI_Agents/src/Rebalancing/utils.py`, `tables.py` | Stateless helpers + in-memory lookups |
| `app/services/ai_bridge/rebalancing_input_builder.py` | DB → `RebalancingComputeRequest`. Mirrors [goal_allocation_input_builder.py](../../../../app/services/ai_bridge/goal_allocation_input_builder.py). The **only** module touching the DB. |
| `app/services/rebalancing/customer_view_adapter.py` | Flattens `internal_view → customer_view` for API/UI consumers (Decision 12). |
| `app/services/mf/holdings_aging.py` | Lot-level FIFO walk over `MfTransaction` → per-ISIN `(present, invested, st_value, st_cost, lt_value, lt_cost, units_within_exit_load_period)`. The aggregation pattern in `simbanks_service.py:457` aggregates per `(scheme_code, folio_no)` only and does **not** age lots, so this is new work. |
| `app/models/mf/mf_recommended_funds.py` + Alembic migration | New lookup table keyed `(asset_subgroup, sub_category, rank)` → ISIN, mirroring the workbook's `Table` sheet rows 2–20+. Used by `input_builder.py` to fetch ranks 2/3. |

---

## Input Parameters

Five buckets. Each row: **name · type · default (if any) · source · what it drives in the sheet**.

### A. Configuration knobs (rows 280–283 of sheet) — module-level via env

| # | Name | Type | Default | Drives |
| --- | --- | --- | --- | --- |
| A1 | `multi_fund_cap_pct` | float | 20.0 | Per-fund cap when `sub_category` ∈ multi-cap set (col H, rows 302–304) |
| A2 | `others_fund_cap_pct` | float | 10.0 | Per-fund cap for every other sub_category (col H) |
| A3 | `rebalance_min_change_pct` | float | 0.10 | `worth_to_change` flag — skip rebalance if abs(diff) < this fraction of target (col P) |
| A4 | `exit_floor_rating` | int | 5 | Force `exit_flag=True` (sheet col N `Exit?`) when fund's `our_rating_parameter_1` < this |

### B. Portfolio context (per-request, from client profile)

| # | Name | Type | Source | Drives |
| --- | --- | --- | --- | --- |
| B1 | `total_corpus` | Decimal | client profile / Finvu | Denominator for `allocation_*_pct` |
| B2 | `tax_regime` | enum {"old","new"} | user profile | STCG/LTCG rate selection |
| B3 | `effective_tax_rate_pct` | float | profile | Slab-rate fallback for STCG when applicable |
| B4 | `rounding_step` | int | profile (`Round to 100/1000`) | Round buy/sell amounts |

### C. Tax / capital-gains limits — module-level via env

| # | Name | Type | Default | Drives |
| --- | --- | --- | --- | --- |
| C1 | `ltcg_annual_exemption_inr` | Decimal | 125000 (FY25-26 equity LTCG threshold) | Exempt slab before LT tax |
| C2 | `stcg_rate_equity_pct` | float | 20.0 | Tax on ST equity gains |
| C3 | `ltcg_rate_equity_pct` | float | 12.5 | Tax on LT equity gains above C1 |
| C4 | `stcg_holding_threshold_months` | int | 12 (equity) / 24 (debt FoF) | Split holdings into ST vs LT |

### D. Per-request capital-gains state

| # | Name | Type | Default | Notes |
| --- | --- | --- | --- | --- |
| D1 | `stcg_offset_budget_inr` | Decimal \| None | None | Optional cap on STCG the engine may realise in pass-1 (drives col AI/AJ logic) |
| D2 | `carryforward_st_loss_inr` | Decimal | 0 | Pass-2 net-off (col AH) |
| D3 | `carryforward_lt_loss_inr` | Decimal | 0 | Pass-2 net-off (col AH) |

### E. Goal-based allocation feed — per asset_subgroup, ordered by rank

| # | Field | Type | Source |
| --- | --- | --- | --- |
| E1 | `asset_subgroup` | str | `AggregatedSubgroupRow.subgroup` |
| E2 | `sub_category` | str | join via ISIN → `MfFundMetadata.sub_category` |
| E3 | `recommended_fund` | str | `SubgroupFundMapping.recommended_fund` |
| E4 | `isin` | str | `SubgroupFundMapping.isin` |
| E5 | `rank` | int | new `mf_recommended_funds` lookup table |
| E6 | `target_amount_pre_cap` (= `allocation_1`) | Decimal | `AggregatedSubgroupRow.total` allocated to this rank (rank-1 gets the goal-allocation amount; ranks 2/3 start at 0 and only fill on cap spill) |
| E7 | `max_pct` | float | derived: A1 if `sub_category` is multi-cap else A2 |

**How ranks 2/3 are wired (Decision 6).** `GoalAllocationOutput.fund_mapping` exposes only rank-1. `input_builder.py` reads ranks 2+ from the new `mf_recommended_funds` table and materialises a `FundRowInput` for every `(asset_subgroup, sub_category, rank)` combination — rank-1 carries the goal-allocation amount, ranks 2+ start with `target_amount_pre_cap = 0`. By the time `step1_cap_and_spill` runs, every potential overflow target already exists; the step only redistributes amounts, never lazy-loads new rows.

### F. Present holdings — per ISIN held, including non-recommended ("BAD") funds

Built fresh by the new `holdings_aging.py` walking `MfTransaction` directly. **Drop `ActualHolding`** — not needed; same data is derivable from `MfTransaction`, and FIFO ageing requires lot-level access anyway.

| # | Field | Type | Source |
| --- | --- | --- | --- |
| F1 | `isin` | str | MfTransaction → MfFundMetadata |
| F2 | `scheme_code` | str | MfTransaction |
| F3 | `present_allocation_inr` | Decimal | Σ(units_balance × current_NAV) |
| F4 | `invested_cost_inr` | Decimal | Σ(net_invested) |
| F5 | `st_value_inr` | Decimal | FIFO lots aged < C4 months → market value |
| F6 | `st_cost_inr` | Decimal | cost basis of those ST lots |
| F7 | `lt_value_inr` | Decimal | FIFO lots ≥ C4 months → market value |
| F8 | `lt_cost_inr` | Decimal | cost basis of those LT lots |
| F9 | `exit_load_pct` | float | `MfFundMetadata.exit_load_percent` |
| F10 | `exit_load_months` | int | `MfFundMetadata.exit_load_months` |
| F11 | `units_within_exit_load_period` | Decimal | from FIFO aging |
| F12 | `fund_rating` | int | `MfFundMetadata.our_rating_parameter_1` (Decision 7) |
| F13 | `is_recommended` | bool | True if ISIN ∈ section E for any subgroup; else "BAD fund" (sheet row 322) |

---

## Decisions confirmed

1. **ST/LT split** — lot-level FIFO from `MfTransaction` (new `app/services/mf/holdings_aging.py`).
2. **Knob source** — module-level defaults + `os.getenv` overrides in `AI_Agents/src/Rebalancing/config.py`. Buckets A and C live there; bucket D stays per-request.
3. **v1 scope** — pass-1 (`step4_initial_trades_under_stcg_cap`) **and** pass-2 (`step5_loss_offset_top_up`) — sheet cols X–AP.
4. **Engine output shape** — full intermediate state (`final_target_amount`, `holding_after_initial_trades`, `final_holding_amount`, diffs, `exit_flag`, `worth_to_change`, tax breakdown). External views are spec'd in [`output_spec.md`](output_spec.md).
5. **Manual override (`allocation_4`)** — out of scope for v1; engine sets `allocation_4 = allocation_3`.
6. **Rank source** — new `mf_recommended_funds` lookup table; `input_builder.py` materialises rows for **all** ranks (rank-1 with the goal-allocation amount, ranks 2+ with `target_amount_pre_cap = 0`) so step 1 only has to spill, never lazy-load.
7. **Rating column** — `MfFundMetadata.our_rating_parameter_1`.
8. **Sell prioritisation** — tax-first ordering, applied within both the forced-exit bucket (BAD + low-rated) and the optional bucket (over-allocated). LT → losses → ST out-of-load → ST in-load is the tax-cheapness ladder; |diff| breaks ties inside the optional bucket.
9. **Cash flow** — closed system in v1 (Σ buys = Σ sells). Inflow/outflow handling deferred.
10. **Row shape** — explicit per-step Pydantic models inheriting from one another (`FundRowInput` → `FundRowAfterStep1` → … → `FundRowAfterStep5`).
11. **Persistence** — full internal_view + a `knob_snapshot` (env-var values used at compute time) is stored in `RebalancingRecommendation.recommendation_data` JSONB. Customer view is **not** stored; it's derived on read.
12. **Customer view adapter** — lives at `app/services/rebalancing/customer_view_adapter.py` (backend service module — keeps the AI_Agents engine pure).

---

## Verification (for follow-up impl)

**Test conventions**, mirroring [AI_Agents/src/asset_allocation_pydantic/Testing/](../../asset_allocation_pydantic/Testing):
- Per-step test files: `test_step1_caps.py`, `test_step2_diff_exit.py`, `test_step3_tax.py`, `test_step4_initial_trades.py`, `test_step5_loss_offset.py`, `test_step6_presentation.py`.
- One end-to-end test: `test_e2e_workbook.py` → row-by-row assertion against the workbook.
- Shared `Testing/conftest.py` with a golden-fixture loader.
- JSON golden fixtures under `Testing/golden_fixtures/workbook_baseline.json`, generated from `goal_based_allocation_model_latest.xlsx` by a small dev-only `Testing/extract_workbook_fixture.py` script.

**End-to-end golden test:** load Client Profile (rows 5–22) and per-fund holdings (col L of rows 286–322) from the workbook into the engine; assert that `final_target_amount`, `diff`, `exit_flag`, `worth_to_change`, `holding_after_initial_trades`, and `final_holding_amount` match the sheet's values within ₹1.

The full test layout lives in [`logical_flow.md`](logical_flow.md) under **Testing strategy**.
