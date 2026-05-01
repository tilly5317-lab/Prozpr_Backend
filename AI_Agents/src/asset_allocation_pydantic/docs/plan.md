# Goal-Based Allocation — Deterministic Pydantic Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 7-step LangChain LCEL pipeline (`goal_based_allocation/`) with a fully deterministic Python/pydantic implementation under `asset_allocation_pydantic/`. Only Step 7 retains a single scoped LLM call, used purely to generate personalized rationale strings; all numeric/allocation logic is deterministic.

**Architecture:** One module per step under `steps/`. Shared pydantic types in `models.py`. Lookup tables (risk score → bounds, horizon → E/D split, fund mapping) in `tables.py`. Pipeline orchestrator in `pipeline.py` exposes `run_allocation(AllocationInput) -> GoalAllocationOutput`. Each step is a pure function `run(step_input) -> step_output`. Tests under `Testing/` mirror `steps/` 1:1.

**Tech Stack:** Python 3.11+, pydantic v2, pytest. Only Step 7 uses `langchain-anthropic` for rationale LLM call.

---

## Reference Docs

- `references/emergency.md` — Step 1 spec
- `references/short-term-goals.md` — Step 2 spec
- `references/medium-term-goals.md` — Step 3 spec
- `references/long-term-goals.md` — Step 4 spec (large, 5 phases)
- `references/aggregation.md` — Step 5 spec
- `references/guardrails.md` — Step 6 spec
- `references/presentation.md` — Step 7 spec
- `references/scheme_classification.md` — Fund mapping table

The original LLM implementation lives at `../goal_based_allocation/` — read its `models.py` for existing pydantic types; copy (don't import) into the new package.

---

## File Structure

```
asset_allocation_pydantic/
├── __init__.py                    # re-exports run_allocation, AllocationInput, GoalAllocationOutput
├── models.py                      # shared types: AllocationInput, Goal, step I/O models, final output
├── tables.py                      # risk bounds, horizon split, fund mapping — pure data
├── utils.py                       # round_to_100, ceil_to_half, proportional_scale
├── pipeline.py                    # run_allocation orchestrator
├── steps/
│   ├── __init__.py
│   ├── step1_emergency.py
│   ├── step2_short_term.py
│   ├── step3_medium_term.py
│   ├── step4_long_term.py         # largest; internally: phase1…phase5 functions
│   ├── step5_aggregation.py
│   ├── step6_guardrails.py
│   ├── step7_presentation.py
│   └── _rationale_llm.py          # scoped LLM call for Step 7 rationale only
├── references/                    # existing spec docs (unchanged)
└── Testing/
    ├── __init__.py
    ├── conftest.py                # fixtures
    ├── test_utils.py
    ├── test_tables.py
    ├── test_step1_emergency.py
    ├── test_step2_short_term.py
    ├── test_step3_medium_term.py
    ├── test_step4_long_term.py
    ├── test_step5_aggregation.py
    ├── test_step6_guardrails.py
    ├── test_step7_presentation.py
    └── test_pipeline.py
```

---

## Global Conventions

- All monetary amounts are integers, rounded to nearest multiple of 100 via `round_to_100`.
- Risk-score ceiling lookup: `ceil_to_half(score)` rounds up to nearest 0.5, clamped to [1.0, 10.0].
- Every step returns a pydantic model. Pipeline threads `remaining_corpus` via the orchestrator.
- Tests follow TDD: red → green → commit. Use pytest. Fixtures live in `Testing/conftest.py`.
- Commit messages: `feat(gba_py):`, `test(gba_py):`.

---

## Task 1 — Scaffolding, shared models, utils

**Files:**
- Create: `__init__.py`, `models.py`, `utils.py`, `Testing/__init__.py`, `Testing/conftest.py`, `Testing/test_utils.py`.

**`models.py`** — copy these from `../goal_based_allocation/models.py` verbatim:
- `Goal`, `MultiAssetFundComposition`, `MarketCommentaryScores`, `AllocationInput`
- `FutureInvestment`, `SubgroupFundMapping`, `BucketAllocation`, `AggregatedSubgroupRow`, `ClientSummary`
- `_normalise_future_investment`, `GoalAllocationOutput`

Add new per-step output models:

```python
class Step1Output(BaseModel):
    emergency_fund_months: int
    emergency_fund_amount: int
    nfa_carveout_amount: int
    total_emergency: int
    remaining_corpus: int
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]

class Step2Output(BaseModel):
    goals_in_bucket: List[Goal]
    asset_subgroup: Literal["debt_subgroup", "arbitrage_income"]
    total_goal_amount: int
    allocated_amount: int
    remaining_corpus: int
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]

class MediumTermGoalAllocation(BaseModel):
    goal_name: str
    time_to_goal_months: int
    amount_needed: float
    goal_priority: str
    horizon_years: int
    equity_pct: int
    debt_pct: int
    equity_amount: int
    debt_amount: int

class Step3Output(BaseModel):
    risk_bucket: Literal["Low", "Medium", "High"]
    asset_subgroup: Literal["arbitrage_plus_income", "debt_subgroup"]
    goals_allocated: List[MediumTermGoalAllocation]
    total_goal_amount: int
    allocated_amount: int
    remaining_corpus: int
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]

class AssetClassAllocation(BaseModel):
    equities_pct: int
    debt_pct: int
    others_pct: int
    equities_amount: int
    debt_amount: int
    others_amount: int

class ElssBlock(BaseModel):
    applicable: bool
    elss_headroom: Optional[int] = None
    elss_amount: int
    residual_equity_corpus: int

class MultiAssetBlock(BaseModel):
    multi_asset_amount: int
    equity_component: int
    debt_component: int
    others_component: int
    equity_for_subgroups: int
    debt_for_subgroups: int
    remaining_others_for_gold: int

class Step4Output(BaseModel):
    asset_class_allocation: AssetClassAllocation
    elss: ElssBlock
    multi_asset: MultiAssetBlock
    goals_allocated: List[Goal]
    leftover_corpus: int
    total_long_term_corpus: int
    total_allocated: int
    remaining_corpus: int = 0
    future_investment: Optional[FutureInvestment] = None
    subgroup_amounts: dict[str, int]

class AggregatedRow(BaseModel):
    subgroup: str
    emergency: int
    short_term: int
    medium_term: int
    long_term: int
    total: int

class Step5Output(BaseModel):
    rows: List[AggregatedRow]
    grand_total: int
    grand_total_matches_corpus: bool

class ValidationBlock(BaseModel):
    all_rules_pass: bool
    violations_found: List[str]
    adjustments_made: List[str]

class FundMapping(BaseModel):
    asset_class: Literal["equity", "debt", "others"]
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    isin: str
    total_amount: int

class Step6Output(BaseModel):
    validation: ValidationBlock
    fund_mappings: List[FundMapping]
```

**`utils.py`:**

```python
from __future__ import annotations
from math import ceil, floor

def round_to_100(x: float) -> int:
    """Round to nearest multiple of 100. Ties go up. Negative inputs clamp to 0."""
    if x <= 0:
        return 0
    return int(round(x / 100.0)) * 100

def ceil_to_half(score: float) -> float:
    """Round up to nearest 0.5; clamp to [1.0, 10.0]."""
    score = max(1.0, min(10.0, float(score)))
    return min(10.0, ceil(score * 2) / 2)

def proportional_scale(values: list[float], target_sum: float) -> list[float]:
    """Scale so sum equals target_sum. If input sum is 0, returns zeros."""
    s = sum(values)
    if s <= 0:
        return [0.0 for _ in values]
    return [v * target_sum / s for v in values]
```

**`Testing/conftest.py`:** six fixtures — `minimal_input`, `high_risk_input`, `low_tax_input`, `old_regime_input`, `intergen_input`, `future_investment_input`. Each returns an `AllocationInput`. Copy realistic defaults from `../goal_based_allocation/Testing` if present; else construct from scratch.

**`Testing/test_utils.py`:**
- [ ] Test `round_to_100`: 50→100, 49→0, 149→100, 150→200, -5→0, 0→0.
- [ ] Test `ceil_to_half`: 7.19→7.5, 7.5→7.5, 7.01→7.5, 10.0→10.0, 10.1→10.0, 1.0→1.0, 0.5→1.0.
- [ ] Test `proportional_scale`: `[1,1,1]` target 100 → `[33.33…, 33.33…, 33.33…]` within 1e-9; zero input returns zeros.

**Verification command:** `cd asset_allocation_pydantic && python -m pytest Testing/test_utils.py -v`

Commit: `feat(gba_py): scaffold package, shared models, utilities`

---

## Task 2 — Lookup tables (`tables.py`)

**Files:** Create `tables.py`, `Testing/test_tables.py`.

Encode these tables verbatim from the reference docs:

1. **`PHASE1_RISK_BOUNDS`** — 19 entries `{score: AssetClassBounds(eq_min, eq_max, debt_min, debt_max, others_min, others_max)}` from `long-term-goals.md` lines 46-66.
2. **`PHASE5_EQUITY_SUBGROUP_BOUNDS`** — 19 entries, each maps `{score: {subgroup_name: (min, max)}}` for 6 subgroups: `us_equities`, `low_beta_equities`, `medium_beta_equities`, `high_beta_equities`, `sector_equities`, `value_equities` from `long-term-goals.md` lines 200-219.
3. **`MEDIUM_TERM_SPLIT`** — `{(horizon_years, "Low"|"Medium"|"High"): (equity_pct, debt_pct)}` for 3/4/5 years × 3 risk buckets, from `medium-term-goals.md` lines 34-38.
4. **`FUND_MAPPING`** — `{asset_subgroup: FundMapRow(asset_class, sub_category, recommended_fund, isin)}` from `scheme_classification.md`. Include all 18 subgroups.

Use `@dataclass(frozen=True)` for table row types.

**`Testing/test_tables.py`:**
- [ ] All 19 risk scores `{1.0, 1.5, …, 10.0}` present in `PHASE1_RISK_BOUNDS`.
- [ ] Each Phase 1 row has `eq_min <= eq_max`, `debt_min <= debt_max`, `others_min <= others_max`.
- [ ] All 19 scores present in `PHASE5_EQUITY_SUBGROUP_BOUNDS`; each row has 6 subgroups.
- [ ] `MEDIUM_TERM_SPLIT` has 9 entries; equity_pct + debt_pct == 100 for each.
- [ ] `FUND_MAPPING` covers every subgroup referenced in spec: `debt_subgroup`, `short_debt`, `arbitrage_income`, `arbitrage_plus_income`, `tax_efficient_equities`, `multi_asset`, `low_beta_equities`, `medium_beta_equities`, `high_beta_equities`, `value_equities`, `sector_equities`, `us_equities`, `gold_commodities`.

**Verification:** `python -m pytest Testing/test_tables.py -v`

Commit: `feat(gba_py): lookup tables for risk bounds, horizon split, fund mapping`

---

## Task 3 — Step 1 Emergency (`steps/step1_emergency.py`)

**Spec:** `references/emergency.md`.

Function signature:

```python
def run(inp: AllocationInput) -> Step1Output: ...
```

**Logic:**
1. If `emergency_fund_needed == False`: `emergency_fund_months = 0`, `emergency_fund_amount = 0`.
   Else: `months = 6 if primary_income_from_portfolio else 3`; `emergency_fund_amount = round_to_100(months * monthly_household_expense)`.
2. `nfa = inp.net_financial_assets`; `nfa_carveout_amount = round_to_100(abs(nfa)) if (nfa is not None and nfa < 0) else 0`.
3. `total_emergency = emergency_fund_amount + nfa_carveout_amount`.
4. If `total_emergency > total_corpus`:
   - `future_investment_amount = total_emergency - int(total_corpus)`
   - `remaining_corpus = 0`
   - `future_investment = FutureInvestment(bucket="emergency", future_investment_amount=future_investment_amount, message="Frame the gap as wealth to build through upcoming monthly investments — a modest step-up in savings has the reserve fully in place.")`
   - (amounts themselves are NOT scaled down — they represent the target)
5. Else: `remaining_corpus = int(total_corpus) - total_emergency`, `future_investment = None`.
6. `subgroup_amounts = {"debt_subgroup": total_emergency}` (only this key — other steps fill in other keys).

**`Testing/test_step1_emergency.py`:**
- [ ] `emergency_fund_needed=False` ⇒ all zeros, remaining_corpus == total_corpus.
- [ ] Base 3m (non-portfolio income, expense 50000, corpus 1000000) ⇒ `150000, remaining=850000`.
- [ ] Portfolio-income 6m (expense 50000) ⇒ `300000`.
- [ ] Negative NFA -200000 added on top of base 3m.
- [ ] Future investment: expense 500000, corpus 1000000 (3m = 1500000 > 1000000) ⇒ future_investment set, remaining=0, message framed around upcoming investments.
- [ ] Both NFA and fund: expense 50000, nfa -100000 ⇒ `total_emergency=250000`.

**Verification:** `python -m pytest Testing/test_step1_emergency.py -v`

Commit: `feat(gba_py): implement step 1 emergency carve-out`

---

## Task 4 — Step 2 Short-Term (`steps/step2_short_term.py`)

**Spec:** `references/short-term-goals.md`.

Signature:

```python
def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output: ...
```

**Logic:**
1. `goals_in_bucket = [g for g in inp.goals if g.time_to_goal_months < 24]`.
2. `asset_subgroup = "debt_subgroup" if inp.effective_tax_rate < 20 else "arbitrage_income"`.
3. `total_goal_amount = round_to_100(sum(g.amount_needed for g in goals_in_bucket))`.
4. If `total_goal_amount > remaining_corpus`:
   - `allocated_amount = remaining_corpus`
   - `negotiable = [g.goal_name for g in goals_in_bucket if g.goal_priority == "negotiable"]`
   - `msg = f"Short-term goals need more than the available corpus. Consider reducing or deferring negotiable goals first: {', '.join(negotiable) or 'none flagged'}."`
   - `future_investment = FutureInvestment(bucket="short_term", future_investment_amount=total_goal_amount - remaining_corpus, message=msg)`
   Else: `allocated_amount = total_goal_amount`, `future_investment = None`.
5. `new_remaining = remaining_corpus - allocated_amount`.
6. `subgroup_amounts = {asset_subgroup: allocated_amount}`.

**Tests:**
- [ ] Empty bucket (no short-term goals) ⇒ zero allocation, remaining unchanged.
- [ ] Low-tax (15%): subgroup = `debt_subgroup`.
- [ ] High-tax (30%): subgroup = `arbitrage_income`.
- [ ] Boundary: `effective_tax_rate = 20` ⇒ `arbitrage_income`.
- [ ] Future investment: 2 goals summing 500000, remaining_corpus 300000, one negotiable → future_investment message names the negotiable goal.

Commit: `feat(gba_py): implement step 2 short-term allocation`

---

## Task 5 — Step 3 Medium-Term (`steps/step3_medium_term.py`)

**Spec:** `references/medium-term-goals.md`.

Signature: `def run(inp: AllocationInput, remaining_corpus: int) -> Step3Output: ...`

**Logic:**
1. `goals_in_bucket = [g for g in inp.goals if 24 <= g.time_to_goal_months <= 60]`.
2. Risk bucket: `Low if score<4 else Medium if score<=7 else High`.
3. Debt subgroup key: `"arbitrage_plus_income" if tax_rate<20 else "debt_subgroup"`.
4. For each goal:
   - `horizon_years = min(5, max(3, floor(months/12)))`.
   - `(eq_pct, dt_pct) = MEDIUM_TERM_SPLIT[(horizon_years, risk_bucket)]`.
   - `equity_amount = round_to_100(amount_needed * eq_pct / 100)`.
   - `debt_amount = round_to_100(amount_needed * dt_pct / 100)`.
5. Sum equity across goals → `multi_asset` key (if > 0).
6. Sum debt across goals → debt subgroup key.
7. `total_goal_amount = round_to_100(sum(amount_needed))`.
8. Future investment identical pattern to Step 2.
9. `subgroup_amounts` only includes non-zero keys.

**Tests:**
- [ ] Empty bucket ⇒ zero subgroups.
- [ ] Low risk, 3-year goal ⇒ 100% debt (per table row `3 years: 0% E / 100% D`).
- [ ] High risk, 5-year goal ⇒ 80/20.
- [ ] Medium risk, 4-year ⇒ 50/50.
- [ ] Tax-rate flip (<20 vs ≥20) routes debt to correct subgroup.
- [ ] Future investment with mixed negotiable/non-negotiable goals.
- [ ] Horizon clamp: 26 months ⇒ 3-year row (not 2-year).

Commit: `feat(gba_py): implement step 3 medium-term allocation`

---

## Task 6 — Step 4 Phase 1 (asset-class bounds)

**File:** `steps/step4_long_term.py` — add `phase1_bounds` function.

```python
@dataclass
class ResolvedBounds:
    eq_min: int; eq_max: int
    debt_min: int; debt_max: int
    others_min: int; others_max: int

def phase1_bounds(
    score: float,
    market_commentary: MarketCommentaryScores,
    goals: list[Goal],
    age: int,
) -> ResolvedBounds: ...
```

**Logic:**
1. `row = PHASE1_RISK_BOUNDS[ceil_to_half(score)]`.
2. `intergen = age > 60 and any(g.investment_goal == "intergenerational_transfer" for g in goals)`.
3. If intergen: `adj = min(ceil_to_half(score) + 2.0, 9.0)`; take `eq_min, debt_min, others_min` from row at `adj`; keep maxes from original row. (Clamp adj to table keys.)
4. Others gate: if `ceil_to_half(score) >= 8` and `market_commentary.others <= 6`: set `others_min=0, others_max=0`; redistribute that max headroom proportionally between equities and debt based on their current max values (preserve sum of maxes). Similarly floor-adjust mins if needed (keep eq_min + debt_min ≤ 100).

**Tests (in `Testing/test_step4_long_term.py`):**
- [ ] Plain: score 5.0, neutral commentary, age 40 ⇒ row matches `PHASE1_RISK_BOUNDS[5.0]`.
- [ ] Ceiling: score 7.19 ⇒ row from 7.5.
- [ ] Intergen active (age 65, goal has intergenerational_transfer, score 5.0): mins from 7.0 row, maxes from 5.0 row.
- [ ] Intergen NOT active (age 50) ⇒ unchanged.
- [ ] Others gate at score 8, commentary.others=5 ⇒ others bounds zeroed, redistribution.
- [ ] Others gate NOT triggered at score 8, commentary.others=7 ⇒ unchanged.

Commit: `feat(gba_py): step 4 phase 1 asset-class bounds`

---

## Task 7 — Step 4 Phase 2 (proportional scaling)

**Add to `steps/step4_long_term.py`:**

```python
def phase2_asset_class_pcts(
    bounds: ResolvedBounds,
    market_commentary: MarketCommentaryScores,
) -> tuple[int, int, int]: ...  # (equities_pct, debt_pct, others_pct), integers summing to 100
```

**Algorithm** (from `long-term-goals.md` Phase 2):

For each asset class k in {equities, debt, others}:
- `midpoint_k = (min_k + max_k) / 2`
- `range_half_k = (max_k - min_k) / 2`
- `normalized_view_k = (view_score_k - 5) / 5`  (where view_score_k comes from market_commentary)
- `raw_target_k = midpoint_k + normalized_view_k * range_half_k`

Then:
1. Scale so sum is 100: `scaled_k = raw_target_k * 100 / sum(raw_targets)`.
2. Clamp any `scaled_k < min_k` to min_k, any `> max_k` to max_k; redistribute excess/deficit proportionally across unclamped classes. Repeat until stable (bound to 5 iterations; assert convergence).
3. Round each to nearest integer.
4. If sum ≠ 100: adjust the largest by ±(100 − sum).

**Tests:**
- [ ] Neutral view (all 5s) ⇒ midpoints, normalized to 100.
- [ ] Bullish equities (equities score 10, others neutral) ⇒ equities near eq_max.
- [ ] Bearish debt (debt score 1) ⇒ debt near debt_min.
- [ ] Integer sum == 100 on 10 random seeds.

Commit: `feat(gba_py): step 4 phase 2 proportional scaling`

---

## Task 8 — Step 4 Phase 3 (ELSS first-pass)

**Add to `steps/step4_long_term.py`:**

```python
def phase3_elss(
    equities_amount: int,
    tax_regime: Literal["old", "new"],
    section_80c_utilized: float,
) -> ElssBlock: ...
```

**Logic:**
- Applicable iff `tax_regime == "old" and section_80c_utilized < 150000`.
- If applicable: `headroom = 150000 - int(section_80c_utilized)`; `elss_amount = round_to_100(min(headroom, equities_amount))`; `residual = equities_amount - elss_amount`.
- Else: `elss_amount = 0`, `residual = equities_amount`, `headroom = None`.

**Tests:**
- [ ] New regime ⇒ not applicable, elss_amount=0.
- [ ] Old regime, 80c fully utilized (150000) ⇒ not applicable.
- [ ] Old regime, 80c=50000, equity=300000 ⇒ elss=100000, residual=200000.
- [ ] Old regime, headroom > equity ⇒ elss caps at equity, residual=0.

Commit: `feat(gba_py): step 4 phase 3 ELSS allocation`

---

## Task 9 — Step 4 Phase 4 (multi-asset decomposition)

```python
def phase4_multi_asset(
    residual_equity_corpus: int,
    debt_amount: int,
    others_amount: int,
    composition: MultiAssetFundComposition,
) -> MultiAssetBlock: ...
```

**Logic:**
1. `eq_pct, dt_pct, oth_pct = composition.equity_pct/100, composition.debt_pct/100, composition.others_pct/100`.
2. Guard: if `eq_pct == 0` then `max_x_eq = inf`, else `max_x_eq = (0.5 * residual_equity_corpus) / eq_pct`.
3. If `dt_pct == 0` then `max_x_dt = inf`, else `max_x_dt = debt_amount / dt_pct`.
4. `multi_asset_amount = round_to_100(min(max_x_eq, max_x_dt))`.
5. `equity_component = round_to_100(multi_asset_amount * eq_pct)`.
6. `debt_component = round_to_100(multi_asset_amount * dt_pct)`.
7. `others_component = round_to_100(multi_asset_amount * oth_pct)`.
8. `equity_for_subgroups = max(0, residual_equity_corpus - equity_component)`.
9. `debt_for_subgroups = max(0, debt_amount - debt_component)`.
10. `remaining_others_for_gold = max(0, others_amount - others_component)`.

**Tests:**
- [ ] Equity-constrained case.
- [ ] Debt-constrained case.
- [ ] Others overshoot ⇒ `remaining_others_for_gold = 0`.
- [ ] Zero residual equity ⇒ multi_asset_amount = 0.
- [ ] Zero debt ⇒ multi_asset_amount = 0.

Commit: `feat(gba_py): step 4 phase 4 multi-asset decomposition`

---

## Task 10 — Step 4 Phase 5 (subgroups)

**Equity subgroups function:**

```python
def phase5_equity_subgroups(
    total_equity_for_subgroups: int,
    score: float,
    market_commentary: MarketCommentaryScores,
) -> dict[str, int]: ...
```

**Algorithm:**
1. `row = PHASE5_EQUITY_SUBGROUP_BOUNDS[ceil_to_half(score)]` — 6 entries each `(min, max)`.
2. **Strict gates BEFORE any math:**
   - If `market_commentary.value_equities <= 7`: exclude `value_equities` from active set.
   - If `market_commentary.sector_equities <= 7`: exclude `sector_equities` from active set.
3. Feasibility upscale: if `sum(max_i for i in active) < 100`: multiply every active max by `100 / sum_of_maxes`.
4. For each active subgroup i: compute `raw_target_i = midpoint_i + (view_i - 5)/5 * range_half_i` where `view_i = market_commentary.<subgroup_name>`. (For `us_equities` the market_commentary key is `us_equities`, for `low_beta_equities` → `low_beta_equities`, etc.)
5. Scale to sum 100%: `scaled_i = raw_i * 100 / sum(raw)`.
6. Clamp to min/max; redistribute excess/deficit among unclamped; iterate up to 5 times.
7. Round to integers.
8. Drop-below-2% pass: any active subgroup with `pct < 2` → set to 0, redistribute to remaining active proportionally. Repeat the drop check once.
9. If sum ≠ 100: adjust largest by ±1 until sum == 100.
10. Convert pcts to amounts: `amount_i = round_to_100(total_equity_for_subgroups * pct_i / 100)`.
11. **Exact-sum fix:** let `S = sum(amounts)`. If `S != total_equity_for_subgroups`: add `(total_equity_for_subgroups - S)` to the largest amount. (Result is still a multiple of 100 because `total_equity_for_subgroups` is itself a multiple of 100 and per-amount values are multiples of 100.)
12. Return dict with ALL 6 subgroup keys — zeros for excluded/dropped.

**Debt + others (inline in orchestrator, not a separate function):**
- `debt_subgroup_key = "debt_subgroup" if tax_rate<20 else "arbitrage_income"`.
- `subgroup_amounts[debt_subgroup_key] = debt_for_subgroups` (the other key = 0).
- `subgroup_amounts["gold_commodities"] = remaining_others_for_gold`.

**Tests:**
- [ ] Default commentary (value=5, sector=5) ⇒ both excluded, amounts in remaining 4 subgroups sum exactly to `total_equity_for_subgroups`.
- [ ] `value_equities=8` ⇒ included; `sector_equities=5` ⇒ still excluded.
- [ ] `total_equity_for_subgroups = 0` ⇒ all zeros.
- [ ] Score 2.0 (many zero-max subgroups): feasibility upscale ensures sum==100.
- [ ] Bullish `high_beta_equities` (score 10) at risk score 10 ⇒ near its max.

Commit: `feat(gba_py): step 4 phase 5 equity subgroups allocation`

---

## Task 11 — Step 4 orchestration + invariants

**`steps/step4_long_term.py`** — compose phases:

```python
def run(inp: AllocationInput, remaining_corpus: int) -> Step4Output: ...
```

**Logic:**
1. `lt_goals = [g for g in inp.goals if g.time_to_goal_months > 60]`.
2. `sum_goals = round_to_100(sum(g.amount_needed for g in lt_goals))`.
3. If `sum_goals > remaining_corpus`:
   - `future_investment` created; `total_long_term_corpus = remaining_corpus`; `leftover_corpus = 0`.
   Else:
   - `total_long_term_corpus = remaining_corpus`; `leftover_corpus = remaining_corpus - sum_goals`.
4. `bounds = phase1_bounds(score, commentary, lt_goals, age)`.
5. `(eq_pct, dt_pct, oth_pct) = phase2_asset_class_pcts(bounds, commentary)`.
6. `equities_amount = round_to_100(total_long_term_corpus * eq_pct / 100)`.
   `debt_amount = round_to_100(total_long_term_corpus * dt_pct / 100)`.
   `others_amount = round_to_100(total_long_term_corpus * oth_pct / 100)`.
7. `elss = phase3_elss(equities_amount, tax_regime, section_80c_utilized)`.
8. `multi = phase4_multi_asset(elss.residual_equity_corpus, debt_amount, others_amount, multi_asset_composition)`.
9. `equity_subgroups = phase5_equity_subgroups(multi.equity_for_subgroups, score, commentary)`.
10. Build `subgroup_amounts` dict with ALL these keys (zero if unused):
    `tax_efficient_equities = elss.elss_amount`
    `multi_asset = multi.multi_asset_amount`
    `us_equities, low_beta_equities, medium_beta_equities, high_beta_equities, sector_equities, value_equities` = from equity_subgroups
    `debt_subgroup` and `arbitrage_income` — exactly one is `multi.debt_for_subgroups`, other is 0
    `gold_commodities = multi.remaining_others_for_gold`
11. `total_allocated = sum(subgroup_amounts.values())`.

**Invariant verification** (`_verify_invariants(step4_output)` — raises `AssertionError` with context on failure):
- `equities_pct + debt_pct + others_pct == 100`
- `sum(6 equity subgroup amounts) == multi.equity_for_subgroups` exactly
- `|sum(6 equity) + elss_amount + multi.equity_component − equities_amount| <= 500`
- `|multi.debt_component + multi.debt_for_subgroups − debt_amount| <= 500`
- `|multi.others_component + gold_commodities − others_amount| <= 500`
- `sum(subgroup_amounts.values()) == total_allocated`
- Every `v % 100 == 0 and v >= 0` for all subgroup amounts

Call `_verify_invariants` at end of `run`.

**Tests:**
- [ ] Leftover present (goals < corpus).
- [ ] Exact fit (goals == corpus).
- [ ] Future investment (goals > corpus).
- [ ] Zero long-term goals (pure leftover).
- [ ] All 7 invariants pass on 3 realistic `AllocationInput` fixtures.

Commit: `feat(gba_py): step 4 long-term orchestration and invariants`

---

## Task 12 — Step 5 Aggregation (`steps/step5_aggregation.py`)

Signature:

```python
def run(
    total_corpus: float,
    step1: Step1Output,
    step2: Step2Output,
    step3: Step3Output,
    step4: Step4Output,
) -> Step5Output: ...
```

**Logic:**
1. Canonical subgroup order: `debt_subgroup, short_debt, arbitrage_income, arbitrage_plus_income, tax_efficient_equities, multi_asset, low_beta_equities, medium_beta_equities, high_beta_equities, value_equities, sector_equities, us_equities, gold_commodities`.
2. For each subgroup, build `AggregatedRow(subgroup, emergency=step1.subgroup_amounts.get(sg,0), short_term=..., medium_term=..., long_term=..., total=sum_of_four)`.
3. Keep only rows where `total > 0`.
4. `grand_total = sum(row.total for row in rows)`.
5. `grand_total_matches_corpus = (grand_total == round_to_100(total_corpus))`.

**Tests:**
- [ ] Symmetric: sum of `total` column == sum of each bucket column's totals.
- [ ] Missing subgroup in a step ⇒ 0 in that column.
- [ ] Mismatch flagged when future_investment reduces grand_total.
- [ ] Rows omitted when all columns are 0.

Commit: `feat(gba_py): step 5 aggregation`

---

## Task 13 — Step 6 Guardrails + Fund Mapping (`steps/step6_guardrails.py`)

Signature:

```python
def run(
    step4: Step4Output,
    step5: Step5Output,
    score: float,
) -> Step6Output: ...
```

**Logic:**

Part 1 — Validation:
- `row = PHASE1_RISK_BOUNDS[ceil_to_half(score)]`.
- Rule 1: `sum(step4.subgroup_amounts.values()) == step4.total_allocated`. If not, record violation.
- Rule 2: `row.eq_min <= equities_pct <= row.eq_max` (same for debt, others). Record any out-of-band.
- Rule 3: For each non-zero equity subgroup amount, its share of `equities_amount` falls within Phase 5 bounds. Record any out-of-band.
- Violation-fix loop: if violations exist, delegate a correction pass that clamps violating subgroup shares and redistributes — up to 3 iterations; record adjustments. (In practice Step 4's invariants should already satisfy this — so this is a safety net. If after 3 iterations still violating, emit violations with `all_rules_pass=False` instead of raising.)

Part 2 — Fund mapping:
- For every aggregated row in `step5` (i.e., non-zero subgroups), look up `FUND_MAPPING[row.subgroup]`.
- Emit `FundMapping(asset_class, asset_subgroup=row.subgroup, sub_category, recommended_fund, isin, total_amount=row.total)`.
- If `row.subgroup` not in `FUND_MAPPING`: record validation violation ("unmapped subgroup: {name}").

**Tests:**
- [ ] Clean Step 4 input ⇒ `all_rules_pass=True`, empty violations/adjustments.
- [ ] Fund mapping completeness: every non-zero subgroup has a mapping.
- [ ] Unknown subgroup triggers violation.

Commit: `feat(gba_py): step 6 guardrails validation and fund mapping`

---

## Task 14 — Step 7 Presentation (`steps/step7_presentation.py` + `steps/_rationale_llm.py`)

**`steps/_rationale_llm.py`:**

```python
class RationaleResponse(BaseModel):
    bucket_rationales: dict[str, str]  # keys: emergency, short_term, medium_term, long_term
    future_investment_messages: dict[str, str] = {}  # keys: buckets with future_investment

def generate_rationales(
    client_summary: ClientSummary,
    bucket_allocations: list[BucketAllocation],
    aggregated_subgroups: list[AggregatedSubgroupRow],
) -> RationaleResponse: ...
```

- Uses `ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=1500, temperature=0)`.
- System prompt: plain-language rationale rules from `references/presentation.md` (no jargon, you/your, 1-3 sentences per bucket).
- User message: JSON-serialized inputs.
- On JSON parse failure: retry once.
- On retry failure: return fallback with generic strings per bucket (don't raise — deterministic core must still ship).

**`steps/step7_presentation.py`:**

```python
def run(
    inp: AllocationInput,
    step1: Step1Output,
    step2: Step2Output,
    step3: Step3Output,
    step4: Step4Output,
    step5: Step5Output,
    step6: Step6Output,
) -> GoalAllocationOutput: ...
```

**Logic:**
1. Build `ClientSummary(age, occupation=inp.occupation_type, effective_risk_score, total_corpus, goals=inp.goals)`.
2. Build `bucket_allocations` list (deterministic): one `BucketAllocation` per bucket with goals, amounts, future_investments, subgroup_amounts drawn from step1-4.
3. Build `aggregated_subgroups` from step5.rows, attaching `fund_mapping` from step6.fund_mappings keyed by subgroup.
4. `future_investments_summary = [b.future_investment for b in bucket_allocations if b.future_investment]`.
5. Call `generate_rationales(...)` to fill rationale strings on each BucketAllocation; update future_investment messages if LLM produced better text (prefer deterministic message if LLM output empty).
6. `grand_total = step5.grand_total`; `all_amounts_in_multiples_of_100 = all(v%100==0 for row in step5.rows for v in [row.emergency, row.short_term, row.medium_term, row.long_term, row.total])`.
7. Return `GoalAllocationOutput(...)` — validate via `.model_validate(...)` to exercise the normalizers.

**Tests (stub the LLM client):**
- [ ] Stub returns fixed rationales ⇒ deterministic fields unchanged across 3 fixture runs.
- [ ] LLM fails twice ⇒ fallback rationales used, no exception.
- [ ] `all_amounts_in_multiples_of_100` is True for a realistic run.
- [ ] Future-investment bucket included in `future_investments_summary`.

Commit: `feat(gba_py): step 7 presentation with scoped LLM rationale`

---

## Task 15 — Pipeline orchestrator (`pipeline.py` + `__init__.py`)

```python
def run_allocation(inp: AllocationInput) -> GoalAllocationOutput:
    s1 = step1.run(inp)
    s2 = step2.run(inp, s1.remaining_corpus)
    s3 = step3.run(inp, s2.remaining_corpus)
    s4 = step4.run_with_remaining(inp, s3.remaining_corpus)  # or compose directly
    s5 = step5.run(inp.total_corpus, s1, s2, s3, s4)
    s6 = step6.run(s4, s5, inp.effective_risk_score)
    return step7.run(inp, s1, s2, s3, s4, s5, s6)
```

**`__init__.py`:**
```python
from .pipeline import run_allocation
from .models import AllocationInput, Goal, GoalAllocationOutput
__all__ = ["run_allocation", "AllocationInput", "Goal", "GoalAllocationOutput"]
```

**Tests (`Testing/test_pipeline.py`):**
- [ ] Low-tax young investor fixture ⇒ grand_total == total_corpus (within rounding), no negative amounts, every non-zero subgroup in aggregated has a fund_mapping.
- [ ] High-tax pre-retiree ⇒ same invariants.
- [ ] Intergenerational-transfer elder.
- [ ] Future investment scenario.

Commit: `feat(gba_py): pipeline orchestrator and end-to-end tests`

---

## Task 16 — Callsite migration

- Grep: `rg "from goal_based_allocation" --glob '!asset_allocation_pydantic/**'`
- For each callsite, switch import to `asset_allocation_pydantic`.
- Old package stays in place for now; add a DeprecationWarning in its `__init__.py`.
- Run the full test suite to confirm.

Commit: `refactor: switch callsites to asset_allocation_pydantic`

---

## Open Questions (resolved)

- Step 7 retains single scoped LLM call for personalized rationale strings. ✅ Per user.
- Invariants: as planned. ✅
- Guardrails iteration bound: 3, falls back to reporting violations rather than raising. ✅
