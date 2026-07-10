# Allocation + Rebalancing v2 — Design Spec

**Status:** Approved. Handoff to `writing-plans` — to produce three separate implementation plans (one per Part) at `docs/superpowers/plans/2026-05-23-allocation-rebalancing-v2-part-{a,b,c}-*-plan.md`.
**Source of truth:** `Local_logics/Sourabh_Logics/goal_based_allocation_model (12) (1).xlsx`, sheet **Allocation 4**.
**Affected modules:**
- `AI_Agents/src/asset_allocation_pydantic/` — minor changes (Part A).
- `AI_Agents/src/practical_asset_allocation/` — NEW peer module (Part B).
- `AI_Agents/src/Rebalancing/` — consumes practical_asset_allocation (Part C).

## Architecture

```
Customer profile + goals + market view
            │
            ▼
┌──────────────────────────────────────────┐
│  asset_allocation_pydantic                │  HOLDINGS-AGNOSTIC
│  (Part A — 5 surgical changes)            │  Output: GoalAllocationOutput
└──────────────────────────────────────────┘
            │
            ▼
   Customer profile/goals/market view
   + 4 corpus scalars (total / mf / non-MF equity / ELSS) + NFA
            │
            ▼
┌──────────────────────────────────────────┐
│  practical_asset_allocation (NEW)         │  HOLDINGS-AWARE
│  (Part B — new peer module)               │  Output: PracticalAllocationOutput
│                                            │  Shape-parity with GoalAllocationOutput
│                                            │  + CorpusBreakdown extras
└──────────────────────────────────────────┘
            │
            │  + per-fund MF rows (from MfTransaction; ELSS rows removed)
            │  + tax breakdown (ST/LT lots), exit-load metadata, fund ratings
            ▼
┌──────────────────────────────────────────┐
│  Rebalancing                              │  PER-FUND TRADES
│  (Part C — thin consumer changes)         │  step1 cap-and-spill → … → step6
└──────────────────────────────────────────┘
            │
            ▼
   Per-fund BUY / SELL / EXIT
   + SELL_DIRECT_STOCKS (when stocks exceed NFA-banded cap)
   + Frozen subgroup summaries (tax_efficient_equities, non_mf_equities)
   + Pass-through PracticalAllocationOutput for ideal-vs-practical UI
```

**Dependency DAG (no cycles):** `asset_allocation_pydantic ← practical_asset_allocation ← Rebalancing`.

**Cross-module import convention:** This spec introduces the **first explicit cross-agent imports** under `AI_Agents/src/`. `src/CLAUDE.md` currently states agents are peers and do not import each other. We're adding two documented exceptions:
- `practical_asset_allocation` imports `step1_emergency.run`, `step2_short_term.run`, `step3_medium_term.run` (and selected helpers from `step4_long_term`) from `asset_allocation_pydantic`.
- `Rebalancing` imports `run_practical_allocation` from `practical_asset_allocation`.

Both modules' CLAUDE.mds will explicitly call out the upstream dependency.

---

## Part A — `asset_allocation_pydantic` (5 surgical changes; contract unchanged)

**Contract:** Input model (`AllocationInput`) and output model (`GoalAllocationOutput`) unchanged. No new fields, no new subgroups in the output. Backend integration untouched.

### A.1 Bucket boundary shift

| Bucket | Current | New |
|---|---|---|
| Short-term | `months < 24` | `months < 36` |
| Medium-term | `24 ≤ months ≤ 60` | `36 ≤ months < 72` |
| Long-term | `months > 60` | `months ≥ 72` |

**Touch points:**
- `tables.py`: `MEDIUM_TERM_BOUNDARY_MONTHS: 24 → 36`, `LONG_TERM_BOUNDARY_MONTHS: 60 → 72`.
- `step2_short_term.py`, `step3_medium_term.py`, `step4_long_term.py`: filter changes follow the constants.

### A.2 Step 1 — Emergency always routes to `short_debt`

Drop the tax-rate gate that currently routes emergency to `arbitrage` when `effective_tax_rate > 20`.

**Touch point:** `step1_emergency.py` lines 34–39 — hard-code `asset_subgroup = "short_debt"`.

### A.3 Step 2 — Short-term year-split with two tax thresholds

| Sub-bucket | Goal window | Subgroup rule |
|---|---|---|
| ST1 | `months < 24` (years 0 + 1) | `arbitrage` if `effective_tax_rate > 20`, else `short_debt`. |
| ST2 | `24 ≤ months < 36` (year 2) | `arbitrage` if `effective_tax_rate > 12.5`, else `short_debt`. |

ST1 allocated first, then ST2 from remaining. Both contribute to `subgroup_amounts`.

**Touch point:** `step2_short_term.py` — partition goals into ST1/ST2, compute per-sub-bucket routing, combine.

**Verify:** `tax_rate = 18%` → ST1 → `short_debt`, ST2 → `arbitrage`. `tax_rate = 25%` → both → `arbitrage`. `tax_rate = 10%` → both → `short_debt`.

### A.4 Step 3 — Medium-term market-view override

Add: when `market_commentary.equities ≤ 3`, force the Low (most conservative) column of `MEDIUM_TERM_SPLIT` regardless of risk score, across all three horizons (year 3 / 4 / 5).

**Touch point:** `step3_medium_term.py` per-goal loop — override `(eq_pct, dt_pct) = MEDIUM_TERM_SPLIT[(horizon, "Low")]` when `inp.market_commentary.equities <= 3`.

Step 3's debt-routing logic (`tax ≥ 15% → arbitrage_plus_income`, else `short_debt`) stays as-is.

### A.5 Risk-band boundary disambiguation

Adopt **`lower < score ≤ upper`** consistently.

```python
# step3_medium_term.py
def _risk_bucket(score):
    if score <= 4.0: return "Low"      # was: score < 4.0
    if score <= 7.0: return "Medium"
    return "High"
```

`score = 4.0` → Low (was Medium); `score = 7.0` → Medium (unchanged).

**Touch points:** `step3_medium_term.py` `_risk_bucket`; `tables.py` rename `MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE` → `MEDIUM_TERM_RISK_LOW_MAX_INCLUSIVE`.

### A.6 Items explicitly NOT in Part A

- ELSS, non-MF equity inputs — **Part B** (practical_asset_allocation).
- `min_equity_elss_%` floor — **Part B**.
- `max_non_mf_equity%` NFA-banded cap — **Part B**.
- `min_equity_pct_required` sliding threshold (now also Excel v2's average-based variant) — **Part B**.
- Step 5 new subgroup rows (`tax_efficient_equities`, `non_mf_equities`) — **Part B** (appear in PracticalAllocationOutput, not GoalAllocationOutput).
- `intergenerational_transfer` input — **kept** (real logic in current code; likely missed from Excel).
- Step 4 phase-1 others-gate operator tweaks, phase-5 sector/value gate operator tweaks, multi-asset overflow redistribution — **dropped** (no instruction received; current behaviour stays).

### A.7 Open questions for Part A

*All resolved.*

---

## Part B — `practical_asset_allocation` (NEW peer module)

A new module at `AI_Agents/src/practical_asset_allocation/`. Re-runs the goal-based bucketing using the customer's actual investable corpus (minus locked ELSS) and a holdings-aware long-term step that handles ELSS, non-MF equity, and the v2 sliding-threshold logic.

### B.1 Module layout

```
AI_Agents/src/practical_asset_allocation/
  __init__.py
  pipeline.py        # ONE file: PracticalAllocationInput, PracticalAllocationOutput,
                     # CorpusBreakdown, run_practical_allocation(), long-term math
  CLAUDE.md
  Testing/           # pytest suite (per-section unit tests + e2e smoke)
```

Per design call: a single `pipeline.py` holds all models, the orchestrator, and the long-term R177–R222 math. If it grows past ~500 lines we revisit splitting.

### B.2 Input contract — `PracticalAllocationInput`

Inherits all of `AllocationInput`'s fields (profile, goals, market view, multi-asset composition, `total_corpus`, `net_financial_assets`, etc.) **unchanged**. Adds four NEW fields only — no shadowing, no renaming:

```python
class PracticalAllocationInput(AllocationInput):
    # Inherited from AllocationInput: total_corpus, net_financial_assets, goals,
    # market_commentary, multi_asset_composition, profile fields, etc.
    # Four NEW fields below; all use float to match parent's total_corpus type.
    mf_corpus: float = Field(..., ge=0)                          # total MF holdings INCLUDING ELSS
    non_mf_equity_corpus: float = Field(default=0.0, ge=0)        # direct stocks + PMS
    elss_corpus: float = Field(default=0.0, ge=0)                 # ELSS MF holdings
    max_non_mf_equity_pct_client_input: Optional[float] = None    # advisor override (Option A)
```

**Implicit corpus accounting (not separate inputs):**
- `cash = total_corpus − mf_corpus − non_mf_equity_corpus`
- `mf_non_elss = mf_corpus − elss_corpus`
- `rebalancing_corpus = total_corpus − elss_corpus`

If a future pass migrates everything to `Decimal`, do it in one go across allocation + practical_allocation + rebalancing — out of scope for this spec.

### B.3 Output contract — `PracticalAllocationOutput`

Shape parity with `GoalAllocationOutput` — same seven fields — plus one extras block.

```python
class CorpusBreakdown(BaseModel):
    total_corpus_inr: int
    mf_corpus_inr: int
    non_mf_equity_input_inr: int            # echo of input
    elss_corpus_inr: int
    rebalancing_corpus_inr: int             # = total − ELSS
    non_mf_equity_actual_inr: int           # ≤ input, NFA-capped
    excess_direct_stocks_inr: int           # = input − actual; drives SELL recommendation
    max_non_mf_equity_pct_computed: float   # NFA-banded value used (or override if provided)

class PracticalAllocationOutput(BaseModel):
    client_summary: ClientSummary                                 # same as ideal
    bucket_allocations: List[BucketAllocation]                    # same
    aggregated_subgroups: List[AggregatedSubgroupRow]             # same shape; includes tax_efficient_equities and non_mf_equities rows
    future_investments_summary: List[FutureInvestment]            # same
    grand_total: float                                            # = total_corpus
    all_amounts_in_multiples_of_100: bool                         # same invariant
    asset_class_breakdown: AssetClassBreakdown                    # same
    corpus_breakdown: CorpusBreakdown                             # NEW (the only practical-only field)
```

Any consumer that already understands `GoalAllocationOutput` handles `PracticalAllocationOutput` for the shared seven fields with zero change.

### B.4 Pipeline — `run_practical_allocation(inp: PracticalAllocationInput)`

```python
def run_practical_allocation(inp: PracticalAllocationInput) -> PracticalAllocationOutput:
    # 1. ELSS freeze
    rebalancing_corpus = inp.total_corpus - inp.elss_corpus
    if rebalancing_corpus < 0:
        # Edge case (α) per user — never happens in practice; raise.
        raise InfeasibleGoalError("ELSS exceeds total corpus")

    # 2. Construct an AllocationInput with rebalancing_corpus as total_corpus,
    #    then call asset_allocation_pydantic's steps 1-3 to bucket the corpus.
    #    model_dump() preserves all parent fields; we override total_corpus only.
    parent_fields = AllocationInput.model_fields.keys()
    sub_inp = AllocationInput(
        **{k: getattr(inp, k) for k in parent_fields if k != "total_corpus"},
        total_corpus=rebalancing_corpus,
    )
    s1 = step1_emergency.run(sub_inp)                           # imported from asset_allocation_pydantic
    s2 = step2_short_term.run(sub_inp, s1.remaining_corpus)     # imported
    s3 = step3_medium_term.run(sub_inp, s2.remaining_corpus)    # imported

    # 3. Long-term step — fresh code in this file. Excel R177-R222 logic, holdings-aware.
    s4_practical = _run_practical_long_term(
        inp=sub_inp,
        remaining_corpus=s3.remaining_corpus,
        elss_amount=inp.elss_corpus,
        non_mf_equity_input=inp.non_mf_equity_corpus,
        nfa=inp.net_financial_assets,
        max_non_mf_equity_pct_client_input=inp.max_non_mf_equity_pct_client_input,
    )

    # 4. Aggregate subgroup amounts including frozen ELSS + non-MF rows.
    s5 = step5_aggregation_with_frozen.run(
        inp.total_corpus, s1, s2, s3, s4_practical,
        elss_amount=inp.elss_corpus,
        non_mf_equity_actual=s4_practical.non_mf_equity_actual,
    )

    # 5. Build response in the same shape as GoalAllocationOutput + corpus_breakdown.
    return _build_output(inp, s1, s2, s3, s4_practical, s5)
```

Step 1, 2, 3 are imported and called unchanged. Step 4 is a NEW function `_run_practical_long_term` (in the same `pipeline.py`) that implements Excel R177–R222, with helpers (`phase1_bounds`, `phase4_multi_asset`, `phase5_equity_subgroups`) optionally imported from `asset_allocation_pydantic.step4_long_term` where useful.

### B.5 Long-term step — `_run_practical_long_term`

Implements Excel R177–R222 with ELSS / non-MF equity / sliding threshold. Reference cells in parentheses.

1. **Add ELSS back to long-term corpus** (R158):
   `total_long_term_corpus = max(0, remaining_corpus + elss_amount)`
2. **ELSS floor** (R159):
   `min_equity_elss_pct = elss / total_long_term_corpus`
3. **First-level asset class allocation** (R161–R165): VLOOKUP into `PHASE1_RISK_BOUNDS` keyed by `ceil_to_half(risk_score)` — `equities`, `debt`, `others` min/max. *Reuse `phase1_bounds` from asset_allocation_pydantic.*
4. **Others-gate** (R167–R168): if `risk > 8` AND `market_commentary.others < 7`, force `others_min = others_max = 0`. *(Note: stricter than asset_allocation's current `risk ≥ 8 / view ≤ 6`; this is the holdings-aware variant.)*
5. **Second-level asset class allocation** (R170–R174):
   - Redistribute removed others_min/max between equity and debt pro-rata to their mins.
   - Apply market-view tilt.
   - `allocation_2_equity = max(allocation_1_eq × 100 / sum_allocation_1, min_equity_elss_pct × 100)` — ELSS floor.
   - `allocation_2_debt = (100 − allocation_2_eq) × allocation_1_debt / (allocation_1_debt + allocation_1_others)` — pro-rata.
   - `allocation_2_others = 100 − allocation_2_eq − allocation_2_debt`.
6. **Amounts** (R177–R179):
   - `equities_amount = round_to_100(total_long_term × allocation_2_eq / 100)`
   - `others_amount = round_to_100(total_long_term × allocation_2_others / 100)`
   - `debt_amount = total_long_term − equities_amount − others_amount`
7. **ELSS / non-MF equity / residual** (R180–R186):
   - `elss = elss_amount` (frozen).
   - `max_non_mf_equity_pct_computed` (R182): NFA-banded — 75% / 60% / 50% / 33% across `> 5Cr / > 2Cr / > 1Cr / else`.
   - `max_non_mf_equity_pct_considered` (R184): client input if provided, else computed (Option A — locked).
   - `max_equities_shares = considered_pct × equities_amount`.
   - `non_mf_equity_actual = min(non_mf_equity_input, equities_amount − elss, max_equities_shares)`.
   - `excess_direct_stocks = max(0, non_mf_equity_input − non_mf_equity_actual)` (drives SELL recommendation in Rebalancing).
   - `residual_equity_corpus = max(0, equities_amount − non_mf_equity_actual − elss)`.
8. **Multi-asset block** (R187–R194):
   - `multi_asset_amount = round_to_100(min(residual_equity × multi_asset_max_equity / (multi_asset_eq_pct/100), debt_amount / (multi_asset_debt_pct/100)))`.
   - Components (eq / debt / others) per fund composition.
   - `multi_asset_others_excess = max(0, round_to_100(others_component − others_amount))`.
   - `excess_to_debt = min(round_to_100(excess × allocation_2_debt / 100), debt_amount − multi_asset_debt_component)`.
   - `excess_to_equity = excess − excess_to_debt`.
   - `residual_equity_corpus_final = residual_equity − multi_asset_equity_component − excess_to_equity`.
   - *Helper: reuse `phase4_multi_asset` from asset_allocation_pydantic.step4_long_term where its signature fits; otherwise inline.*
9. **Equity subgroup gates** (R196–R199):
   - Sector / value market-view gates: `view ≤ 7` → force min = max = 0 for that subgroup.
   - **Excel v2 average-based slider:**
     - `average_equity_subgroup_allocation = avg(non-zero % OF EQUITIES values)`. (R198)
     - `min_equity_pct_required = max(8 − max(0, (elss + non_mf_equity_actual)/equities_amount − 0.20) × 10, min(3, average_equity_subgroup_allocation))`. (R199)
10. **Equity subgroup amounts** (R200–R215):
    - Per subgroup (sector, value, us, low, medium, high beta): VLOOKUP min/max from Table sheet keyed by risk score; apply market-view tilt; compute `% OF EQUITIES`; drop those below `min_equity_pct_required`; renormalise survivors. *Reuse `phase5_equity_subgroups` from asset_allocation_pydantic if its API fits; the new threshold logic likely requires a wrapper.*
11. **Debt and others residuals** (R217–R222):
    - `residual_debt_corpus = debt_amount − multi_asset_debt_component − excess_to_debt`.
    - `arbitrage_plus_income = residual_debt_corpus` (Excel always routes the long-term debt residual to `arbitrage_plus_income`; the tax-rate gate that asset_allocation_pydantic keeps in Part A.4 applies to **medium-term** debt routing only, not to this long-term residual).
    - `residual_other_corpus = max(0, others_amount − (others_component − multi_asset_others_excess))`.
    - `gold_commodities = round_to_100(residual_other_corpus)`.

**Output:** subgroup amounts for long-term bucket, plus the corpus-breakdown fields surfaced in step B.6.

### B.6 Aggregation — extends asset_allocation's step5

`step5_aggregation.run(...)` is imported from `asset_allocation_pydantic` but takes a small wrapper to add two new subgroup rows:

| Subgroup | `long_term` amount | All other buckets |
|---|---|---|
| `tax_efficient_equities` | `elss_amount` | 0 |
| `non_mf_equities` | `non_mf_equity_actual` | 0 |

`grand_total` reconciles to `total_corpus_inr` (not `rebalancing_corpus_inr`) because ELSS and capped non-MF equity are now visible rows.

### B.7 Edge cases

- **(α) ELSS > total corpus** (impossible per user): raise `InfeasibleGoalError`.
- **(β) Mid-sequence underfunding** (rebalancing corpus runs out at emergency / ST / MT / LT): step1/2/3/4 each emit `FutureInvestment` with the gap, same pattern as `asset_allocation_pydantic` today. Pipeline continues with what was funded.
- **Negative residual_equity_corpus** (ELSS + non-MF actual > equity allocation): clamp to 0 in step 7 above; equity subgroups receive zero allocation; warning surfaced.

### B.8 Helpers from `asset_allocation_pydantic` consumed by `practical_asset_allocation`

Documented imports (these names must remain stable on the `asset_allocation_pydantic` side):
- `steps.step1_emergency.run`, `step2_short_term.run`, `step3_medium_term.run`
- `steps.step4_long_term.phase1_bounds`, `phase4_multi_asset`, `phase5_equity_subgroups`
- `steps.step5_aggregation.run` (with a wrapper for the two extra subgroup rows)
- `utils.round_to_100`, `ceil_to_half`
- `models.AllocationInput`, `Goal`, `MarketCommentaryScores`, `MultiAssetFundComposition`, `BucketAllocation`, `AggregatedSubgroupRow`, `FutureInvestment`, `ClientSummary`, `AssetClassBreakdown`, etc. (output shape parity)

### B.9 Tests

`practical_asset_allocation/Testing/` with these scenarios:
1. ELSS = 0, non-MF equity = 0 → output matches `asset_allocation_pydantic` exactly (regression guard).
2. ELSS > 0 but well below equity allocation → ELSS appears as frozen long-term row; equity subgroup amounts shrunk pro-rata.
3. ELSS lifts equity above ideal → debt/others shrink pro-rata.
4. Non-MF equity below NFA cap → fully absorbed; `excess = 0`.
5. Non-MF equity above NFA cap → capped; `excess > 0` for SELL recommendation downstream.
6. Sliding threshold v2 → with crowded equity, threshold drops below 3% per `min(3, avg_subgroup_alloc)`.
7. Mid-sequence underfunding → `FutureInvestment` populated; pipeline continues.

---

## Part C — `Rebalancing/` (thin consumer)

The Rebalancing engine becomes a thin consumer of `practical_asset_allocation`. Most steps unchanged.

### C.1 New request shape — `RebalancingComputeRequest`

```python
class RebalancingComputeRequest(BaseModel):
    practical_allocation_input: PracticalAllocationInput      # NEW — passed straight to practical module
    rows: List[FundRowInput]                                   # MF rows only — ELSS rows removed by input builder
    stcg_offset_budget_inr: Optional[Decimal] = None           # unchanged
    carryforward_st_loss_inr: Decimal = 0                      # unchanged
    carryforward_lt_loss_inr: Decimal = 0                      # unchanged
    rounding_step: int = 100                                   # unchanged
    tax_regime: Literal["old", "new"]                          # unchanged
    effective_tax_rate_pct: Decimal                            # unchanged
```

The previous top-level `total_corpus` scalar now comes via `practical_allocation_input.total_corpus`.

### C.2 New response shape — `RebalancingComputeResponse`

Adds one field; all others unchanged.

```python
class RebalancingComputeResponse(BaseModel):
    rows: list[FundRowAfterStep5]
    subgroups: list[SubgroupSummary]                  # now includes 2 frozen rows
    totals: RebalancingTotals
    metadata: RebalancingRunMetadata
    trade_list: list[TradeAction]                     # may contain SELL_DIRECT_STOCKS action
    warnings: list[RebalancingWarning]
    practical_allocation: PracticalAllocationOutput   # NEW — verbatim passthrough for ideal-vs-practical UI
```

### C.3 Pipeline orchestration — `Rebalancing/pipeline.py`

```python
def run_rebalancing(req: RebalancingComputeRequest) -> RebalancingComputeResponse:
    # 1. Practical allocation
    practical = practical_asset_allocation.run_practical_allocation(req.practical_allocation_input)

    # 2. Lift per-subgroup targets onto rank-1 fund rows
    rows_with_targets = _assign_targets_to_rank1(req.rows, practical.aggregated_subgroups)

    # 3. Existing pipeline (step1 onwards) — interface unchanged
    s1 = step1_cap_and_spill.run(rows_with_targets, ...)
    s2 = step2_compare_and_decide.run(s1, ...)
    s3 = step3_tax_classification.run(s2, ...)
    s4 = step4_initial_trades_under_stcg_cap.run(s3, stcg_budget=req.stcg_offset_budget_inr, ...)
    s5 = step5_loss_offset_top_up.run(s4, carry_forward=...)

    # 4. Presentation — step6 also reads practical for frozen subgroups + SELL action
    return step6_presentation.run(s5, req=req, practical=practical, ...)
```

`_assign_targets_to_rank1` is a small inline helper (~10 lines) in `pipeline.py` — for each subgroup in `practical.aggregated_subgroups`, sets `target_amount_pre_cap` on the rank-1 row that matches. Filters out frozen subgroups (`tax_efficient_equities`, `non_mf_equities`) since they have no MF rows.

### C.4 step2_compare_and_decide — remove ELSS row special-case

Today (lines 37–45 of `step2_compare_and_decide.py`) ELSS rows are treated as pure buy-demand. **Remove this branch entirely** — ELSS rows no longer arrive in `req.rows` (input builder filters them out and surfaces ELSS via `practical_allocation_input.elss_corpus_inr`).

Also remove the BAD_FUND_DETECTED ELSS exclusion (lines 63–77) — that branch is dead code after the filter.

### C.5 step1_cap_and_spill — add `short_debt = 30%` cap tier

Today `tables.MULTI_FUND_CAP_SUBGROUPS = {"multi_asset"}` and `config.MULTI_FUND_CAP_PCT = 20.0`. Excel R247 adds a third cap tier for `short_debt = 30%`.

**Touch points:**
- `config.py`: add `SHORT_DEBT_FUND_CAP_PCT = 30.0` with env override `REBAL_SHORT_DEBT_FUND_CAP_PCT`.
- `tables.py`: replace single-entry frozenset with `SUBGROUP_FUND_CAP_PCT: dict[str, float]` = `{"multi_asset": 20.0, "short_debt": 30.0}` with default fallback = `OTHERS_FUND_CAP_PCT`.
- `step1_cap_and_spill.py`: replace the `max_pct` decision branch with the lookup.

### C.6 step6_presentation — emit SELL_DIRECT_STOCKS + frozen subgroups

Accepts new `practical: PracticalAllocationOutput` parameter.

**(a) Frozen subgroup summaries** — append two entries to `subgroups`:

| Subgroup | `goal_target` | `current_holding` | `final_holding` | `rows` |
|---|---|---|---|---|
| `tax_efficient_equities` | `practical.corpus_breakdown.elss_corpus_inr` | same | same | `[]` |
| `non_mf_equities` | `practical.corpus_breakdown.non_mf_equity_actual_inr` | `practical.corpus_breakdown.non_mf_equity_input_inr` | `practical.corpus_breakdown.non_mf_equity_actual_inr` | `[]` |

Update `SubgroupSummary` in `models.py` to allow `rows: List[...] = []` for frozen entries.

**(b) SELL_DIRECT_STOCKS trade action** — when `practical.corpus_breakdown.excess_direct_stocks_inr > 0`, emit:

```python
TradeAction(
    action="SELL_DIRECT_STOCKS",
    isin=None,
    fund_name=None,
    amount_inr=practical.corpus_breakdown.excess_direct_stocks_inr,
    reason_code="sell_excess_direct_stocks",
    reason_title=...,    # from rationales.py
    reason_text=...,
)
```

Update `TradeAction.action` literal union: `BUY | SELL | EXIT | SELL_DIRECT_STOCKS`. Allow `isin` and `fund_name` to be Optional.

**(c) Passthrough** — set `practical_allocation = practical` on the response.

### C.7 Rationales — new entry

`rationales.py`:
```python
"sell_excess_direct_stocks": {
    "title": "Trim direct-stock holdings",
    "text": (
        "Your direct stock holdings exceed the level we'd recommend for your "
        "wealth bracket — concentrated single-stock positions are hard to "
        "manage well without active research. We recommend selling ₹{amount} "
        "and reallocating to diversified mutual funds, which give you the "
        "same equity exposure with much less single-name risk."
    ),
},
```

### C.8 BAD-fund STCG cap behaviour — Excel parity confirmed

In pass 1, ALL funds (BAD or recommended) are capped at LT-only selling when STCG > 0. Pass 2 lifts the cap proportionally using loss offsets. **No behaviour change vs current code.**

### C.9 ST/LT loss-offset two-pass — Excel parity confirmed

`step5_loss_offset_top_up` matches Excel intent. **No change.**

### C.10 Input-builder plumbing — `app/services/ai_bridge/rebalancing/input_builder.py`

Required edits (outside the engine; included in the implementation plan for completeness):

- Build `PracticalAllocationInput`:
  - Read customer profile → fields shared with `AllocationInput`.
  - Compute `total_corpus_inr` = MF holdings sum + ELSS sum (already in MF) + `StockTransaction` sum + cash (from wherever it lives).
  - `mf_corpus_inr` = sum of all MF holdings (incl. ELSS).
  - `non_mf_equity_corpus_inr` = sum from `StockTransaction` (NEW read; previously unused).
  - `elss_corpus_inr` = sum of MF rows with `asset_subgroup == "tax_efficient_equities"`.
  - `net_financial_assets` = pass through.
- Filter out ELSS rows from `rows` (they're now scalar; not per-fund).
- Set `target_amount_pre_cap = 0` on all rows (the engine assigns targets after step0).

### C.11 Items kept as-is

- Tax-aware sell prioritisation (LT → ST out-of-load → ST in-load).
- STCG offset budget binding and two-pass top-up.
- Carry-forward losses feeding into pass-2 budget.
- All env-overrideable knobs.
- ELSS lock-in: never trim or force-exit ELSS even if off-list. (Now enforced by ELSS never appearing as a row at all.)

### C.12 Explicit deferrals (Excel "Logic not covered" footer)

| # | Item | Status |
|---|---|---|
| 1 | BAD Fund subgroup-offset (Excel R289 col Q partial formula) | Defer — Sourabh's design incomplete. |
| 2 | Selling ST gains up to ST/LT losses | Already handled by current `step5_loss_offset_top_up` (C.9). |
| 3 | ELSS exit — rebalance | Defer (locked under 3-year SEBI). |
| 4 | Managing inflow rebalance when `worth_to_change = False` | Defer. |
| 5 | "Debt fund" (text incomplete in Excel) | Defer — clarify with Sourabh. |

---

## Deferred refactors (out of scope for this spec)

These were considered and explicitly deferred to keep the spec surgical.

### Rename `GoalAllocationOutput` → `IdealAllocationOutput`

The naming `GoalAllocationOutput` predates the introduction of `PracticalAllocationOutput` and now reads asymmetric. Renaming would touch ~15 files (engine, bridge service, tests, alembic migration `d6e7f8a90b12_replace_allocation_and_rebalancing_with_normalized_tables.py`, persistence layer). Pure cosmetic; behaviour identical. **Defer** to a standalone task to keep this spec's review surface manageable.

### Rename module `asset_allocation_pydantic` → `ideal_asset_allocation`

Would touch ~30 files (every import, all CLAUDE.mds, the `ensure_ai_agents_path()` injection comments, archive references) plus the bridge consumer (`app/services/ai_bridge/asset_allocation/`) for consistency. **Defer** to a standalone task after the class rename above stabilises.

**Recommended order when picked up:**
1. Class rename only: `GoalAllocationOutput → IdealAllocationOutput`.
2. Let it sit / run in prod for a release.
3. Module rename if still desired.

---

## Summary — all changes at a glance

### Part A (5 changes, contract unchanged)
- **A.1** Bucket boundaries: `< 36 / 36–71 / ≥ 72` months.
- **A.2** Emergency → always `short_debt`.
- **A.3** Short-term ST1/ST2 split with thresholds 20% / 12.5%.
- **A.4** Medium-term: `market_commentary.equities ≤ 3` → force Low column.
- **A.5** Risk-band: `lower < score ≤ upper`.

### Part B (NEW module — practical_asset_allocation)
- **B.1** Module layout — single `pipeline.py`.
- **B.2** `PracticalAllocationInput` extending `AllocationInput` with 4 corpus scalars + optional cap override.
- **B.3** `PracticalAllocationOutput` shape-parity with `GoalAllocationOutput` + `CorpusBreakdown`.
- **B.4** Pipeline imports steps 1–3 from `asset_allocation_pydantic`; new long-term step.
- **B.5** Long-term step implements R177–R222 with ELSS floor, NFA-banded non-MF cap, v2 average-based sliding threshold.
- **B.6** Aggregation extended with two frozen subgroup rows.
- **B.7** Edge cases (infeasibility, underfunding, negative residual).
- **B.8** Documented helper imports from upstream module.
- **B.9** Test suite.

### Part C (Rebalancing — thin consumer)
- **C.1** Request carries `practical_allocation_input` nested.
- **C.2** Response carries `practical_allocation` passthrough.
- **C.3** `pipeline.py` calls `practical_asset_allocation.run_practical_allocation` first, then assigns per-subgroup targets onto rank-1 rows.
- **C.4** Drop ELSS row-special-case in step2.
- **C.5** `short_debt = 30%` per-fund cap.
- **C.6** step6 emits `SELL_DIRECT_STOCKS` + frozen subgroup summaries.
- **C.7** New rationale entry.
- **C.8** BAD-fund STCG cap parity confirmed (no change).
- **C.9** Loss-offset parity confirmed (no change).
- **C.10** Input-builder plumbing in `app/services/ai_bridge/rebalancing/input_builder.py`.
- **C.11** Items kept as-is.
- **C.12** Explicit deferrals from Excel footer.

### Deferred refactors
- Rename `GoalAllocationOutput → IdealAllocationOutput` (class-only, ~15 files).
- Rename module `asset_allocation_pydantic → ideal_asset_allocation` (~30 files).

---

## Open questions

*All resolved.*

## Next steps

1. ✅ User signoff.
2. ✅ Spec self-review.
3. Hand to `superpowers:writing-plans` → three plans (Part A, Part B, Part C) shipped as three sequential PRs.
