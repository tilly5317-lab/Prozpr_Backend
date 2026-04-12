# Long-Term Goal Allocation — Full Logic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the placeholder 60/40 split in long-term goals with full risk-score-driven, market-commentary-adjusted, ELSS-first, equity-subgroup allocation.

**Architecture:** Three reference file changes + two model additions. The LLM executes all allocation math at inference time guided by the rewritten `long-term-goals.md` system prompt. No new Python files.

**Tech Stack:** Python 3.11, Pydantic v2, LangChain LCEL, pytest

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/goal_based_allocation/models.py` | Modify | Add `MarketCommentaryScores` model; add `investment_goal` to `Goal`; add `market_commentary` to `AllocationInput` |
| `src/goal_based_allocation/references/long-term-goals.md` | Rewrite | Full 4-phase allocation logic as LLM system prompt |
| `src/goal_based_allocation/references/scheme_classification.md` | Modify | Add `debt` subgroup row → Nippon India Arbitrage Fund |
| `src/goal_based_allocation/prompts.py` | Modify | Update step 4 human prompt to describe all 4 phases |
| `src/goal_based_allocation/Testing/test_models.py` | Modify | Add tests for new model fields |
| `src/goal_based_allocation/Testing/dev_run_samples.py` | Modify | Add intergenerational transfer profile (age > 60) |

**Run tests from:** `project/backend/AI_Agents/`  
**Test command:** `python -m pytest src/goal_based_allocation/Testing/test_models.py -v`

---

### Task 1: Add `MarketCommentaryScores` model and update `Goal` + `AllocationInput`

**Files:**
- Modify: `src/goal_based_allocation/Testing/test_models.py`
- Modify: `src/goal_based_allocation/models.py`

- [ ] **Step 1: Write failing tests**

Add to the bottom of `src/goal_based_allocation/Testing/test_models.py`:

```python
# ── MarketCommentaryScores ────────────────────────────────────────────────────

def test_market_commentary_scores_defaults():
    from src.goal_based_allocation.models import MarketCommentaryScores
    scores = MarketCommentaryScores()
    assert scores.equities == 5.0
    assert scores.debt == 5.0
    assert scores.others == 5.0
    assert scores.low_beta_equities == 5.0
    assert scores.value_equities == 5.0
    assert scores.dividend_equities == 5.0
    assert scores.medium_beta_equities == 5.0
    assert scores.high_beta_equities == 5.0
    assert scores.sector_equities == 5.0
    assert scores.us_equities == 5.0


def test_market_commentary_scores_custom():
    from src.goal_based_allocation.models import MarketCommentaryScores
    scores = MarketCommentaryScores(equities=8.0, debt=3.0)
    assert scores.equities == 8.0
    assert scores.debt == 3.0
    assert scores.others == 5.0  # default unchanged


def test_market_commentary_scores_out_of_range():
    from src.goal_based_allocation.models import MarketCommentaryScores
    with pytest.raises(ValidationError):
        MarketCommentaryScores(equities=11.0)

    with pytest.raises(ValidationError):
        MarketCommentaryScores(debt=0.0)


# ── Goal.investment_goal ──────────────────────────────────────────────────────

def test_goal_investment_goal_default():
    g = Goal(goal_name="Retirement", time_to_goal_months=240,
             amount_needed=5_000_000, goal_priority="non_negotiable")
    assert g.investment_goal == "wealth_creation"


def test_goal_investment_goal_intergenerational():
    g = Goal(goal_name="Estate Transfer", time_to_goal_months=120,
             amount_needed=5_000_000, goal_priority="non_negotiable",
             investment_goal="intergenerational_transfer")
    assert g.investment_goal == "intergenerational_transfer"


def test_goal_investment_goal_invalid():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=120,
             amount_needed=100, goal_priority="negotiable",
             investment_goal="gambling")


def test_goal_all_investment_goal_values():
    for value in ["wealth_creation", "retirement", "intergenerational_transfer",
                  "education", "home_purchase", "other"]:
        g = Goal(goal_name="G", time_to_goal_months=120,
                 amount_needed=100, goal_priority="negotiable",
                 investment_goal=value)
        assert g.investment_goal == value


# ── AllocationInput.market_commentary ────────────────────────────────────────

def test_allocation_input_market_commentary_default():
    from src.goal_based_allocation.models import MarketCommentaryScores
    inp = AllocationInput(**_base_input())
    assert isinstance(inp.market_commentary, MarketCommentaryScores)
    assert inp.market_commentary.equities == 5.0
    assert inp.market_commentary.debt == 5.0


def test_allocation_input_market_commentary_custom():
    from src.goal_based_allocation.models import MarketCommentaryScores
    custom = MarketCommentaryScores(equities=7.0, debt=4.0, high_beta_equities=8.0)
    inp = AllocationInput(**_base_input(market_commentary={"equities": 7.0, "debt": 4.0, "high_beta_equities": 8.0}))
    assert inp.market_commentary.equities == 7.0
    assert inp.market_commentary.debt == 4.0
    assert inp.market_commentary.others == 5.0  # default
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd project/backend/AI_Agents
python -m pytest src/goal_based_allocation/Testing/test_models.py::test_market_commentary_scores_defaults \
  src/goal_based_allocation/Testing/test_models.py::test_goal_investment_goal_default \
  src/goal_based_allocation/Testing/test_models.py::test_allocation_input_market_commentary_default -v
```

Expected: FAIL with `ImportError` or `ValidationError` (models not yet updated).

- [ ] **Step 3: Implement model changes**

Replace the contents of `src/goal_based_allocation/models.py` with:

```python
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class Goal(BaseModel):
    goal_name: str
    time_to_goal_months: int = Field(..., ge=1)
    amount_needed: float = Field(..., gt=0)
    goal_priority: Literal["negotiable", "non_negotiable"]
    investment_goal: Literal[
        "wealth_creation", "retirement", "intergenerational_transfer",
        "education", "home_purchase", "other"
    ] = "wealth_creation"


class MarketCommentaryScores(BaseModel):
    equities: float = Field(default=5.0, ge=1, le=10)
    debt: float = Field(default=5.0, ge=1, le=10)
    others: float = Field(default=5.0, ge=1, le=10)
    low_beta_equities: float = Field(default=5.0, ge=1, le=10)
    value_equities: float = Field(default=5.0, ge=1, le=10)
    dividend_equities: float = Field(default=5.0, ge=1, le=10)
    medium_beta_equities: float = Field(default=5.0, ge=1, le=10)
    high_beta_equities: float = Field(default=5.0, ge=1, le=10)
    sector_equities: float = Field(default=5.0, ge=1, le=10)
    us_equities: float = Field(default=5.0, ge=1, le=10)


class AllocationInput(BaseModel):
    # ── From risk_profiling ──────────────────────────────────────────────────
    effective_risk_score: float = Field(..., ge=1, le=10)
    age: int
    annual_income: float = Field(..., ge=0)
    osi: float = Field(..., ge=0.0, le=1.0)
    savings_rate_adjustment: Literal["none", "equity_boost", "equity_reduce", "skipped"]
    gap_exceeds_3: bool
    shortfall_amount: Optional[float] = None

    # ── Gathered by this module ──────────────────────────────────────────────
    total_corpus: float = Field(..., ge=0)
    monthly_household_expense: float = Field(..., ge=0)
    tax_regime: Literal["old", "new"]
    section_80c_utilized: float = Field(default=0.0, ge=0.0)
    emergency_fund_needed: bool = True
    primary_income_from_portfolio: bool = False
    effective_tax_rate: float = Field(..., ge=0.0, le=100.0)  # percentage 0–100
    goals: List[Goal] = []
    market_commentary: MarketCommentaryScores = Field(default_factory=MarketCommentaryScores)

    # ── From risk_profiling internals (optional) ─────────────────────────────
    risk_willingness: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    net_financial_assets: Optional[float] = None
    occupation_type: Optional[str] = None


# ── Output models ─────────────────────────────────────────────────────────────

class BucketShortfall(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    shortfall_amount: float = Field(..., ge=0)
    message: str


class SubgroupFundMapping(BaseModel):
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    isin: str
    amount: float = Field(..., ge=0)


class BucketAllocation(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    goals: List[Goal]
    total_goal_amount: float = Field(..., ge=0)
    allocated_amount: float = Field(..., ge=0)
    shortfall: Optional[BucketShortfall] = None
    subgroup_amounts: dict  # subgroup -> amount


class AggregatedSubgroupRow(BaseModel):
    subgroup: str
    sub_category: str
    emergency: float = Field(..., ge=0)
    short_term: float = Field(..., ge=0)
    medium_term: float = Field(..., ge=0)
    long_term: float = Field(..., ge=0)
    total: float = Field(..., ge=0)
    fund_mapping: Optional[SubgroupFundMapping] = None


class ClientSummary(BaseModel):
    age: int
    occupation: Optional[str] = None
    effective_risk_score: float
    total_corpus: float
    goals: List[Goal]


class GoalAllocationOutput(BaseModel):
    client_summary: ClientSummary
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    shortfall_summary: List[BucketShortfall]
    grand_total: float
    all_amounts_in_multiples_of_100: bool
```

- [ ] **Step 4: Update the existing test that checks `investment_goal` does not exist on `AllocationInput`**

In `src/goal_based_allocation/Testing/test_models.py`, the test `test_allocation_input_no_investment_horizon_field` has this assertion:
```python
assert not hasattr(inp, "investment_goal")
```
`investment_goal` is on `Goal`, not `AllocationInput`, so this assertion remains correct. No change needed.

- [ ] **Step 5: Run all model tests**

```bash
cd project/backend/AI_Agents
python -m pytest src/goal_based_allocation/Testing/test_models.py -v
```

Expected: ALL PASS (including pre-existing tests).

- [ ] **Step 6: Commit**

```bash
git add src/goal_based_allocation/models.py src/goal_based_allocation/Testing/test_models.py
git commit -m "feat(goal-alloc): add MarketCommentaryScores model and investment_goal to Goal"
```

---

### Task 2: Add income arbitrage fund to scheme_classification.md

**Files:**
- Modify: `src/goal_based_allocation/references/scheme_classification.md`

- [ ] **Step 1: Add the new row**

In `src/goal_based_allocation/references/scheme_classification.md`, add one row after the last `debt` row (currently line 21, `debt | floating_debt | ...`). Insert:

```
| debt | debt | Nippon India Arbitrage Fund - Direct Plan - Growth | Arbitrage Fund | INF204K01XZ7 |
```

The table block should look like this after the change:

```
| debt | debt_subgroup | HDFC Liquid Fund - Growth Option - Direct Plan | Liquid Fund | INF179KB1HP9 |
| debt | short_debt | HDFC Low Duration Fund - Direct Plan - Growth | Ultra Short to Short Term Fund (6-12 months) | INF179K01VF7 |
| debt | medium_debt | ICICI Prudential All Seasons Bond Fund - Direct Plan - Growth | Dynamic Term Fund | INF109K016E5 |
| debt | high_risk_debt | ICICI Prudential Corporate Bond Fund - Direct Plan - Growth | Corporate Bond Fund | INF109K016B1 |
| debt | long_duration_debt | SBI Constant Maturity 10 Year Gilt Fund - Direct Plan - Growth | 10-Year Constant Maturity Gilt Fund | INF200K01SK7 |
| debt | floating_debt | Nippon India Floater Fund - Direct Plan Growth Plan - Growth Option | Floating Interest Rates Fund | INF204K01E05 |
| debt | debt | Nippon India Arbitrage Fund - Direct Plan - Growth | Arbitrage Fund | INF204K01XZ7 |
| debt | others | SBI Conservative Hybrid Fund - Direct Plan - Growth | Conservative Hybrid Fund (Equity 10-25%, Debt 75-90%) | INF200K01TS8 |
```

- [ ] **Step 2: Verify the file looks correct**

```bash
grep "Nippon India Arbitrage" src/goal_based_allocation/references/scheme_classification.md
```

Expected: `| debt | debt | Nippon India Arbitrage Fund - Direct Plan - Growth | Arbitrage Fund | INF204K01XZ7 |`

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/references/scheme_classification.md
git commit -m "feat(goal-alloc): add income arbitrage fund mapping for long-term debt subgroup"
```

---

### Task 3: Rewrite long-term-goals.md with full 4-phase logic

**Files:**
- Rewrite: `src/goal_based_allocation/references/long-term-goals.md`

- [ ] **Step 1: Replace the file contents**

Write the following content to `src/goal_based_allocation/references/long-term-goals.md`:

```markdown
# Step 4 — Long-Term Goals (> 60 Months) + Leftover Corpus

Allocate goals with `time_to_goal_months > 60`, plus any leftover corpus after Steps 1–3,
treated as a long-term wealth creation goal.

---

## Inputs Required

- `goals` — filter to goals where `time_to_goal_months > 60`
- `remaining_corpus` — from Step 3 output (`step3_medium_term.output.remaining_corpus`)
- `effective_risk_score`
- `age`
- `tax_regime`, `section_80c_utilized`
- `market_commentary` — view scores object with fields: `equities`, `debt`, `others`,
  `low_beta_equities`, `value_equities`, `dividend_equities`, `medium_beta_equities`,
  `high_beta_equities`, `sector_equities`, `us_equities` (all default to 5)

---

## Leftover Corpus

```
leftover = remaining_corpus − sum(goal.amount_needed for all long-term goals)
```
If `leftover > 0`, treat it as an implicit "Wealth Creation" goal included in the allocation.
`total_long_term_corpus = remaining_corpus` in all cases.

---

## Shortfall Check

If `sum(long_term_goal.amount_needed) > remaining_corpus`:
- Flag shortfall, list negotiable goals.
- Proceed with allocation using `total_long_term_corpus = remaining_corpus`.
- `leftover = 0`.

---

## Phase 1 — Asset Class Min/Max Ranges

Look up `effective_risk_score` in the table below. Values are provided at 0.5-step increments.

| Score | Eq Min | Eq Max | Debt Min | Debt Max | Others Min | Others Max |
|-------|--------|--------|----------|----------|------------|------------|
| 10.0  | 70     | 100    | 0        | 20       | 0          | 10         |
| 9.5   | 65     | 100    | 0        | 25       | 0          | 10         |
| 9.0   | 60     | 100    | 0        | 30       | 0          | 10         |
| 8.5   | 60     | 90     | 5        | 30       | 0          | 10         |
| 8.0   | 55     | 85     | 10       | 30       | 0          | 15         |
| 7.5   | 50     | 80     | 10       | 35       | 5          | 15         |
| 7.0   | 45     | 75     | 15       | 40       | 5          | 15         |
| 6.5   | 40     | 70     | 20       | 45       | 5          | 15         |
| 6.0   | 35     | 65     | 25       | 50       | 5          | 15         |
| 5.5   | 30     | 60     | 30       | 55       | 5          | 15         |
| 5.0   | 30     | 60     | 30       | 60       | 5          | 15         |
| 4.5   | 25     | 55     | 35       | 65       | 5          | 15         |
| 4.0   | 20     | 55     | 35       | 70       | 5          | 15         |
| 3.5   | 15     | 50     | 40       | 70       | 5          | 15         |
| 3.0   | 15     | 45     | 40       | 75       | 5          | 15         |
| 2.5   | 10     | 45     | 40       | 80       | 5          | 15         |
| 2.0   | 10     | 40     | 40       | 85       | 5          | 15         |
| 1.5   | 5      | 35     | 45       | 90       | 5          | 15         |
| 1.0   | 5      | 30     | 45       | 95       | 5          | 15         |

**Score interpolation (for scores not in the table):**
```
Find the two nearest table rows bracketing the actual score.
t = (actual_score − lower_row_score) / 0.5
interpolated_min = min_at_lower + t × (min_at_upper − min_at_lower)
interpolated_max = max_at_lower + t × (max_at_upper − max_at_lower)
```
Round interpolated values to nearest integer. Apply for each of equities, debt, others.

### Others Allocation Caveat (Risk Score 8.0–10.0)

When `effective_risk_score >= 8.0`, allocate to the `others` class **only if**
`market_commentary.others > 6`. Otherwise set Others Min = 0, Others Max = 0
(zero out the others allocation entirely and redistribute its portion to equities and debt
in proportion to their current targets).

### Intergenerational Transfer Override

If `age > 60` AND any goal in the long-term list has `investment_goal = "intergenerational_transfer"`:
1. Compute `adjusted_score = min(effective_risk_score + 2, 9)` (cap at 9).
2. Look up `adjusted_score` in the table — use the **Min values only** from that row
   (Eq Min, Debt Min, Others Min).
3. Keep the **Max values** from the original `effective_risk_score` row unchanged.

This raises the allocation floor without capping the ceiling.

---

## Phase 2 — Market Commentary Proportional Scaling

For each of equities, debt, others, apply:

```
midpoint = (Min + Max) / 2
range_half = (Max − Min) / 2
normalized_view = (view_score − 5) / 5
raw_target = midpoint + normalized_view × range_half
```

Where:
- `view_score` for equities = `market_commentary.equities`
- `view_score` for debt = `market_commentary.debt`
- `view_score` for others = `market_commentary.others`

This maps view_score=1 → Min, view_score=5 → midpoint, view_score=10 → Max.

**Normalize to 100%:**
1. Sum the three raw_targets.
2. Scale each: `scaled = raw_target × 100 / sum_of_raw_targets`
3. If any scaled value breaches its Min or Max: clamp it to the bound, redistribute
   the excess/deficit proportionally between the remaining two.
4. Round each to the nearest integer.
5. If the three integers do not sum to 100: add or subtract 1 from the largest value.

Result: `equities_pct`, `debt_pct`, `others_pct` — integers summing to exactly 100.

Compute amounts:
```
equities_amount = total_long_term_corpus × equities_pct / 100  (round to nearest 100)
debt_amount     = total_long_term_corpus × debt_pct / 100      (round to nearest 100)
others_amount   = total_long_term_corpus × others_pct / 100    (round to nearest 100)
```

---

## Phase 3 — ELSS First-Pass

**Condition:** `tax_regime = "old"` AND `section_80c_utilized < 150000`

```
elss_headroom           = 150000 − section_80c_utilized
equity_corpus           = equities_amount
elss_amount             = min(elss_headroom, equity_corpus)  (round to nearest 100)
residual_equity_corpus  = equity_corpus − elss_amount
```

If condition NOT met:
- `elss_amount = 0`
- `residual_equity_corpus = equities_amount`

`tax_efficient_equities` counts fully toward the equity total.

---

## Phase 4 — Subgroup Allocation

### Equity Subgroups

**Pool:** `total_equity_for_subgroups = residual_equity_corpus`

Allocate across 7 standard equity subgroups using the guardrail table. Percentages are
**% of `total_equity_for_subgroups`** (not % of total corpus).

| Score | low_beta | value | dividend | medium_beta | high_beta | sector | us_equities |
|-------|----------|-------|----------|-------------|-----------|--------|-------------|
| 10    | 10–20    | 0–25  | 0–20     | 10–25       | 10–25     | 0–10   | 10–25       |
| 9     | 10–20    | 5–25  | 0–20     | 10–25       | 10–25     | 0–10   | 10–25       |
| 8     | 10–20    | 5–25  | 0–20     | 5–25        | 5–20      | 0–10   | 10–20       |
| 7     | 5–20     | 2–20  | 0–25     | 5–20        | 5–20      | 0–10   | 10–20       |
| 6     | 5–20     | 2–20  | 0–25     | 5–20        | 2–15      | 0–10   | 5–20        |
| 5     | 2–20     | 2–20  | 0–20     | 5–15        | 0–15      | 0–10   | 5–15        |
| 4     | 2–15     | 2–15  | 0–20     | 0–15        | 0–10      | 0–5    | 5–15        |
| 3     | 2–15     | 0–15  | 0–20     | 0–15        | 0–10      | 0–5    | 0–10        |
| 2     | 0–10     | 0–10  | 0–20     | 0–15        | 0–5       | 0–5    | 0–5         |
| 1     | 0–10     | 0–10  | 0–15     | 0–15        | 0–5       | 0–0    | 0–5         |

Interpolate for non-integer scores using the same formula as Phase 1.

Apply proportional scaling per subgroup using `market_commentary.<subgroup>` (same formula
as Phase 2). After computing raw targets:
1. Normalize so the 7 targets sum to 100% of `total_equity_for_subgroups`.
2. Clamp any value that breaches its Min or Max; redistribute proportionally.
3. Round each to nearest integer.
4. Drop any subgroup whose final allocation rounds to < 1% of `total_equity_for_subgroups`
   (set to 0), redistribute proportionally to remaining subgroups.
5. If 7 subgroup percentages do not sum to 100 after rounding, adjust the largest by ±1.

Convert subgroup percentages to amounts:
```
subgroup_amount = total_equity_for_subgroups × subgroup_pct / 100  (round to nearest 100)
```

### Debt

All of `debt_amount` → single `debt` key. Instrument: income arbitrage fund.
No subgroup table. No market commentary applied.

### Others

All of `others_amount` → `gold_commodities` key.

---

## Invariants (verify before outputting)

- `equities_pct + debt_pct + others_pct = 100`
- `sum(7 equity subgroup amounts) + elss_amount = equities_amount`  (within ±100 rounding tolerance)
- All amounts are multiples of 100

---

## JSON Output Schema

**IMPORTANT — JSON Completeness Rule:** Always include every field. Set inapplicable
numeric fields to 0 and boolean fields to false. Never omit fields.

```json
{
  "step": 4,
  "step_name": "long_term_goals",
  "asset_class_allocation": {
    "equities_pct": <integer>,
    "debt_pct": <integer>,
    "others_pct": <integer>,
    "equities_amount": <number>,
    "debt_amount": <number>,
    "others_amount": <number>
  },
  "elss": {
    "applicable": <boolean>,
    "elss_headroom": <number | null>,
    "elss_amount": <number>,
    "residual_equity_corpus": <number>
  },
  "output": {
    "goals_allocated": [
      {
        "goal_name": "<string>",
        "time_to_goal_months": <number>,
        "amount_needed": <number>,
        "goal_priority": "<string>",
        "investment_goal": "<string>"
      }
    ],
    "leftover_corpus": <number>,
    "total_long_term_corpus": <number>,
    "total_allocated": <number>,
    "remaining_corpus": 0,
    "shortfall": null,
    "subgroup_amounts": {
      "tax_efficient_equities": <number>,
      "low_beta_equities": <number>,
      "value_equities": <number>,
      "dividend_equities": <number>,
      "medium_beta_equities": <number>,
      "high_beta_equities": <number>,
      "sector_equities": <number>,
      "us_equities": <number>,
      "debt": <number>,
      "gold_commodities": <number>
    }
  }
}
```
```

- [ ] **Step 2: Verify the file was written correctly**

```bash
grep -c "Phase" src/goal_based_allocation/references/long-term-goals.md
```

Expected output: `4` (four Phase headings).

```bash
grep "PLACEHOLDER" src/goal_based_allocation/references/long-term-goals.md
```

Expected output: (no output — placeholder must be gone).

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/references/long-term-goals.md
git commit -m "feat(goal-alloc): replace long-term goals placeholder with 4-phase allocation logic"
```

---

### Task 4: Update step 4 human prompt in prompts.py

**Files:**
- Modify: `src/goal_based_allocation/prompts.py` lines 81–94

- [ ] **Step 1: Replace the `_STEP4_HUMAN` string**

In `src/goal_based_allocation/prompts.py`, replace the current `_STEP4_HUMAN` block:

```python
_STEP4_HUMAN = """\
Accumulated state (client inputs + Steps 1–3 outputs):

{state_json}

Allocate all goals where time_to_goal_months > 60, plus any leftover corpus as
wealth creation, from step3_medium_term.output.remaining_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
```

With:

```python
_STEP4_HUMAN = """\
Accumulated state (client inputs + Steps 1–3 outputs):

{state_json}

Allocate all goals where time_to_goal_months > 60, plus any leftover corpus as
wealth creation, from step3_medium_term.output.remaining_corpus.
Run all 4 phases in order:
  Phase 1 — look up asset-class min/max from effective_risk_score; apply intergenerational
             transfer override if age > 60 and any goal has investment_goal = "intergenerational_transfer"
  Phase 2 — apply market_commentary proportional scaling to get equities_pct, debt_pct,
             others_pct summing to 100
  Phase 3 — ELSS first-pass if tax_regime = "old" and section_80c_utilized < 150000
  Phase 4 — allocate residual equity across 7 subgroups using guardrail table and
             market_commentary subgroup scores; all debt → single "debt" key; others → gold_commodities

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
```

- [ ] **Step 2: Run prompt slimmer tests to verify nothing broke**

```bash
cd project/backend/AI_Agents
python -m pytest src/goal_based_allocation/Testing/test_prompts.py -v
```

Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/prompts.py
git commit -m "feat(goal-alloc): update step 4 prompt to describe all 4 allocation phases"
```

---

### Task 5: Add intergenerational transfer profile and run smoke test

**Files:**
- Modify: `src/goal_based_allocation/Testing/dev_run_samples.py`

- [ ] **Step 1: Add a 4th profile to `PROFILES`**

In `src/goal_based_allocation/Testing/dev_run_samples.py`, add this entry to the `PROFILES` list after the Meera Joshi entry:

```python
    ("Arjun Mehta", AllocationInput(
        effective_risk_score=6.5,
        age=62,
        annual_income=800_000,
        osi=1.0,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        risk_willingness=6.0,
        risk_capacity_score=7.0,
        net_financial_assets=8_000_000,
        occupation_type="retired",
        total_corpus=8_000_000,
        monthly_household_expense=80_000,
        tax_regime="old",
        section_80c_utilized=50_000,
        effective_tax_rate=20.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=True,
        goals=[
            Goal(goal_name="Estate Transfer", time_to_goal_months=120,
                 amount_needed=5_000_000, goal_priority="non_negotiable",
                 investment_goal="intergenerational_transfer"),
            Goal(goal_name="Travel Fund", time_to_goal_months=24,
                 amount_needed=500_000, goal_priority="negotiable"),
        ],
    )),
```

- [ ] **Step 2: Run smoke test (requires `ANTHROPIC_API_KEY` in environment)**

```bash
cd project/backend/AI_Agents
python -m src.goal_based_allocation.Testing.dev_run_samples
```

Expected: All 4 customers complete. In Arjun Mehta's output:
- `step4_long_term.asset_class_allocation` is present with `equities_pct + debt_pct + others_pct = 100`
- `step4_long_term.output.subgroup_amounts` contains keys `debt` and `gold_commodities` (not `high_risk_debt` / `floating_debt`)
- `step4_long_term.elss.applicable = true` (tax_regime=old, section_80c_utilized=50000 < 150000)
- No warnings about grand_total mismatch

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/Testing/dev_run_samples.py
git commit -m "feat(goal-alloc): add intergenerational transfer smoke test profile (Arjun Mehta)"
```
