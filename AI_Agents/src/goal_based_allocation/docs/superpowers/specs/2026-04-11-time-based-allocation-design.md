# Goal-Based Asset Allocation — Design Spec

**Date:** 2026-04-11  
**Module:** `src/goal_based_allocation`  
**Status:** Approved

---

## 1. Overview

Replace the single-horizon `Ideal_asset_allocation` pipeline with a goal-based, time-bucketed allocation pipeline. Clients provide a list of named financial goals; each goal is automatically bucketed by timeline, and the corpus is allocated bucket by bucket in priority order. Any leftover corpus defaults to long-term wealth creation.

---

## 2. Input Model Changes

### New `Goal` Model

```python
class Goal(BaseModel):
    goal_name: str
    time_to_goal_months: int
    amount_needed: float          # already inflation-adjusted
    goal_priority: Literal["negotiable", "non_negotiable"]
```

### `AllocationInput` Changes

**Removed fields:**
- `investment_horizon`
- `investment_horizon_years`
- `investment_goal`
- `short_term_expenses`

**Added fields:**
- `goals: List[Goal]` — all client goals, each with name, timeline, amount, and priority
- `effective_tax_rate: float` — percentage (0–100), used in medium-term allocation logic

All other existing fields (`effective_risk_score`, `age`, `annual_income`, `osi`, `total_corpus`, `monthly_household_expense`, `tax_regime`, `section_80c_utilized`, `emergency_fund_needed`, `primary_income_from_portfolio`, `net_financial_assets`, `occupation_type`, `risk_willingness`, `risk_capacity_score`, `savings_rate_adjustment`, `gap_exceeds_3`, `shortfall_amount`) are retained unchanged.

### Goal Bucketing Rules

Goals are bucketed by `time_to_goal_months`:
- **Short-term:** `< 24` months
- **Medium-term:** `>= 24` and `<= 60` months
- **Long-term:** `> 60` months
- **Leftover corpus** (after all goals are funded): treated as a long-term wealth creation goal

---

## 3. Pipeline Structure

7-step sequential LLM chain (Approach A — all LLM steps):

| Step | State Key | Reference File | Responsibility |
|------|-----------|----------------|----------------|
| 1 | `step1_emergency` | `references/carve-outs.md` | Emergency fund (3–6 months expenses) + negative NFA carve-out. Simple: all goes to `debt_subgroup`. Flags shortfall if corpus < emergency amount. |
| 2 | `step2_short_term` | `references/short-term-goals.md` | Allocates goals <24m into `debt_subgroup` or `short_debt` based on timeline. Simple: no subgroup breakdown needed. Flags shortfall. |
| 3 | `step3_medium_term` | `references/medium-term-goals.md` | Allocates goals 24–60m into appropriate debt/hybrid instruments. Driven by `effective_tax_rate` + risk profile. Flags shortfall. |
| 4 | `step4_long_term` | `references/long-term-goals.md` | **Full subgroup allocation** (equity/debt/others with all subgroups). Allocates goals >60m + leftover corpus as wealth creation. Flags shortfall. Placeholder — allocation logic TBD. |
| 5 | `step5_aggregation` | `references/aggregation.md` | Consolidates all 4 buckets into subgroup × investment_type matrix. |
| 6 | `step6_guardrails` | `references/guardrails.md` | Validates Step 4 long-term subgroup allocation against guardrail rules + corrects violations. Also maps each `asset_subgroup` → actual mutual fund using `mf_subgroup_mapped.csv`. Output at `sub_category` level for customer-facing display. |
| 7 | `step7_presentation` | `references/presentation.md` | Final client-facing output: goals by bucket, shortfall warnings, aggregated subgroup matrix, fund recommendations at subcategory level. |

### State Flow

Each step receives the full accumulated state. Steps 1–4 each output:
- Their bucket's subgroup-level allocations
- `remaining_corpus` after this bucket is funded
- `shortfall` flag + amount if corpus is insufficient for this bucket

### Shortfall Logic (per step)

At the end of each allocation step:
```
if sum(goal.amount_needed for goals in this bucket) > remaining_corpus_entering_this_step:
    flag shortfall = sum(goals) - remaining_corpus
    allocate as much as available
    pass remaining_corpus = 0 to next step
else:
    allocate fully
    pass remaining_corpus = remaining_corpus - sum(goals)
```

Priority (negotiable vs non-negotiable) is surfaced in the shortfall message but does not change allocation logic in this version. The system informs the client to either increase investments or reduce negotiable goals.

---

## 4. Subgroup Allocation Per Step

Subgroup complexity increases with investment horizon:

| Step | Subgroup Detail |
|------|----------------|
| Step 1 — Emergency | Always `debt_subgroup` |
| Step 2 — Short-term | Tax-rate driven: `debt_subgroup` if `effective_tax_rate < 20%`, else `arbitrage_income` |
| Step 3 — Medium-term | Risk + horizon table → equity: `multi_asset`, debt: `arbitrage_plus_income` or `pure_debt` |
| Step 4 — Long-term | **Full subgroup breakdown** across all equity, debt, and others subgroups |

### Step 2 — Short-term Tax Logic (< 24 months)

Arbitrage funds are taxed as equity (STCG at 15–20%), making them more tax-efficient than debt funds (taxed at slab rate) for investors in higher tax brackets.

```
if effective_tax_rate < 20%:
    → allocate 100% of short-term goal amounts to debt_subgroup
else:
    → allocate 100% of short-term goal amounts to arbitrage_income
```

This rule applies uniformly across all goals in the short-term bucket regardless of individual goal timeline within the <24m range.

---

### Step 3 — Medium-term Logic (24–60 months)

Each goal in the medium-term bucket is allocated independently using its own `time_to_goal_months`.

#### Step A — Determine Risk Bucket

| Risk Bucket | Condition |
|-------------|-----------|
| Low | `effective_risk_score < 4` |
| Medium | `4 <= effective_risk_score <= 7` |
| High | `effective_risk_score > 7` |

#### Step B — Equity / Debt Split Lookup

Use `floor(time_to_goal_months / 12)` rounded to the nearest year (3, 4, or 5). Goals with horizon < 36 months (3 years) use the 3-year row. Others = always 0%.

| Horizon | Low Risk | Medium Risk | High Risk |
|---------|----------|-------------|-----------|
| 5 years | 50% E / 50% D | 70% E / 30% D | 80% E / 20% D |
| 4 years | 35% E / 65% D | 50% E / 50% D | 65% E / 35% D |
| 3 years (and 2 years) | 0% E / 100% D | 0% E / 100% D | 0% E / 100% D |

> Goals with `time_to_goal_months` between 24–35 months use the 3-year row (0% equity, 100% debt). No interpolation needed.

#### Step C — Equity Instrument

For the equity portion (when > 0%), always recommend a **Multi-Asset fund** (`multi_asset` subgroup). Do not break equity into further subgroups for medium-term goals.

#### Step D — Debt Instrument Preference

```
if effective_tax_rate < 20%:
    debt_instrument_preference = "arbitrage_plus_income"
else:
    debt_instrument_preference = "pure_debt"
```

This flag is passed through to the subgroup allocation so the correct debt instruments are recommended. The flag applies to all medium-term goals for the client (not per-goal).

---

**Full subgroup taxonomy:**

Equity (long-term only): `tax_efficient_equities`, `low_beta_equities`, `value_equities`, `dividend_equities`, `medium_beta_equities`, `high_beta_equities`, `sector_equities`, `us_equities`

Debt (long-term only): `debt_subgroup`, `short_debt`, `medium_debt`, `long_duration_debt`, `floating_debt`, `high_risk_debt`

Others (long-term only): `gold_commodities`

Hybrid — new for this module:
- `arbitrage_income` — short-term, when `effective_tax_rate >= 20%`
- `multi_asset` — medium-term equity portion
- `arbitrage_plus_income` — medium-term debt portion when `effective_tax_rate < 20%`
- `pure_debt` — medium-term debt portion when `effective_tax_rate >= 20%`

---

## 5. Aggregation Step (Step 5)

Produces a subgroup × investment_type matrix:

| Subgroup | Emergency | Short-term | Medium-term | Long-term | Total |
|---|---|---|---|---|---|
| debt_subgroup | ₹X | ₹0 | ₹0 | ₹0 | ₹X |
| short_debt | ₹0 | ₹X | ₹0 | ₹0 | ₹X |
| medium_beta_equities | ₹0 | ₹0 | ₹X | ₹X | ₹X |
| ... | | | | | |

Grand total across all cells must equal `total_corpus`.

---

## 6. Output Model Changes

New models added (existing models retained):

```python
class BucketShortfall(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    shortfall_amount: float
    message: str  # plain language, surfaces negotiable goals as candidates to cut

class SubgroupFundMapping(BaseModel):
    asset_subgroup: str
    sub_category: str          # from mf_subgroup_mapped.csv sub_category column
    recommended_fund: str      # fund name from mf_subgroup_mapped.csv
    isin: str                  # isinGrowth from mf_subgroup_mapped.csv
    amount: float

class BucketAllocation(BaseModel):
    bucket: Literal["emergency", "short_term", "medium_term", "long_term"]
    goals: List[Goal]              # goals assigned to this bucket
    total_goal_amount: float
    allocated_amount: float
    shortfall: Optional[BucketShortfall]
    subgroup_amounts: dict         # subgroup -> amount

class AggregatedSubgroupRow(BaseModel):
    subgroup: str
    sub_category: str          # subcategory level for customer display
    emergency: float
    short_term: float
    medium_term: float
    long_term: float
    total: float
    fund_mapping: Optional[SubgroupFundMapping]  # populated for long-term subgroups

class GoalAllocationOutput(BaseModel):
    client_summary: ClientSummary  # updated: no investment_horizon/investment_goal; shows goals list
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    shortfall_summary: List[BucketShortfall]   # empty if no shortfalls
    grand_total: float
    all_amounts_in_multiples_of_100: bool
```

### Fund Mapping (Step 6)

Step 6 uses `mf_subgroup_mapped.csv` to map each `asset_subgroup` to a recommended fund. The output to the customer is at **`sub_category` level** (e.g. "Large Cap Fund", "Liquid Fund", "Money Market Fund"), not at the individual scheme level. The recommended fund and ISIN are included for reference but the primary display field is `sub_category`.

---

## 7. Reference Files

### New files (to be created in `references/`):
- `short-term-goals.md` — allocation logic for <24m goals (based on 1b Short Term Funds logic from `Ideal_asset_allocation/references/carve-outs.md`)
- `medium-term-goals.md` — allocation logic for 24–60m goals; instruments chosen based on `effective_tax_rate` + risk profile
- `long-term-goals.md` — full subgroup allocation for >60m goals; placeholder for allocation logic (TBD), structure defined
- `aggregation.md` — consolidation rules for step 5 (subgroup × investment_type matrix)

### Updated files (written fresh for `goal_based_allocation`):
- `carve-outs.md` — emergency fund (1a) + negative NFA (1c) only; 1b Short Term Funds section removed (moved to `short-term-goals.md`)
- `guardrails.md` — updated to validate Step 4 long-term subgroup allocation only; adds fund mapping section using `mf_subgroup_mapped.csv` with output at `sub_category` level
- `presentation.md` — restructured for time-based bucket format: goals per bucket, shortfall warnings, aggregated subgroup × investment_type matrix, fund recommendations at `sub_category` level

### Files carried over unchanged:
- `scheme_classification.md`
- `subgroup-allocation.md`
- `asset-class-allocation.md`
- `mf_subgroup_mapped.csv`

---

## 8. Files To Create

```
src/goal_based_allocation/
├── __init__.py
├── main.py            # 7-step LangChain LCEL chain
├── models.py          # updated AllocationInput + new output models
├── prompts.py         # 7 prompt templates + state slimmers
├── references/
│   ├── carve-outs.md              (updated — emergency + NFA only)
│   ├── short-term-goals.md        (new)
│   ├── medium-term-goals.md       (new)
│   ├── long-term-goals.md         (new, placeholder)
│   ├── aggregation.md             (new)
│   ├── guardrails.md              (carried over)
│   ├── presentation.md            (carried over)
│   ├── scheme_classification.md   (carried over)
│   ├── subgroup-allocation.md     (carried over)
│   └── asset-class-allocation.md  (carried over)
├── Testing/
│   └── dev_run_samples.py
└── docs/
    └── superpowers/specs/
        └── 2026-04-11-time-based-allocation-design.md
```

---

## 9. Out of Scope (This Version)

- Long-term goal allocation logic (placeholder only — structure defined, rules TBD)
- Partial funding of negotiable goals (shortfall is flagged, not auto-resolved)
- Python pre-processing for shortfall detection (Approach B — deferred to future)
- Individual fund selection within a subcategory (output is at `sub_category` level, one recommended fund per subgroup)
