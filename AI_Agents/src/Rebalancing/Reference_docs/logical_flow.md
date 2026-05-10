# Rebalancing Module — Logical Flow & Code Structure

## Context

Building on [`input_parameter_spec.md`](input_parameter_spec.md), this plan converts the spreadsheet rebalancing logic (rows 279–323 of `goal_based_allocation_model_latest.xlsx`, sheet "Allocation 2") into a modular Python pipeline.

**Design priority: easy to edit in the future.** Achieved by:
1. Mirroring the proven `asset_allocation_pydantic` package layout already in the repo.
2. One file per pipeline step (no monolithic engine).
3. Pure functions with explicit input/output Pydantic models — no hidden state.
4. All I/O isolated to a single input-builder module; the engine itself is sync and DB-free.
5. Module-level constants in `config.py` (env-overrideable) — no magic numbers in step files.

---

## Package layout

Mirror the `asset_allocation_pydantic` structure (verified against [../asset_allocation_pydantic/](../../asset_allocation_pydantic/)).

```
AI_Agents/src/Rebalancing/
├── __init__.py
├── CLAUDE.md                        # module overview (per repo convention)
├── input_parameter_spec.md          # already saved
├── logical_flow.md                  # this document
├── config.py                        # env-overrideable knobs (buckets A & C)
├── models.py                        # Pydantic: request, FundRow*, response
├── pipeline.py                      # orchestrator: run_rebalancing(request)
├── tables.py                        # in-memory lookups (multi-cap subcat set, rank table cache)
├── utils.py                         # tiny stateless helpers
├── steps/
│   ├── __init__.py
│   ├── step1_cap_and_spill.py       # cols F → I → J → K
│   ├── step2_compare_and_decide.py  # cols L → M → N → O → P
│   ├── step3_tax_classification.py  # cols S → T → U → V → W
│   ├── step4_initial_trades_under_stcg_cap.py     # cols X → Y → Z → AA → AB → AC + AD–AL
│   ├── step5_loss_offset_top_up.py        # cols AM → AN → AO → AP
│   └── step6_presentation.py        # final response shape
└── Testing/
    ├── conftest.py                  # golden-fixture loader
    ├── test_step1_caps.py
    ├── test_step2_diff_exit.py
    ├── test_step3_tax.py
    ├── test_step4_initial_trades.py
    ├── test_step5_loss_offset.py
    ├── test_step6_presentation.py
    ├── test_e2e_workbook.py         # full sheet → engine, asserts col K, M, N, P, AC, AP within ₹1
    └── golden_fixtures/
        └── workbook_baseline.json   # extracted from goal_based_allocation_model_latest.xlsx
```

### Backend integration files (separate from the engine)

| File | Role | New / extend |
| --- | --- | --- |
| `app/services/ai_bridge/rebalancing_input_builder.py` | DB → `RebalancingComputeRequest`. **Only** module that touches the DB. Mirrors [`goal_allocation_input_builder.py`](../../../../app/services/ai_bridge/goal_allocation_input_builder.py). | New |
| `app/services/mf/holdings_aging.py` | Lot-level FIFO over `MfTransaction` → per-ISIN `(present, invested, st_value, st_cost, lt_value, lt_cost, units_in_exit_load_period)`. | New |
| `app/models/mf/mf_recommended_funds.py` + Alembic migration | Lookup table keyed `(asset_subgroup, sub_category, rank)` → `isin`. | New |
| `app/schemas/rebalancing.py` | Add request/response wrappers (thin, re-export `models.py` types). | Extend |
| `app/routers/rebalancing.py` | Add `POST /rebalancing/compute` endpoint. | Extend |

---

## Pipeline flow

```
RebalancingComputeRequest
        │
        ▼
   step1_cap_and_spill      ──► assigns max_pct, applies cap, spills overflow rank-1 → 2 → 3
        │                       sets:  max_pct, target_pre_cap_pct, target_own_capped_pct,
        │                              final_target_pct, final_target_amount
        ▼
   step2_compare_and_decide ──► joins present holdings, computes diff & flags
        │                       sets:  diff, exit_flag, worth_to_change
        │                       also: synthesises rows for held-but-not-recommended funds (BAD)
        ▼
   step3_tax_classification ──► for each sell candidate, splits ST/LT and computes exit load
        │                       sets:  stcg_amount, ltcg_amount, exit_load_amount
        ▼
   step4_initial_trades_under_stcg_cap    ──► first rebalance pass under STCG offset budget
        │                       sets:  pass1_buy_amount, pass1_underbuy_amount,
        │                              pass1_sell_amount, pass1_undersell_amount,
        │                              pass1_sell_lt_amount, _LTCG/LTCL,
        │                              pass1_sell_st_amount, _STCG/STCL,
        │                              stcg_budget_remaining_after_pass1,
        │                              pass1_sell_amount_no_stcg_cap,
        │                              pass1_undersell_due_to_stcg_cap(_value_STCG),
        │                              holding_after_initial_trades
        ▼
   step5_loss_offset_top_up       ──► uses carryforward losses to enable extra sells
        │                       sets:  stcg_offset_amount, pass2_sell_amount,
        │                              pass2_undersell_amount, final_holding_amount
        ▼
   step6_presentation       ──► aggregates to RebalancingComputeResponse (per-fund + totals + trade list)
        │
        ▼
RebalancingComputeResponse
```

---

## Key data structures (`models.py`)

### Per-step `FundRow` models (Decision 10: explicit, growing-by-inheritance)

Six models, one per step, each inheriting from the previous so each step's required fields are non-Optional and type-checked.

```python
# 0. Input row (built by input_builder; sections E + F)
class FundRowInput(BaseModel):
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    isin: str
    rank: int
    target_amount_pre_cap: Decimal              # allocation_1

    present_allocation_inr: Decimal = Decimal(0)
    invested_cost_inr: Decimal = Decimal(0)
    st_value_inr: Decimal = Decimal(0)
    st_cost_inr: Decimal = Decimal(0)
    lt_value_inr: Decimal = Decimal(0)
    lt_cost_inr: Decimal = Decimal(0)
    exit_load_pct: float = 0.0
    exit_load_months: int = 0
    units_within_exit_load_period: Decimal = Decimal(0)
    fund_rating: int = 10
    is_recommended: bool = True

# 1. After cap & spill
class FundRowAfterStep1(FundRowInput):
    max_pct: float
    target_pre_cap_pct: float
    target_own_capped_pct: float
    final_target_pct: float
    final_target_amount: Decimal

# 2. After compare & decide
class FundRowAfterStep2(FundRowAfterStep1):
    diff: Decimal
    exit_flag: bool
    worth_to_change: bool

# 3. After tax classification
class FundRowAfterStep3(FundRowAfterStep2):
    stcg_amount: Decimal
    ltcg_amount: Decimal
    exit_load_amount: Decimal

# 4. After initial trades (under STCG cap)
class FundRowAfterStep4(FundRowAfterStep3):
    pass1_buy_amount: Decimal
    pass1_underbuy_amount: Decimal
    pass1_sell_amount: Decimal
    pass1_undersell_amount: Decimal
    pass1_sell_lt_amount: Decimal
    pass1_realised_ltcg: Decimal
    pass1_sell_st_amount: Decimal
    pass1_realised_stcg: Decimal
    stcg_budget_remaining_after_pass1: Decimal
    pass1_sell_amount_no_stcg_cap: Decimal
    pass1_undersell_due_to_stcg_cap: Decimal
    pass1_blocked_stcg_value: Decimal
    holding_after_initial_trades: Decimal

# 5. After loss-offset top-up (final shape)
class FundRowAfterStep5(FundRowAfterStep4):
    stcg_offset_amount: Decimal
    pass2_sell_amount: Decimal
    pass2_undersell_amount: Decimal
    final_holding_amount: Decimal
```

Each step's signature is strictly typed:

```python
# step1_cap_and_spill.py
def apply(rows: list[FundRowInput], request: RebalancingComputeRequest) -> list[FundRowAfterStep1]: ...

# step2_compare_and_decide.py
def apply(rows: list[FundRowAfterStep1], request: RebalancingComputeRequest) -> list[FundRowAfterStep2]: ...

# ...and so on
```

Adding a column tomorrow: add one field to the right `FundRowAfterStepN`, update one step. Inheritance keeps later steps unchanged.

### `RebalancingComputeRequest` / `RebalancingComputeResponse`

Already specified in `input_parameter_spec.md` (buckets A–F). Response = `list[FundRowAfterStep5]` (final state) + summary totals + `trade_list: list[TradeAction]`.

---

## Module-level constants (`config.py`)

```python
import os
from decimal import Decimal

# Bucket A — caps & thresholds
MULTI_FUND_CAP_PCT = float(os.getenv("REBAL_MULTI_FUND_CAP_PCT", "20.0"))
OTHERS_FUND_CAP_PCT = float(os.getenv("REBAL_OTHERS_FUND_CAP_PCT", "10.0"))
REBALANCE_MIN_CHANGE_PCT = float(os.getenv("REBAL_MIN_CHANGE_PCT", "0.10"))
EXIT_FLOOR_RATING = int(os.getenv("REBAL_EXIT_FLOOR_RATING", "5"))

# Bucket C — tax limits
LTCG_ANNUAL_EXEMPTION_INR = Decimal(os.getenv("REBAL_LTCG_EXEMPTION_INR", "125000"))
STCG_RATE_EQUITY_PCT = float(os.getenv("REBAL_STCG_RATE_EQUITY", "20.0"))
LTCG_RATE_EQUITY_PCT = float(os.getenv("REBAL_LTCG_RATE_EQUITY", "12.5"))
ST_THRESHOLD_MONTHS_EQUITY = int(os.getenv("REBAL_ST_THRESHOLD_EQUITY", "12"))
ST_THRESHOLD_MONTHS_DEBT = int(os.getenv("REBAL_ST_THRESHOLD_DEBT", "24"))

# Sub-categories that get the "multi" cap (20%) instead of "others" (10%)
MULTI_CAP_SUB_CATEGORIES: frozenset[str] = frozenset({
    "Multi Cap Fund",
    # add others if & when the sheet's row 280 cap applies to them
})
```

Tunable without editing code; documented in `CLAUDE.md`.

---

## Step contracts

### `steps/step1_cap_and_spill.py`

**Spreadsheet refs:** cols F (`allocation_1`), G (`target_pre_cap_pct`), H (`max_pct`), I (`allocation_2+pct`), J (`final_target_pct`), K (`final_target_amount`).

**Pre-condition (built by `input_builder.py`):** the input row list already contains a `FundRowInput` for every `(asset_subgroup, sub_category, rank)` slot in `mf_recommended_funds`. Rank-1 rows carry the goal-allocation amount in `target_amount_pre_cap`; ranks 2+ rows arrive with `target_amount_pre_cap = 0`. Step 1 redistributes amounts across these pre-existing slots — it never adds rows.

**Logic:**
1. Group rows by `asset_subgroup`; within each, sort by `rank` ascending.
2. For each row: `max_pct = MULTI_FUND_CAP_PCT if sub_category in MULTI_CAP_SUB_CATEGORIES else OTHERS_FUND_CAP_PCT`.
3. Walk ranks in order: if rank-N's `pct > max_pct`, cap it and push the overflow (`(pct − max_pct) × corpus / 100`) into rank-(N+1)'s pre-cap amount. Repeat until all overflow absorbed or last rank reached (residual stays — feeds the "logic not covered" caveat in sheet row 325; engine raises a structured warning instead of silently dropping).
4. Set `final_target_amount = final_target_pct × corpus / 100`, rounded to `request.rounding_step`.

### `steps/step2_compare_and_decide.py`

**Spreadsheet refs:** cols L (`present_allocation`), M (`diff`), N (`Exit?`), O (`fund_ratings`), P (`worth_to_change`).

**Logic:**
1. Left-join `request.holdings` onto rows by `isin`; populate `present_allocation_inr`, `fund_rating`, etc.
2. **Synthesise BAD-fund rows** for any holding ISIN not in the recommended set (sheet row 322 pattern): `final_target_amount = 0`, `is_recommended = False`, target marked for full exit.
3. `diff = final_target_amount − present_allocation_inr`.
4. `exit_flag = (fund_rating < EXIT_FLOOR_RATING) OR (not is_recommended)`.
5. `worth_to_change = abs(diff) ≥ REBALANCE_MIN_CHANGE_PCT × max(final_target_amount, present_allocation_inr)` — OR `exit_flag` is True (force change for exits).

### `steps/step3_tax_classification.py`

**Spreadsheet refs:** cols S (`exit_load_amount`), T (`stcg_amount`), U (`st_investment_value`), V (`ltcg_amount`), W (`lt_investment_value`).

**Logic (per row where `worth_to_change` AND `diff < 0`, OR `exit_flag`):**
1. `stcg_amount = st_value_inr − st_cost_inr` (signed; can be negative loss).
2. `ltcg_amount = lt_value_inr − lt_cost_inr`.
3. `exit_load_amount = utils.compute_exit_load(units_within_exit_load_period × current_NAV, exit_load_pct)` — applied only when those specific units are sold (Step 4 prefers selling out-of-period units first).
4. Helpers in `utils.py`: `compute_stcg`, `compute_ltcg`, `compute_exit_load`.

### `steps/step4_initial_trades_under_stcg_cap.py`

**Spreadsheet refs:** cols X–AC (pass-1 buys/sells, `holding_after_initial_trades`) and AD–AL (LT/ST split, carryforward, w/o-ST counterfactual).

**Logic:**
1. **Desired buys** = rows with `diff > 0 AND worth_to_change` → total `target_buy`.
2. **Desired sells** = rows with `diff < 0 AND worth_to_change`, plus all `exit_flag = True` rows → total `target_sell`.
3. **Sell prioritisation — tax-first within forced and optional buckets** (Decision 8):
   - **Forced sells** (`is_recommended = False` OR `fund_rating < EXIT_FLOOR_RATING`): must fully exit, but the *order* of these exits within the run is tax-cheap-first — LT lots → realised losses → ST out-of-exit-load → ST in-exit-load.
   - **Optional sells** (`worth_to_change AND diff < 0` only — neither BAD nor low-rated): sorted strictly by tax-cheapness first (LT → losses → out-of-load → in-load), then by largest `|diff|` as a tie-break.
   - Net effect: the engine never lets a BAD/low-rated fund linger to save tax, but among funds that *could* be sold, the tax-cheap units always go first.
4. **STCG offset budget (D1).** Walk sells in priority order; for each, accumulate realised STCG. Stop selling more STCG once the budget is hit; route remaining demand to next-priority lot. Records `pass1_undersell_amount` and `pass1_blocked_stcg_value`.
5. **Cash conservation.** v1 assumes a closed system: `Σ buys = Σ sells`. If `target_buy > Σ allowed sells`, scale buys down proportionally and record `pass1_underbuy_amount`.
6. **Counterfactual w/o ST limit** (col AI/AJ/AK) — re-run sell prioritisation ignoring D1, capture deltas. Cheap because the prioritisation is a generator; just run it twice with different filters.
7. Compute `holding_after_initial_trades = present + pass1_buy_amount − pass1_sell_amount`.

### `steps/step5_loss_offset_top_up.py`

**Spreadsheet refs:** cols AH (`stcg_budget_remaining_after_pass1`), AL (`stcg_offset_amount`), AM–AP.

**Logic:**
1. `available_loss_offset = request.carryforward_st_loss_inr + request.carryforward_lt_loss_inr + Σ(realised losses from pass-1)`.
2. `stcg_offset_amount = min(realised_stcg_pass_1, available_loss_offset)`. This is "headroom unlocked".
3. Re-run a tiny prioritisation pass: take rows with `pass1_undersell_due_to_stcg_cap > 0` and convert as much as the headroom allows into `pass2_sell_amount`.
4. `final_holding_amount = holding_after_initial_trades − pass2_sell_amount`.

### `steps/step6_presentation.py`

**Spreadsheet refs:** none — engine output assembly.

**Logic:**
1. Build `trade_list = [TradeAction(isin, action="BUY"|"SELL", amount, reason)]` from non-zero buys/sells.
2. Compute totals: `total_buy`, `total_sell`, `total_stcg`, `total_ltcg`, `total_tax_estimate`, `total_exit_load`, `unrebalanced_remainder` (if any rank-N overflow couldn't fit).
3. Wrap rows + totals into `RebalancingComputeResponse`.

---

## Helpers (`utils.py`)

Tiny pure functions, one responsibility each. Each gets a unit test.

```python
def get_max_pct(sub_category: str) -> float: ...
def compute_stcg(st_value: Decimal, st_cost: Decimal) -> Decimal: ...
def compute_ltcg(lt_value: Decimal, lt_cost: Decimal, exemption: Decimal) -> Decimal: ...
def compute_exit_load(amount: Decimal, exit_load_pct: float) -> Decimal: ...
def round_to_step(amount: Decimal, step: int) -> Decimal: ...     # honours request.rounding_step
def estimate_tax(stcg: Decimal, ltcg: Decimal, regime: str) -> Decimal: ...
```

---

## Testing strategy

Per-step golden fixtures + one end-to-end fixture from the workbook itself:

| Test file | Fixture | Asserts |
| --- | --- | --- |
| `test_step1_caps.py` | hand-crafted: subgroup with rank-1 over cap | `final_target_pct ≤ max_pct`, overflow lands in rank-2 |
| `test_step2_diff_exit.py` | rows 286, 322 of sheet | `diff`, `Exit?`, `worth_to_change` match sheet col M, N, P |
| `test_step3_tax.py` | row 286 + rows 309, 312 | `exit_load_amount`, `stcg_amount`, `ltcg_amount` match cols S, T, V |
| `test_step4_initial_trades.py` | full row set with STCG budget = sheet's `O283` value (0.4379…) | `holding_after_initial_trades`, `sold_rebal_1_*` match cols AC + AD–AL |
| `test_step5_loss_offset.py` | same + carryforward losses | `final_holding_amount` matches col AP |
| `test_step6_presentation.py` | small synthetic | `trade_list` has correct ISINs and signs |
| `test_e2e_workbook.py` | `golden_fixtures/workbook_baseline.json` | row-by-row col K, M, N, P, AC, AP within ₹1 |

`golden_fixtures/workbook_baseline.json` is generated once by a small dev-only script (`Testing/extract_workbook_fixture.py`) reading the .xlsx and dumping inputs + expected outputs. **Source of truth stays the .xlsx**; the JSON is regenerated when the sheet changes.

---

## Critical files to be created (in dependency order)

1. `AI_Agents/src/Rebalancing/config.py`
2. `AI_Agents/src/Rebalancing/models.py`
3. `AI_Agents/src/Rebalancing/utils.py`
4. `AI_Agents/src/Rebalancing/tables.py`
5. `AI_Agents/src/Rebalancing/steps/step1_cap_and_spill.py`
6. `AI_Agents/src/Rebalancing/steps/step2_compare_and_decide.py`
7. `AI_Agents/src/Rebalancing/steps/step3_tax_classification.py`
8. `AI_Agents/src/Rebalancing/steps/step4_initial_trades_under_stcg_cap.py`
9. `AI_Agents/src/Rebalancing/steps/step5_loss_offset_top_up.py`
10. `AI_Agents/src/Rebalancing/steps/step6_presentation.py`
11. `AI_Agents/src/Rebalancing/pipeline.py`
12. `AI_Agents/src/Rebalancing/Testing/` (all)
13. `app/services/mf/holdings_aging.py`
14. `app/models/mf/mf_recommended_funds.py` + Alembic migration
15. `app/services/ai_bridge/rebalancing_input_builder.py`
16. `app/schemas/rebalancing.py` (extend)
17. `app/routers/rebalancing.py` (extend with `POST /compute`)

---

## Verification (end to end)

1. Run `pytest AI_Agents/src/Rebalancing/Testing/` — all step tests + e2e workbook test pass.
2. Spin up the FastAPI app, hit `POST /rebalancing/compute` with the user_id whose Finvu data feeds the workbook example, assert response equals `workbook_baseline.json`'s `expected_output`.
3. Smoke: change `REBAL_OTHERS_FUND_CAP_PCT=15` env var, re-run, confirm cap-spill behaviour shifts as expected.

---

## Decisions confirmed (this round)

8. **Sell prioritisation** — tax-first ordering, applied within both the forced-exit bucket (BAD + low-rated) and the optional bucket (over-allocated). LT → losses → ST out-of-load → ST in-load is the tax-cheapness ladder; |diff| breaks ties inside the optional bucket.
9. **Cash flow** — closed system in v1 (Σ buys = Σ sells). Inflow/outflow handling deferred.
10. **Row shape** — explicit per-step Pydantic models inheriting from one another (`FundRowInput` → `FundRowAfterStep1` → … → `FundRowAfterStep5`).
