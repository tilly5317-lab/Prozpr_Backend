# Goal-Based Asset Allocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `goal_based_allocation` module that allocates a client's corpus across time-bucketed goals (emergency, short-term <24m, medium-term 24–60m, long-term >60m) using a 7-step LLM pipeline.

**Architecture:** 7-step sequential LangChain LCEL chain where each step is an LLM call using a reference `.md` file as system prompt. Steps 1–4 allocate one bucket each and carry `remaining_corpus` forward. Step 5 aggregates, Step 6 validates + maps funds, Step 7 produces the final client-facing output.

**Tech Stack:** Python 3.11+, Pydantic v2, LangChain LCEL, `langchain_anthropic` (Claude Haiku), dotenv

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/goal_based_allocation/__init__.py` | Create | Module exports |
| `src/goal_based_allocation/models.py` | Create | All Pydantic models: `Goal`, `AllocationInput`, output models |
| `src/goal_based_allocation/prompts.py` | Create | 7 prompt templates + 7 state slimmer functions |
| `src/goal_based_allocation/main.py` | Create | 7-step LCEL chain + `run_allocation()` |
| `src/goal_based_allocation/references/carve-outs.md` | Create | Emergency fund + NFA carve-out rules |
| `src/goal_based_allocation/references/short-term-goals.md` | Create | Short-term (<24m) tax-rate-driven allocation rules |
| `src/goal_based_allocation/references/medium-term-goals.md` | Create | Medium-term (24–60m) risk+horizon table rules |
| `src/goal_based_allocation/references/long-term-goals.md` | Create | Long-term (>60m) placeholder rules |
| `src/goal_based_allocation/references/aggregation.md` | Create | Subgroup × bucket matrix aggregation rules |
| `src/goal_based_allocation/references/guardrails.md` | Create | Validation + fund mapping rules |
| `src/goal_based_allocation/references/presentation.md` | Create | Final presentation output schema |
| `src/goal_based_allocation/references/scheme_classification.md` | Copy | Fund type taxonomy (from `Ideal_asset_allocation`) |
| `src/goal_based_allocation/references/subgroup-allocation.md` | Copy | Subgroup guardrail tables (from `Ideal_asset_allocation`) |
| `src/goal_based_allocation/references/asset-class-allocation.md` | Copy | Asset class allocation rules (from `Ideal_asset_allocation`) |
| `src/goal_based_allocation/references/mf_subgroup_mapped.csv` | Copy | MF subgroup→fund mapping data |
| `src/goal_based_allocation/Testing/__init__.py` | Create | Test package |
| `src/goal_based_allocation/Testing/test_models.py` | Create | Pydantic model unit tests |
| `src/goal_based_allocation/Testing/test_prompts.py` | Create | State slimmer unit tests |
| `src/goal_based_allocation/Testing/dev_run_samples.py` | Create | Full integration smoke test |

---

## Task 1: Module scaffold + Pydantic models

**Files:**
- Create: `src/goal_based_allocation/__init__.py`
- Create: `src/goal_based_allocation/models.py`
- Create: `src/goal_based_allocation/Testing/__init__.py`
- Create: `src/goal_based_allocation/Testing/test_models.py`

- [ ] **Step 1: Write the failing tests**

Create `src/goal_based_allocation/Testing/test_models.py`:

```python
"""Unit tests for goal_based_allocation Pydantic models."""
import pytest
from pydantic import ValidationError
from src.goal_based_allocation.models import (
    Goal, AllocationInput, BucketShortfall, BucketAllocation,
    SubgroupFundMapping, AggregatedSubgroupRow, ClientSummary,
    GoalAllocationOutput,
)


# ── Goal ──────────────────────────────────────────────────────────────────────

def test_goal_valid():
    g = Goal(goal_name="Retirement", time_to_goal_months=240,
              amount_needed=5_000_000, goal_priority="non_negotiable")
    assert g.goal_name == "Retirement"
    assert g.time_to_goal_months == 240


def test_goal_priority_values():
    Goal(goal_name="Holiday", time_to_goal_months=18,
         amount_needed=100_000, goal_priority="negotiable")
    Goal(goal_name="Education", time_to_goal_months=36,
         amount_needed=500_000, goal_priority="non_negotiable")


def test_goal_invalid_priority():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=12,
             amount_needed=100, goal_priority="maybe")


def test_goal_negative_amount_invalid():
    with pytest.raises(ValidationError):
        Goal(goal_name="X", time_to_goal_months=12,
             amount_needed=-1, goal_priority="negotiable")


# ── AllocationInput ───────────────────────────────────────────────────────────

def _base_input(**overrides) -> dict:
    base = dict(
        effective_risk_score=7.0,
        age=35,
        annual_income=2_000_000,
        osi=0.8,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        total_corpus=3_000_000,
        monthly_household_expense=60_000,
        tax_regime="old",
        section_80c_utilized=0.0,
        effective_tax_rate=30.0,
        goals=[],
    )
    base.update(overrides)
    return base


def test_allocation_input_valid():
    inp = AllocationInput(**_base_input())
    assert inp.effective_risk_score == 7.0
    assert inp.effective_tax_rate == 30.0
    assert inp.goals == []


def test_allocation_input_with_goals():
    goals = [
        Goal(goal_name="Car", time_to_goal_months=18,
             amount_needed=800_000, goal_priority="negotiable"),
        Goal(goal_name="Retirement", time_to_goal_months=300,
             amount_needed=10_000_000, goal_priority="non_negotiable"),
    ]
    inp = AllocationInput(**_base_input(goals=goals))
    assert len(inp.goals) == 2


def test_allocation_input_tax_rate_out_of_range():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(effective_tax_rate=110.0))


def test_allocation_input_risk_score_out_of_range():
    with pytest.raises(ValidationError):
        AllocationInput(**_base_input(effective_risk_score=11.0))


def test_allocation_input_no_investment_horizon_field():
    """Confirm removed fields do not exist on the model."""
    inp = AllocationInput(**_base_input())
    assert not hasattr(inp, "investment_horizon")
    assert not hasattr(inp, "investment_horizon_years")
    assert not hasattr(inp, "investment_goal")
    assert not hasattr(inp, "short_term_expenses")


# ── Output models ─────────────────────────────────────────────────────────────

def test_bucket_shortfall_valid():
    s = BucketShortfall(
        bucket="short_term",
        shortfall_amount=100_000,
        message="Insufficient corpus for short-term goals.",
    )
    assert s.bucket == "short_term"


def test_goal_allocation_output_valid():
    goals = [Goal(goal_name="R", time_to_goal_months=300,
                  amount_needed=1_000_000, goal_priority="non_negotiable")]
    summary = ClientSummary(age=35, effective_risk_score=7.0,
                            total_corpus=3_000_000, goals=goals)
    out = GoalAllocationOutput(
        client_summary=summary,
        bucket_allocations=[],
        aggregated_subgroups=[],
        shortfall_summary=[],
        grand_total=3_000_000,
        all_amounts_in_multiples_of_100=True,
    )
    assert out.grand_total == 3_000_000
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /path/to/AI_Agents
python -m pytest src/goal_based_allocation/Testing/test_models.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` — models don't exist yet.

- [ ] **Step 3: Create `__init__.py` files**

Create `src/goal_based_allocation/__init__.py`:
```python
"""Goal-based asset allocation pipeline."""
```

Create `src/goal_based_allocation/Testing/__init__.py`:
```python
```

- [ ] **Step 4: Create `models.py`**

Create `src/goal_based_allocation/models.py`:

```python
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class Goal(BaseModel):
    goal_name: str
    time_to_goal_months: int = Field(..., ge=1)
    amount_needed: float = Field(..., gt=0)
    goal_priority: Literal["negotiable", "non_negotiable"]


class AllocationInput(BaseModel):
    # ── From risk_profiling ──────────────────────────────────────────────────
    effective_risk_score: float = Field(..., ge=1, le=10)
    age: int
    annual_income: float
    osi: float = Field(..., ge=0.0, le=1.0)
    savings_rate_adjustment: Literal["none", "equity_boost", "equity_reduce", "skipped"]
    gap_exceeds_3: bool
    shortfall_amount: Optional[float] = None

    # ── Gathered by this module ──────────────────────────────────────────────
    total_corpus: float
    monthly_household_expense: float
    tax_regime: Literal["old", "new"]
    section_80c_utilized: float = Field(default=0.0, ge=0.0)
    emergency_fund_needed: bool = True
    primary_income_from_portfolio: bool = False
    effective_tax_rate: float = Field(..., ge=0.0, le=100.0)  # percentage 0–100
    goals: List[Goal] = []

    # ── From risk_profiling internals (optional) ─────────────────────────────
    risk_willingness: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    net_financial_assets: Optional[float] = None
    occupation_type: Optional[str] = None


# ── Output models ─────────────────────────────────────────────────────────────

class BucketShortfall(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    shortfall_amount: float
    message: str


class SubgroupFundMapping(BaseModel):
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    isin: str
    amount: float


class BucketAllocation(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    goals: List[Goal]
    total_goal_amount: float
    allocated_amount: float
    shortfall: Optional[BucketShortfall] = None
    subgroup_amounts: dict  # subgroup -> amount


class AggregatedSubgroupRow(BaseModel):
    subgroup: str
    sub_category: str
    emergency: float
    short_term: float
    medium_term: float
    long_term: float
    total: float
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

- [ ] **Step 5: Run tests and verify they pass**

```bash
cd /path/to/AI_Agents
python -m pytest src/goal_based_allocation/Testing/test_models.py -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/goal_based_allocation/__init__.py \
        src/goal_based_allocation/models.py \
        src/goal_based_allocation/Testing/__init__.py \
        src/goal_based_allocation/Testing/test_models.py
git commit -m "feat(goal-alloc): add models and Pydantic validation tests"
```

---

## Task 2: Reference file — carve-outs.md

**Files:**
- Create: `src/goal_based_allocation/references/carve-outs.md`

No unit tests — this is an LLM system prompt document.

- [ ] **Step 1: Create carve-outs.md**

Create `src/goal_based_allocation/references/carve-outs.md`:

```markdown
# Step 1 — Emergency Carve-Out

Ring-fence emergency funds from `total_corpus` before allocating to any goals.
All emergency carve-out amounts go into `debt_subgroup` only.

---

## 1a. Emergency Fund

**Inputs:** `monthly_household_expense`, `primary_income_from_portfolio`, `emergency_fund_needed`

If `emergency_fund_needed = false`: skip this carve-out (emergency_fund_amount = 0).

### Base Emergency Fund
Allocate **3 months** of `monthly_household_expense` into `debt_subgroup`.

### Portfolio-Dependent Income
If `primary_income_from_portfolio = true`: allocate **6 months** instead of 3.

---

## 1b. Negative Net Financial Assets Carve-Out

**Input:** `net_financial_assets`

If `net_financial_assets < 0`: ring-fence `abs(net_financial_assets)` into `debt_subgroup`.

---

## Shortfall Check

After computing all emergency amounts:
```
total_emergency = emergency_fund_amount + nfa_carveout_amount
remaining_corpus = total_corpus - total_emergency
```

If `total_emergency > total_corpus`:
- Set `remaining_corpus = 0`
- Set `shortfall.amount = total_emergency - total_corpus`
- Set `shortfall.message` in plain language mentioning the client should increase their corpus.

---

## JSON Output Schema

All amounts rounded to nearest multiple of 100.

```json
{
  "step": 1,
  "step_name": "emergency_carve_out",
  "output": {
    "emergency_fund_months": <number>,
    "emergency_fund_amount": <number>,
    "nfa_carveout_amount": <number>,
    "total_emergency": <number>,
    "remaining_corpus": <number>,
    "shortfall": null,
    "subgroup_amounts": {
      "debt_subgroup": <number>
    }
  }
}
```

If there is a shortfall, replace `null` with:
```json
{
  "bucket": "emergency",
  "shortfall_amount": <number>,
  "message": "<plain-language explanation>"
}
```
```

- [ ] **Step 2: Commit**

```bash
git add src/goal_based_allocation/references/carve-outs.md
git commit -m "feat(goal-alloc): add emergency carve-out reference doc"
```

---

## Task 3: Reference file — short-term-goals.md

**Files:**
- Create: `src/goal_based_allocation/references/short-term-goals.md`

- [ ] **Step 1: Create short-term-goals.md**

Create `src/goal_based_allocation/references/short-term-goals.md`:

```markdown
# Step 2 — Short-Term Goals (< 24 Months)

Allocate all goals with `time_to_goal_months < 24` from the remaining corpus after Step 1.

---

## Inputs Required

- `goals` — filter to goals where `time_to_goal_months < 24`
- `effective_tax_rate` — percentage (0–100)
- `remaining_corpus` — from Step 1 output

---

## Instrument Selection (Tax-Rate Driven)

The instrument choice is the same for ALL short-term goals regardless of their individual timeline:

```
if effective_tax_rate < 20:
    instrument = "debt_subgroup"
else:
    instrument = "arbitrage_income"
```

**Rationale:** Arbitrage funds are taxed as equity (LTCG ~12.5%, STCG 20%), making them more tax-efficient than debt funds (taxed at slab rate) for investors in higher brackets. For low-tax clients, plain near-debt is simpler and equally effective.

---

## Allocation

1. Sum all short-term goal amounts: `total_goal_amount = sum(goal.amount_needed)`
2. Check shortfall:
   - If `total_goal_amount > remaining_corpus`: allocated_amount = remaining_corpus, flag shortfall
   - Else: allocated_amount = total_goal_amount
3. Place `allocated_amount` entirely into the chosen instrument.

---

## Shortfall Message

If shortfall, the message must list negotiable goals first as candidates to reduce or defer.

---

## JSON Output Schema

All amounts rounded to nearest multiple of 100.

```json
{
  "step": 2,
  "step_name": "short_term_goals",
  "output": {
    "goals_in_bucket": [
      {"goal_name": "<string>", "time_to_goal_months": <number>, "amount_needed": <number>, "goal_priority": "<string>"}
    ],
    "instrument": "<debt_subgroup | arbitrage_income>",
    "total_goal_amount": <number>,
    "allocated_amount": <number>,
    "remaining_corpus": <number>,
    "shortfall": null,
    "subgroup_amounts": {
      "<instrument>": <number>
    }
  }
}
```
```

- [ ] **Step 2: Commit**

```bash
git add src/goal_based_allocation/references/short-term-goals.md
git commit -m "feat(goal-alloc): add short-term goals reference doc"
```

---

## Task 4: Reference file — medium-term-goals.md

**Files:**
- Create: `src/goal_based_allocation/references/medium-term-goals.md`

- [ ] **Step 1: Create medium-term-goals.md**

Create `src/goal_based_allocation/references/medium-term-goals.md`:

```markdown
# Step 3 — Medium-Term Goals (24–60 Months)

Allocate goals with `24 <= time_to_goal_months <= 60` from the remaining corpus after Step 2.
Each goal is allocated independently using its own timeline.

---

## Inputs Required

- `goals` — filter to goals where `24 <= time_to_goal_months <= 60`
- `effective_risk_score`
- `effective_tax_rate` — percentage (0–100)
- `remaining_corpus` — from Step 2 output

---

## Step A — Determine Risk Bucket

| Risk Bucket | Condition |
|-------------|-----------|
| Low | `effective_risk_score < 4` |
| Medium | `4 <= effective_risk_score <= 7` |
| High | `effective_risk_score > 7` |

---

## Step B — Equity / Debt Split per Goal

Convert `time_to_goal_months` to horizon years: `horizon_years = floor(time_to_goal_months / 12)`.
- If `horizon_years <= 3` (i.e. 24–36 months): use 3-year row.
- If `horizon_years <= 4` (i.e. 36-48 months): use 3-year row.
- If `horizon_years <= 5` (i.e. 48-60 months): use 3-year row.
- If `horizon_years > 5`: use 5-year row (should not happen given bucket bounds).
- Others = always 0%.

| Horizon | Low Risk | Medium Risk | High Risk |
|---------|----------|-------------|-----------|
| 5 years | 50% E / 50% D | 70% E / 30% D | 80% E / 20% D |
| 4 years | 35% E / 65% D | 50% E / 50% D | 65% E / 35% D |
| 3 years | 0% E / 100% D | 0% E / 100% D | 0% E / 100% D |

---

## Step C — Equity Instrument

For the equity portion (when > 0%), always use `multi_asset`. Do NOT break into subgroups.

---

## Step D — Debt Instrument Preference (applies to ALL medium-term goals)

```
if effective_tax_rate < 20:
    debt_instrument = "arbitrage_plus_income"
else:
    debt_instrument = "pure_debt"
```

---

## Allocation per Goal

For each goal:
1. Look up equity_pct and debt_pct from the table.
2. `equity_amount = goal.amount_needed × equity_pct / 100`
3. `debt_amount = goal.amount_needed × debt_pct / 100`

Sum across all goals for total subgroup amounts.

---

## Shortfall Check

After computing total medium-term allocation:
- `total_goal_amount = sum(goal.amount_needed for all medium-term goals)`
- If `total_goal_amount > remaining_corpus`: flag shortfall, list negotiable goals.

---

## JSON Output Schema

All amounts rounded to nearest multiple of 100.

```json
{
  "step": 3,
  "step_name": "medium_term_goals",
  "output": {
    "risk_bucket": "<Low | Medium | High>",
    "debt_instrument": "<arbitrage_plus_income | pure_debt>",
    "goals_allocated": [
      {
        "goal_name": "<string>",
        "time_to_goal_months": <number>,
        "amount_needed": <number>,
        "goal_priority": "<string>",
        "horizon_years": <number>,
        "equity_pct": <number>,
        "debt_pct": <number>,
        "equity_amount": <number>,
        "debt_amount": <number>
      }
    ],
    "total_goal_amount": <number>,
    "allocated_amount": <number>,
    "remaining_corpus": <number>,
    "shortfall": null,
    "subgroup_amounts": {
      "multi_asset": <number>,
      "<arbitrage_plus_income | pure_debt>": <number>
    }
  }
}
```
```

- [ ] **Step 2: Commit**

```bash
git add src/goal_based_allocation/references/medium-term-goals.md
git commit -m "feat(goal-alloc): add medium-term goals reference doc"
```

---

## Task 5: Reference file — long-term-goals.md (placeholder)

**Files:**
- Create: `src/goal_based_allocation/references/long-term-goals.md`

- [ ] **Step 1: Create long-term-goals.md**

Create `src/goal_based_allocation/references/long-term-goals.md`:

```markdown
# Step 4 — Long-Term Goals (> 60 Months) + Leftover Corpus

Allocate goals with `time_to_goal_months > 60`, plus any leftover corpus after Steps 1–3,
treated as a long-term wealth creation goal.

---

## Inputs Required

- `goals` — filter to goals where `time_to_goal_months > 60`
- `remaining_corpus` — from Step 3 output
- `effective_risk_score`
- `tax_regime`, `section_80c_utilized`, `annual_income`

---

## Leftover Corpus

After allocating named long-term goals:
```
leftover = remaining_corpus - sum(long_term_goal.amount_needed)
```
If `leftover > 0`, treat it as an implicit goal named "Wealth Creation" and allocate it
alongside the long-term goals using the same subgroup logic.

---

## Allocation Logic

**PLACEHOLDER — Full subgroup allocation logic is TBD.**

For now, allocate the entire long-term corpus (named goals + leftover) as follows:
- 60% to `medium_beta_equities` (placeholder equity)
- 40% to `medium_debt` (placeholder debt)

This will be replaced with the full risk-score + horizon + ELSS + market-commentary
subgroup logic in a future update.

---

## Shortfall Check

If `sum(long_term_goal.amount_needed) > remaining_corpus`:
- Flag shortfall, list negotiable goals.
- Leftover = 0 in this case.

---

## JSON Output Schema

All amounts rounded to nearest multiple of 100.

```json
{
  "step": 4,
  "step_name": "long_term_goals",
  "output": {
    "goals_allocated": [
      {
        "goal_name": "<string>",
        "time_to_goal_months": <number>,
        "amount_needed": <number>,
        "goal_priority": "<string>"
      }
    ],
    "leftover_corpus": <number>,
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
      "high_risk_debt": <number>,
      "long_duration_debt": <number>,
      "floating_debt": <number>,
      "medium_debt": <number>,
      "gold_commodities": <number>
    }
  }
}
```
```

- [ ] **Step 2: Commit**

```bash
git add src/goal_based_allocation/references/long-term-goals.md
git commit -m "feat(goal-alloc): add long-term goals reference doc (placeholder logic)"
```

---

## Task 6: Reference file — aggregation.md

**Files:**
- Create: `src/goal_based_allocation/references/aggregation.md`

- [ ] **Step 1: Create aggregation.md**

Create `src/goal_based_allocation/references/aggregation.md`:

```markdown
# Step 5 — Aggregation

Consolidate the subgroup allocations from all 4 bucket steps into a unified
subgroup × investment_type matrix. No new allocation decisions are made here.

---

## Inputs Required

- `step1_emergency.output.subgroup_amounts`
- `step2_short_term.output.subgroup_amounts`
- `step3_medium_term.output.subgroup_amounts`
- `step4_long_term.output.subgroup_amounts`
- `total_corpus` (for grand total verification)

---

## All Subgroups

Include every subgroup that appears across any step. Subgroups with zero allocation
in a bucket show 0 for that bucket column.

Subgroups to include (set missing to 0):
`debt_subgroup`, `short_debt`, `arbitrage_income`, `multi_asset`, `arbitrage_plus_income`,
`pure_debt`, `tax_efficient_equities`, `low_beta_equities`, `value_equities`,
`dividend_equities`, `medium_beta_equities`, `high_beta_equities`, `sector_equities`,
`us_equities`, `high_risk_debt`, `long_duration_debt`, `floating_debt`, `medium_debt`,
`gold_commodities`

Only include rows where at least one bucket column is non-zero.

---

## Verification

`grand_total = sum of all amounts across all rows and all bucket columns`

`grand_total` must equal `total_corpus`. If not, flag the discrepancy.

---

## JSON Output Schema

```json
{
  "step": 5,
  "step_name": "aggregation",
  "output": {
    "rows": [
      {
        "subgroup": "<string>",
        "emergency": <number>,
        "short_term": <number>,
        "medium_term": <number>,
        "long_term": <number>,
        "total": <number>
      }
    ],
    "grand_total": <number>,
    "grand_total_matches_corpus": <boolean>
  }
}
```
```

- [ ] **Step 2: Commit**

```bash
git add src/goal_based_allocation/references/aggregation.md
git commit -m "feat(goal-alloc): add aggregation reference doc"
```

---

## Task 7: Reference files — guardrails.md + presentation.md

**Files:**
- Create: `src/goal_based_allocation/references/guardrails.md`
- Create: `src/goal_based_allocation/references/presentation.md`

- [ ] **Step 1: Create guardrails.md**

Create `src/goal_based_allocation/references/guardrails.md`:

```markdown
# Step 6 — Guardrails Validation + Fund Mapping

Two responsibilities:
1. Validate the long-term subgroup allocation (Step 4) against min/max bounds.
2. Map each allocated subgroup to a recommended mutual fund using the `mf_subgroup_mapped.csv` reference.

---

## Part 1 — Guardrails Validation (Long-Term Only)

Validate `step4_long_term.output.subgroup_amounts` against the bounds tables.
Use `effective_risk_score` for interpolation. Same rules as legacy Step 4 guardrails:

- Rule 1: All subgroup amounts sum to `step4_long_term.output.total_allocated`.
- Rule 2: Asset class totals (equity / debt / others) fall within min/max bounds for the risk score.
- Rule 3: Each subgroup's share of its parent class falls within subgroup bounds.

If any rule is violated, correct and re-check. Apply the same violation resolution process
(clamp → redistribute → re-sum) as in the `Ideal_asset_allocation` guardrails.

**PLACEHOLDER:** Long-term allocation logic is a placeholder (Task 5). Once real subgroup
logic is implemented, update validation here accordingly.

---

## Part 2 — Fund Mapping

For every subgroup in the aggregation output (Step 5), look up the `sub_category` and
a recommended fund from the `mf_subgroup_mapped.csv` data provided in the system prompt.

### Mapping Rule

For each `asset_subgroup`:
1. Find rows in the CSV where `asset_subgroup` matches.
2. Pick one representative fund (prefer Growth option: `isinGrowth` is not null).
3. Return: `sub_category`, `recommended_fund` (schemeName), `isin` (isinGrowth).

### New Subgroups (not in CSV)

For the new subgroups introduced in this module, use these mappings:
- `arbitrage_income` → sub_category: "Arbitrage Fund", recommended_fund: "Nippon India Arbitrage Fund - Growth"
- `multi_asset` → sub_category: "Multi Asset Allocation Fund", recommended_fund: "ICICI Prudential Multi-Asset Fund - Growth"
- `arbitrage_plus_income` → sub_category: "Arbitrage + Income Fund", recommended_fund: "HDFC Arbitrage Fund - Growth"
- `pure_debt` → sub_category: "Short Duration Fund", recommended_fund: "HDFC Short Term Debt Fund - Growth"

---

## JSON Output Schema

```json
{
  "step": 6,
  "step_name": "guardrails_and_fund_mapping",
  "output": {
    "validation": {
      "all_rules_pass": <boolean>,
      "violations_found": [],
      "adjustments_made": []
    },
    "fund_mappings": [
      {
        "asset_subgroup": "<string>",
        "sub_category": "<string>",
        "recommended_fund": "<string>",
        "isin": "<string>",
        "total_amount": <number>
      }
    ]
  }
}
```
```

- [ ] **Step 2: Create presentation.md**

Create `src/goal_based_allocation/references/presentation.md`:

```markdown
# Step 7 — Presentation

Produce the final JSON output for the frontend. Combine all prior step outputs into
a single client-facing document.

---

## Inputs Required

- Client profile: `age`, `occupation_type`, `effective_risk_score`, `total_corpus`, `goals`
- Step 1–4 outputs: bucket allocations, subgroup amounts, shortfall flags
- Step 5 output: aggregated subgroup × investment_type matrix
- Step 6 output: fund mappings, validated long-term allocation

---

## Rationale Guidelines

Write all rationale text in plain, everyday language. No finance jargon.
Use "you / your". Each explanation: 1–3 short sentences. Focus on the *why*.

Do NOT use: alpha, beta, duration risk, NAV, asset class, volatility, liquidity,
corpus, portfolio rebalancing.

Explain:
- **Emergency bucket**: why money is set aside, how many months it covers
- **Short-term bucket**: why each goal uses the chosen instrument (tax reasoning in simple terms)
- **Medium-term bucket**: why the equity/debt split was chosen for each goal's timeline
- **Long-term bucket**: why the allocation is growth-oriented for long horizons
- **Shortfalls** (if any): friendly message suggesting either more investment or cutting negotiable goals

---

## JSON Output Schema

```json
{
  "step": 7,
  "step_name": "presentation",
  "client_summary": {
    "age": <number>,
    "occupation": "<string | null>",
    "effective_risk_score": <number>,
    "total_corpus": <number>,
    "goals": [
      {"goal_name": "<string>", "time_to_goal_months": <number>,
       "amount_needed": <number>, "goal_priority": "<string>"}
    ]
  },
  "bucket_allocations": [
    {
      "bucket": "<emergency | short_term | medium_term | long_term>",
      "goals": [...],
      "total_goal_amount": <number>,
      "allocated_amount": <number>,
      "shortfall": null,
      "subgroup_amounts": {"<subgroup>": <number>},
      "rationale": "<plain-language explanation>"
    }
  ],
  "aggregated_subgroups": [
    {
      "subgroup": "<string>",
      "sub_category": "<string>",
      "emergency": <number>,
      "short_term": <number>,
      "medium_term": <number>,
      "long_term": <number>,
      "total": <number>,
      "fund_mapping": {
        "asset_subgroup": "<string>",
        "sub_category": "<string>",
        "recommended_fund": "<string>",
        "isin": "<string>",
        "amount": <number>
      }
    }
  ],
  "shortfall_summary": [],
  "grand_total": <number>,
  "all_amounts_in_multiples_of_100": <boolean>
}
```
```

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/references/guardrails.md \
        src/goal_based_allocation/references/presentation.md
git commit -m "feat(goal-alloc): add guardrails and presentation reference docs"
```

---

## Task 8: Copy static reference files

**Files:**
- Copy from `Ideal_asset_allocation/references/`: `scheme_classification.md`, `subgroup-allocation.md`, `asset-class-allocation.md`, `mf_subgroup_mapped.csv`

- [ ] **Step 1: Copy the files**

```bash
SRC=src/Ideal_asset_allocation/references
DST=src/goal_based_allocation/references

cp "$SRC/scheme_classification.md"   "$DST/scheme_classification.md"
cp "$SRC/subgroup-allocation.md"     "$DST/subgroup-allocation.md"
cp "$SRC/asset-class-allocation.md"  "$DST/asset-class-allocation.md"
cp "$SRC/mf_subgroup_mapped.csv"     "$DST/mf_subgroup_mapped.csv"
```

Run from: `project/backend/AI_Agents/`

- [ ] **Step 2: Verify files exist**

```bash
ls src/goal_based_allocation/references/
```

Expected: all 11 reference files listed.

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/references/
git commit -m "feat(goal-alloc): copy static reference files from Ideal_asset_allocation"
```

---

## Task 9: prompts.py + state slimmer tests

**Files:**
- Create: `src/goal_based_allocation/prompts.py`
- Create: `src/goal_based_allocation/Testing/test_prompts.py`

- [ ] **Step 1: Write failing slimmer tests**

Create `src/goal_based_allocation/Testing/test_prompts.py`:

```python
"""Unit tests for state slimmer functions in prompts.py."""
import pytest
from src.goal_based_allocation.prompts import (
    _slim_for_step2, _slim_for_step3, _slim_for_step4,
    _slim_for_step5, _slim_for_step6, _slim_for_step7,
)


def _make_full_state() -> dict:
    return {
        "effective_risk_score": 7.0,
        "age": 35,
        "annual_income": 2_000_000,
        "osi": 0.8,
        "savings_rate_adjustment": "equity_boost",
        "gap_exceeds_3": False,
        "total_corpus": 3_000_000,
        "monthly_household_expense": 60_000,
        "tax_regime": "old",
        "section_80c_utilized": 0.0,
        "effective_tax_rate": 30.0,
        "primary_income_from_portfolio": False,
        "emergency_fund_needed": True,
        "net_financial_assets": 500_000,
        "occupation_type": "private_sector",
        "goals": [
            {"goal_name": "Car", "time_to_goal_months": 18,
             "amount_needed": 800_000, "goal_priority": "negotiable"},
        ],
        "step1_emergency": {"output": {"remaining_corpus": 2_820_000, "subgroup_amounts": {"debt_subgroup": 180_000}}},
        "step2_short_term": {"output": {"remaining_corpus": 2_020_000, "subgroup_amounts": {"debt_subgroup": 800_000}}},
        "step3_medium_term": {"output": {"remaining_corpus": 1_020_000, "subgroup_amounts": {}}},
        "step4_long_term":   {"output": {"remaining_corpus": 0, "subgroup_amounts": {}}},
        "step5_aggregation": {"output": {"rows": [], "grand_total": 3_000_000}},
        "step6_guardrails":  {"output": {"fund_mappings": [], "validation": {}}},
    }


def test_slim_for_step2_has_step1_output():
    slim = _slim_for_step2(_make_full_state())
    assert "step1_emergency" in slim
    assert slim["step1_emergency"].get("output", {}).get("remaining_corpus") == 2_820_000


def test_slim_for_step2_excludes_later_steps():
    slim = _slim_for_step2(_make_full_state())
    assert "step2_short_term" not in slim
    assert "step3_medium_term" not in slim


def test_slim_for_step3_has_step1_and_step2():
    slim = _slim_for_step3(_make_full_state())
    assert "step1_emergency" in slim
    assert "step2_short_term" in slim
    assert "step3_medium_term" not in slim


def test_slim_for_step4_has_steps_1_to_3():
    slim = _slim_for_step4(_make_full_state())
    assert "step1_emergency" in slim
    assert "step2_short_term" in slim
    assert "step3_medium_term" in slim
    assert "step4_long_term" not in slim


def test_slim_for_step5_has_all_bucket_outputs():
    slim = _slim_for_step5(_make_full_state())
    assert "step1_emergency" in slim
    assert "step2_short_term" in slim
    assert "step3_medium_term" in slim
    assert "step4_long_term" in slim
    assert "step5_aggregation" not in slim


def test_slim_for_step6_has_step4_and_step5():
    slim = _slim_for_step6(_make_full_state())
    assert "step4_long_term" in slim
    assert "step5_aggregation" in slim
    assert "effective_risk_score" in slim


def test_slim_for_step7_has_all_steps():
    slim = _slim_for_step7(_make_full_state())
    for key in ["step1_emergency", "step2_short_term", "step3_medium_term",
                "step4_long_term", "step5_aggregation", "step6_guardrails"]:
        assert key in slim
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest src/goal_based_allocation/Testing/test_prompts.py -v 2>&1 | head -20
```

Expected: `ImportError` — prompts module not yet created.

- [ ] **Step 3: Create prompts.py**

Create `src/goal_based_allocation/prompts.py`:

```python
"""
LangChain prompt templates for the 7-step goal-based allocation pipeline.
"""

import json
from pathlib import Path
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda

_REFS = Path(__file__).parent / "references"


def _load(filename: str) -> str:
    content = (_REFS / filename).read_text()
    return content.replace("{", "{{").replace("}", "}}")


def _serialize(state: dict) -> str:
    return json.dumps(state, indent=2, default=str)


# ── Step 1: Emergency Carve-Out ───────────────────────────────────────────────

_STEP1_SYSTEM = _load("carve-outs.md")
_STEP1_HUMAN = """\
Full client state (inputs only at this stage):

{state_json}

Apply the emergency carve-out rules. Work through Emergency Fund → Negative NFA
in order. All amounts to debt_subgroup. Check shortfall against total_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step1_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP1_SYSTEM),
    ("human", _STEP1_HUMAN),
])


# ── Step 2: Short-Term Goals ──────────────────────────────────────────────────

_STEP2_SYSTEM = _load("short-term-goals.md")
_STEP2_HUMAN = """\
Accumulated state (client inputs + Step 1 output):

{state_json}

Allocate all goals where time_to_goal_months < 24 from step1_emergency.output.remaining_corpus.
Apply the tax-rate instrument selection rule, then check shortfall.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step2_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP2_SYSTEM),
    ("human", _STEP2_HUMAN),
])


# ── Step 3: Medium-Term Goals ─────────────────────────────────────────────────

_STEP3_SYSTEM = _load("medium-term-goals.md")
_STEP3_HUMAN = """\
Accumulated state (client inputs + Steps 1–2 outputs):

{state_json}

Allocate all goals where 24 <= time_to_goal_months <= 60 from step2_short_term.output.remaining_corpus.
Apply risk bucket → equity/debt table per goal, then assign instruments and check shortfall.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step3_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP3_SYSTEM),
    ("human", _STEP3_HUMAN),
])


# ── Step 4: Long-Term Goals ───────────────────────────────────────────────────

_STEP4_SYSTEM = _load("long-term-goals.md")
_STEP4_HUMAN = """\
Accumulated state (client inputs + Steps 1–3 outputs):

{state_json}

Allocate all goals where time_to_goal_months > 60, plus any leftover corpus as
wealth creation, from step3_medium_term.output.remaining_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step4_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP4_SYSTEM),
    ("human", _STEP4_HUMAN),
])


# ── Step 5: Aggregation ───────────────────────────────────────────────────────

_STEP5_SYSTEM = _load("aggregation.md")
_STEP5_HUMAN = """\
Accumulated state (Steps 1–4 outputs):

{state_json}

Consolidate all four bucket subgroup_amounts into a subgroup × investment_type matrix.
Sum to grand_total and verify it equals total_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step5_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP5_SYSTEM),
    ("human", _STEP5_HUMAN),
])


# ── Step 6: Guardrails + Fund Mapping ────────────────────────────────────────

_STEP6_SYSTEM = (
    _load("guardrails.md")
    + "\n\n---\n\n## Mutual Fund Type Reference\n\n"
    + _load("scheme_classification.md")
)
_STEP6_HUMAN = """\
Accumulated state (Steps 1–5 outputs):

{state_json}

1. Validate step4_long_term.output.subgroup_amounts against guardrail rules.
2. Map every subgroup in step5_aggregation.output.rows to sub_category + recommended_fund.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step6_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP6_SYSTEM),
    ("human", _STEP6_HUMAN),
])


# ── Step 7: Presentation ──────────────────────────────────────────────────────

_STEP7_SYSTEM = _load("presentation.md")
_STEP7_HUMAN = """\
Full pipeline state (client inputs + Steps 1–6 outputs):

{state_json}

Produce the final presentation JSON. Use step6_guardrails.output.fund_mappings for
sub_category and fund recommendations. Write all rationale in plain language.
Verify grand_total equals total_corpus.

Return ONLY a valid JSON object matching the output schema — no commentary, no markdown fences.
"""
step7_prompt = ChatPromptTemplate.from_messages([
    ("system", _STEP7_SYSTEM),
    ("human", _STEP7_HUMAN),
])


# ── State slimmers ────────────────────────────────────────────────────────────

def _input_fields(state: dict) -> dict:
    """Return all top-level input fields (non-step keys)."""
    return {k: v for k, v in state.items() if not k.startswith("step")}


def _slim_for_step2(state: dict) -> dict:
    return {
        **_input_fields(state),
        "step1_emergency": {"output": state.get("step1_emergency", {}).get("output", {})},
    }


def _slim_for_step3(state: dict) -> dict:
    return {
        **_input_fields(state),
        "step1_emergency": {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
    }


def _slim_for_step4(state: dict) -> dict:
    return {
        **_input_fields(state),
        "step1_emergency":  {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
        "step3_medium_term":{"output": state.get("step3_medium_term", {}).get("output", {})},
    }


def _slim_for_step5(state: dict) -> dict:
    return {
        "total_corpus": state.get("total_corpus"),
        "step1_emergency":  {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
        "step3_medium_term":{"output": state.get("step3_medium_term", {}).get("output", {})},
        "step4_long_term":  {"output": state.get("step4_long_term", {}).get("output", {})},
    }


def _slim_for_step6(state: dict) -> dict:
    return {
        "effective_risk_score": state.get("effective_risk_score"),
        "step4_long_term":  {"output": state.get("step4_long_term", {}).get("output", {})},
        "step5_aggregation":{"output": state.get("step5_aggregation", {}).get("output", {})},
    }


def _slim_for_step7(state: dict) -> dict:
    input_keys = [
        "age", "occupation_type", "effective_risk_score", "total_corpus",
        "goals", "monthly_household_expense", "primary_income_from_portfolio",
        "tax_regime", "section_80c_utilized", "effective_tax_rate",
    ]
    return {
        **{k: state[k] for k in input_keys if k in state},
        "step1_emergency":  {"output": state.get("step1_emergency", {}).get("output", {})},
        "step2_short_term": {"output": state.get("step2_short_term", {}).get("output", {})},
        "step3_medium_term":{"output": state.get("step3_medium_term", {}).get("output", {})},
        "step4_long_term":  {"output": state.get("step4_long_term", {}).get("output", {})},
        "step5_aggregation":{"output": state.get("step5_aggregation", {}).get("output", {})},
        "step6_guardrails": {"output": state.get("step6_guardrails", {}).get("output", {})},
    }
```

- [ ] **Step 4: Run tests and verify they pass**

```bash
python -m pytest src/goal_based_allocation/Testing/test_prompts.py -v
```

Expected: All 7 slimmer tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/goal_based_allocation/prompts.py \
        src/goal_based_allocation/Testing/test_prompts.py
git commit -m "feat(goal-alloc): add prompts, state slimmers and slimmer tests"
```

---

## Task 10: main.py — 7-step LCEL chain

**Files:**
- Create: `src/goal_based_allocation/main.py`

- [ ] **Step 1: Create main.py**

Create `src/goal_based_allocation/main.py`:

```python
"""
7-step LangChain LCEL pipeline for goal-based asset allocation.

Steps:
  1. step1_emergency     — emergency carve-out
  2. step2_short_term    — short-term goals (<24m)
  3. step3_medium_term   — medium-term goals (24–60m)
  4. step4_long_term     — long-term goals (>60m) + leftover
  5. step5_aggregation   — subgroup × bucket matrix
  6. step6_guardrails    — validation + fund mapping
  7. step7_presentation  — final client output

Usage:
    from goal_based_allocation.main import run_allocation
    from goal_based_allocation.models import AllocationInput, Goal

    result = run_allocation(AllocationInput(...))
"""

import json
import logging
import re
import time
from typing import Optional

from langchain_anthropic import ChatAnthropic
from langchain_core.runnables import RunnableLambda, RunnablePassthrough

from .models import AllocationInput, GoalAllocationOutput
from .prompts import (
    step1_prompt, step2_prompt, step3_prompt, step4_prompt,
    step5_prompt, step6_prompt, step7_prompt,
    _serialize,
    _slim_for_step2, _slim_for_step3, _slim_for_step4,
    _slim_for_step5, _slim_for_step6, _slim_for_step7,
)

logger = logging.getLogger(__name__)

_llm = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=3000)
_llm_step7 = ChatAnthropic(model="claude-haiku-4-5-20251001", max_tokens=5000)


def _add_cache_control(messages: list) -> list:
    for msg in messages:
        if msg.type == "system":
            msg.additional_kwargs["cache_control"] = {"type": "ephemeral"}
    return messages


def _extract_text_content(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _extract_json(text: str) -> dict:
    raw = text.strip()
    if raw.startswith("```"):
        for part in raw.split("```")[1:]:
            candidate = part.strip()
            if candidate.startswith("json"):
                candidate = candidate[4:].strip()
            if candidate.startswith("{"):
                obj, _ = json.JSONDecoder().raw_decode(candidate)
                return obj
    if raw.startswith("{"):
        obj, _ = json.JSONDecoder().raw_decode(raw)
        return obj
    match = re.search(r"\{", raw)
    if match:
        obj, _ = json.JSONDecoder().raw_decode(raw[match.start():])
        return obj
    raise json.JSONDecodeError("No JSON object found", raw, 0)


def _make_step(prompt, step_name: str, state_slicer=None, llm=None):
    _used_llm = llm or _llm

    def _run(state: dict):
        slim = state_slicer(state) if state_slicer else state
        messages = prompt.format_messages(state_json=_serialize(slim))
        _add_cache_control(messages)
        last_err: Optional[Exception] = None
        for attempt in range(3):
            response = _used_llm.invoke(messages)
            raw = _extract_text_content(response.content)
            try:
                return _extract_json(raw)
            except json.JSONDecodeError as e:
                last_err = e
                logger.warning("[%s] JSON parse failed (attempt %s/3): %s",
                               step_name, attempt + 1, e.msg)
                time.sleep(1.0 * (attempt + 1))
        raise last_err

    return RunnableLambda(_run)


# ── Pipeline ──────────────────────────────────────────────────────────────────

goal_allocation_chain = (
    RunnablePassthrough.assign(step1_emergency   = _make_step(step1_prompt, "step1_emergency"))
  | RunnablePassthrough.assign(step2_short_term  = _make_step(step2_prompt, "step2_short_term",  _slim_for_step2))
  | RunnablePassthrough.assign(step3_medium_term = _make_step(step3_prompt, "step3_medium_term", _slim_for_step3))
  | RunnablePassthrough.assign(step4_long_term   = _make_step(step4_prompt, "step4_long_term",   _slim_for_step4))
  | RunnablePassthrough.assign(step5_aggregation = _make_step(step5_prompt, "step5_aggregation", _slim_for_step5))
  | RunnablePassthrough.assign(step6_guardrails  = _make_step(step6_prompt, "step6_guardrails",  _slim_for_step6))
  | RunnablePassthrough.assign(step7_presentation= _make_step(step7_prompt, "step7_presentation",_slim_for_step7, llm=_llm_step7))
)


def run_allocation(inputs: AllocationInput) -> GoalAllocationOutput:
    """Run the 7-step pipeline and return a validated GoalAllocationOutput."""
    result = goal_allocation_chain.invoke(inputs.model_dump())
    return GoalAllocationOutput.model_validate(result["step7_presentation"])
```

- [ ] **Step 2: Verify the module imports without errors (no LLM call)**

```bash
python -c "from src.goal_based_allocation.main import goal_allocation_chain; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/main.py
git commit -m "feat(goal-alloc): add 7-step LCEL pipeline in main.py"
```

---

## Task 11: Integration smoke test

**Files:**
- Create: `src/goal_based_allocation/Testing/dev_run_samples.py`

- [ ] **Step 1: Create dev_run_samples.py**

Create `src/goal_based_allocation/Testing/dev_run_samples.py`:

```python
"""
Goal-based allocation integration smoke test.

3 dummy profiles:
  1. Ananya Singh  — 32, private sector, risk score 7.5
  2. Ravi Kumar    — 48, public sector,  risk score 5.2
  3. Meera Joshi   — 38, family business, risk score 8.5

Run:
    cd project/backend/AI_Agents
    python -m src.goal_based_allocation.Testing.dev_run_samples
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_agents_root = Path(__file__).resolve().parents[3]
_backend_root = _agents_root.parent
for env_path in [_agents_root / ".env", _backend_root / ".env"]:
    if env_path.exists():
        load_dotenv(env_path)
        break

from src.goal_based_allocation.main import goal_allocation_chain
from src.goal_based_allocation.models import AllocationInput, Goal

_TESTING_DIR = Path(__file__).resolve().parent

PROFILES: list[tuple[str, AllocationInput]] = [

    ("Ananya Singh", AllocationInput(
        effective_risk_score=7.5,
        age=32,
        annual_income=1_800_000,
        osi=0.8,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=7.0,
        risk_capacity_score=8.0,
        net_financial_assets=1_200_000,
        occupation_type="private_sector",
        total_corpus=1_200_000,
        monthly_household_expense=55_000,
        tax_regime="new",
        section_80c_utilized=0.0,
        effective_tax_rate=30.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="International Holiday", time_to_goal_months=12,
                 amount_needed=200_000, goal_priority="negotiable"),
            Goal(goal_name="Car Purchase", time_to_goal_months=36,
                 amount_needed=600_000, goal_priority="negotiable"),
            Goal(goal_name="Retirement", time_to_goal_months=336,
                 amount_needed=20_000_000, goal_priority="non_negotiable"),
        ],
    )),

    ("Ravi Kumar", AllocationInput(
        effective_risk_score=5.2,
        age=48,
        annual_income=1_200_000,
        osi=1.0,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        risk_willingness=5.0,
        risk_capacity_score=5.4,
        net_financial_assets=3_500_000,
        occupation_type="public_sector",
        total_corpus=3_500_000,
        monthly_household_expense=70_000,
        tax_regime="old",
        section_80c_utilized=150_000,
        effective_tax_rate=20.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="Child Education", time_to_goal_months=48,
                 amount_needed=1_500_000, goal_priority="non_negotiable"),
            Goal(goal_name="Home Renovation", time_to_goal_months=18,
                 amount_needed=400_000, goal_priority="negotiable"),
            Goal(goal_name="Retirement", time_to_goal_months=144,
                 amount_needed=15_000_000, goal_priority="non_negotiable"),
        ],
    )),

    ("Meera Joshi", AllocationInput(
        effective_risk_score=8.5,
        age=38,
        annual_income=3_500_000,
        osi=0.6,
        savings_rate_adjustment="equity_boost",
        gap_exceeds_3=False,
        risk_willingness=8.0,
        risk_capacity_score=9.0,
        net_financial_assets=5_000_000,
        occupation_type="family_business",
        total_corpus=5_000_000,
        monthly_household_expense=120_000,
        tax_regime="old",
        section_80c_utilized=150_000,
        effective_tax_rate=35.0,
        emergency_fund_needed=True,
        primary_income_from_portfolio=False,
        goals=[
            Goal(goal_name="Business Expansion", time_to_goal_months=24,
                 amount_needed=800_000, goal_priority="non_negotiable"),
            Goal(goal_name="Child Education", time_to_goal_months=60,
                 amount_needed=2_000_000, goal_priority="non_negotiable"),
            Goal(goal_name="Retirement", time_to_goal_months=264,
                 amount_needed=30_000_000, goal_priority="non_negotiable"),
        ],
    )),
]


def run_customer(name: str, client: AllocationInput) -> dict:
    print(f"\n{'=' * 60}")
    print(f"  Customer: {name}")
    print(f"  Risk Score: {client.effective_risk_score}  |  Age: {client.age}  |  Corpus: ₹{client.total_corpus:,.0f}")
    print(f"  Tax Rate: {client.effective_tax_rate}%  |  Goals: {len(client.goals)}")
    print(f"{'=' * 60}")

    result = goal_allocation_chain.invoke(client.model_dump())

    warnings: list[str] = []

    # Check grand total
    s7 = result.get("step7_presentation", {})
    grand_total = s7.get("grand_total", 0)
    if abs(grand_total - client.total_corpus) > 100:
        warnings.append(f"grand_total {grand_total} != total_corpus {client.total_corpus}")

    # Check shortfalls
    shortfalls = s7.get("shortfall_summary", [])
    if shortfalls:
        for sf in shortfalls:
            warnings.append(f"Shortfall in {sf['bucket']}: ₹{sf['shortfall_amount']:,.0f}")

    if warnings:
        for w in warnings:
            print(f"  ⚠ WARNING: {w}")
    else:
        print(f"  ✓ No warnings")

    print(f"\n  Bucket allocations:")
    for bucket in s7.get("bucket_allocations", []):
        print(f"    {bucket['bucket']:<15} allocated: ₹{bucket['allocated_amount']:>12,.0f}")
        for sg, amt in bucket.get("subgroup_amounts", {}).items():
            if amt > 0:
                print(f"      {sg:<30} ₹{amt:>12,.0f}")

    print(f"\n  Grand total: ₹{grand_total:,.0f}")
    return result


def run():
    all_results: dict[str, dict] = {}
    for customer_name, profile in PROFILES:
        all_results[customer_name] = run_customer(customer_name, profile)

    print(f"\n{'=' * 60}")
    print("ALL CUSTOMERS COMPLETED ✓")
    print(f"{'=' * 60}")

    json_path = _TESTING_DIR / "dev_output_samples.json"
    with open(json_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n💾 Full output → {json_path.name}")


if __name__ == "__main__":
    run()
```

- [ ] **Step 2: Verify it imports cleanly**

```bash
python -c "from src.goal_based_allocation.Testing.dev_run_samples import PROFILES; print(f'{len(PROFILES)} profiles loaded')"
```

Expected: `3 profiles loaded`

- [ ] **Step 3: Commit**

```bash
git add src/goal_based_allocation/Testing/dev_run_samples.py
git commit -m "feat(goal-alloc): add integration smoke test with 3 profiles"
```

---

## Task 12: Run full integration test

- [ ] **Step 1: Run unit test suite**

```bash
cd /path/to/AI_Agents
python -m pytest src/goal_based_allocation/Testing/test_models.py \
                 src/goal_based_allocation/Testing/test_prompts.py -v
```

Expected: All tests PASS.

- [ ] **Step 2: Run smoke test against real LLM (requires ANTHROPIC_API_KEY)**

```bash
python -m src.goal_based_allocation.Testing.dev_run_samples
```

Expected output for each customer:
- `✓ No warnings` (or shortfall warnings if corpus genuinely insufficient)
- Bucket breakdown printed with non-zero amounts
- `Grand total` close to `total_corpus`
- JSON saved to `Testing/dev_output_samples.json`

- [ ] **Step 3: Verify JSON output structure**

```bash
python -c "
import json
data = json.load(open('src/goal_based_allocation/Testing/dev_output_samples.json'))
for name, result in data.items():
    s7 = result.get('step7_presentation', {})
    print(name, '→ buckets:', [b['bucket'] for b in s7.get('bucket_allocations', [])])
    print('  grand_total:', s7.get('grand_total'))
"
```

Expected: Each customer has 2–4 bucket entries, grand_total matches their corpus.

- [ ] **Step 4: Final commit**

```bash
git add src/goal_based_allocation/
git commit -m "feat(goal-alloc): complete goal-based allocation module"
```