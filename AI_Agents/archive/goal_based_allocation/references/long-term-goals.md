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
  `low_beta_equities`, `value_equities`, `medium_beta_equities`,
  `high_beta_equities`, `sector_equities`, `us_equities` (all default to 5)
- `multi_asset_composition` — internal breakdown of the multi-asset fund:
  `equity_pct`, `debt_pct`, `others_pct` (must sum to 100)

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

Look up `effective_risk_score` in the table below. For scores not listed, use the nearest row (round up to next 0.5). For example, score 7.19 → use row 7.5.

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

### Others Allocation Caveat (Risk Score ≥ 8)

When `effective_risk_score >= 8`, allocate to `others` **only if** `market_commentary.others > 6`.
If `market_commentary.others <= 6`, set Others Min = 0 and Others Max = 0, then redistribute
that portion proportionally between equities and debt based on their current raw targets.


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

This maps view_score=10 → Max, view_score=5 → midpoint, view_score=1 → slightly above Min.
The Min bound is only reachable after the normalization step applies clamping.

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

## Phase 4 — Multi-Asset Fund Allocation

**Runs on:** `residual_equity_corpus` (from Phase 3) and `debt_amount` (from Phase 2).

The multi-asset fund's internal composition is given as input (`multi_asset_composition`):
- `equity_pct` — % of the fund in equity
- `debt_pct`   — % of the fund in debt
- `others_pct` — % of the fund in others

### Step 1 — Compute maximum allocatable amount

```
max_x_from_equity  = (0.50 × residual_equity_corpus) / (equity_pct / 100)
max_x_from_debt    = debt_amount / (debt_pct / 100)
multi_asset_amount = min(max_x_from_equity, max_x_from_debt)  (round to nearest 100)
```

Constraints enforced:
- Equity component of the fund ≤ 50% of `residual_equity_corpus`
- Debt component of the fund ≤ 100% of `debt_amount`

### Step 2 — Decompose into asset class components

```
equity_component  = multi_asset_amount × equity_pct / 100
debt_component    = multi_asset_amount × debt_pct / 100
others_component  = multi_asset_amount × others_pct / 100
```

### Step 3 — Compute residuals for Phase 5

```
equity_for_subgroups      = residual_equity_corpus − equity_component
debt_for_subgroups        = debt_amount − debt_component
remaining_others_for_gold = others_amount − others_component
```

**If `remaining_others_for_gold` is negative** (the multi_asset fund's others_component exceeded others_amount), set `remaining_others_for_gold = 0`. Do not adjust `multi_asset_amount`.
If `remaining_others_for_gold = 0`, set `gold_commodities = 0` in Phase 5 — do not allocate anything to gold.

The `others_component` is counted against `others_amount`.
`remaining_others_for_gold` flows to the `gold_commodities` subgroup in Phase 5.

---

## Phase 5 — Subgroup Allocation

### Equity Subgroups

**Pool:** `total_equity_for_subgroups = equity_for_subgroups` (from Phase 4)

Allocate across 6 equity subgroups using the guardrail table. Percentages are
**% of `total_equity_for_subgroups`** (not % of total corpus).

Use the same ceiling-lookup rule as Phase 1: round `effective_risk_score` up to the nearest 0.5.

| Score | us_equities | low_beta_equities | medium_beta_equities | high_beta_equities | sector_equities | value_equities |
|-------|-------------|-------------------|----------------------|--------------------|-----------------|----------------|
| 10.0  | 20–40       | 0–20              | 30–50                | 10–30              | 0–20            | 0–30           |
| 9.5   | 20–40       | 0–20              | 30–50                | 10–30              | 0–20            | 0–30           |
| 9.0   | 20–40       | 0–25              | 25–50                | 10–30              | 0–20            | 0–30           |
| 8.5   | 20–40       | 0–30              | 25–45                | 10–30              | 0–20            | 0–30           |
| 8.0   | 20–40       | 10–30             | 20–40                | 5–25               | 0–20            | 0–30           |
| 7.5   | 20–40       | 15–35             | 20–40                | 5–25               | 0–20            | 0–30           |
| 7.0   | 20–40       | 20–40             | 20–40                | 5–25               | 0–20            | 0–30           |
| 6.5   | 20–40       | 25–45             | 20–40                | 5–20               | 0–20            | 0–30           |
| 6.0   | 20–40       | 30–50             | 15–35                | 5–20               | 0–20            | 0–30           |
| 5.5   | 20–40       | 30–55             | 10–35                | 5–20               | 0–20            | 0–30           |
| 5.0   | 20–40       | 35–55             | 10–30                | 0–20               | 0               | 0–30           |
| 4.5   | 20–40       | 40–60             | 5–25                 | 0–20               | 0               | 0–30           |
| 4.0   | 20–40       | 45–65             | 5–25                 | 0                  | 0               | 0–30           |
| 3.5   | 20–40       | 50–70             | 0–20                 | 0                  | 0               | 0–30           |
| 3.0   | 20–40       | 55–75             | 0–20                 | 0                  | 0               | 0–30           |
| 2.5   | 20–40       | 60–80             | 0–20                 | 0                  | 0               | 0–30           |
| 2.0   | 20–40       | 60–80             | 0                    | 0                  | 0               | 0–30           |
| 1.5   | 20–40       | 60–80             | 0                    | 0                  | 0               | 0–30           |
| 1.0   | 20–40       | 60–80             | 0                    | 0                  | 0               | 0–30           |

**Conditional subgroups — strict gate, evaluated before any allocation math:**

- `value_equities`: **ONLY allocate if `market_commentary.value_equities > 7`.**
  If `market_commentary.value_equities <= 7` (including the default of 5.0): set amount = 0, exclude entirely from subgroup math.
- `sector_equities`: **ONLY allocate if `market_commentary.sector_equities > 7`.**
  If `market_commentary.sector_equities <= 7` (including the default of 5.0): set amount = 0, exclude entirely from subgroup math.

**These are hard gates — not soft preferences.** A score of 5.0 (the default) means the subgroup is excluded. Do not allocate any amount to these subgroups unless the condition is explicitly met.

If either subgroup is excluded, redistribute its portion proportionally to the remaining active subgroups.

Apply proportional scaling per subgroup using `market_commentary.<subgroup>` (same formula as Phase 2).
After computing raw targets:
1. Before clamping, check feasibility: if the sum of all active subgroup Max values is less than
   100, scale all Max values proportionally upward so their sum equals exactly 100.
2. **MANDATORY — Normalize so the active subgroup percentages sum to exactly 100%** of `total_equity_for_subgroups`.
   Do not skip this step. Picking values within their Min/Max ranges is NOT enough — they must be rescaled so that they sum to 100. If raw targets sum to S, multiply each by `100/S`.
3. Clamp any value that breaches its Min or Max; redistribute the excess/deficit proportionally among the unclamped active subgroups.
4. Round each percentage to nearest integer.
5. Drop any subgroup whose final allocation rounds to < 2% of `total_equity_for_subgroups`
   (set to 0), redistribute proportionally to remaining subgroups. Repeat the drop check once.
6. If subgroup percentages do not sum to 100 after rounding, adjust the largest by ±1 until they do.

Convert subgroup percentages to amounts:
```
subgroup_amount = total_equity_for_subgroups × subgroup_pct / 100
```
Then round each `subgroup_amount` to nearest 100.

**MANDATORY final amount check:**
Let `S = sum of all 6 equity subgroup_amounts`.
- `S` **must equal** `total_equity_for_subgroups` exactly.
- If `S ≠ total_equity_for_subgroups` (due to rounding), add the difference (`total_equity_for_subgroups − S`) to the subgroup with the largest amount.
- All subgroup amounts in the final output must be **non-negative integer multiples of 100**.
- Do not emit a subgroup value that is not a multiple of 100. If a value like 361434 results from math, round it to 361400 and redistribute the 34 difference into the largest subgroup.

### Debt

**Pool:** `debt_for_subgroups` (from Phase 4). Allocate the full amount into **exactly one** subgroup based on tax slab:

```
if effective_tax_rate < 20:
    asset_subgroup = "debt_subgroup"   ← use this key exactly
else:
    asset_subgroup = "arbitrage_income" ← use this key exactly
```

**This is a strict binary rule — no exceptions, no blending between the two.**
- `effective_tax_rate = 20` → use `arbitrage_income` (the condition is strictly `< 20`)
- Never split `debt_for_subgroups` across both `debt_subgroup` and `arbitrage_income`
- The unchosen key must be set to 0 in `subgroup_amounts`

**Rationale:** Arbitrage funds are taxed as equity (STCG ~15–20%), making them more tax-efficient than debt funds (taxed at slab rate) for investors in higher brackets. For low-tax clients, plain debt is simpler and equally effective.

### Others

`remaining_others_for_gold` (from Phase 4) → `gold_commodities` key.

---

## Invariants (MUST verify before outputting — re-compute and fix if any fails)

- `equities_pct + debt_pct + others_pct = 100`
- `sum(6 equity subgroup amounts) = equity_for_subgroups` (exactly, no tolerance — adjust largest subgroup by the rounding residual)
- `sum(6 equity subgroup amounts) + elss_amount + equity_component = equities_amount` (within ±500 rounding tolerance)
- `debt_component + debt_for_subgroups = debt_amount` (within ±500 rounding tolerance)
- `others_component + gold_commodities = others_amount` (within ±500 rounding tolerance)
- **`sum(all subgroup_amounts in output) = total_allocated`** (exactly — this is the critical check; if off, adjust the largest subgroup)
- **All subgroup amounts are non-negative integer multiples of 100** — no exceptions

If any invariant fails, fix the allocation before returning. Do not return output that violates these.

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
  "multi_asset": {
    "multi_asset_amount": <number>,
    "equity_component": <number>,
    "debt_component": <number>,
    "others_component": <number>,
    "equity_for_subgroups": <number>,
    "debt_for_subgroups": <number>,
    "remaining_others_for_gold": <number>
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
      "multi_asset": <number>,
      "us_equities": <number>,
      "low_beta_equities": <number>,
      "medium_beta_equities": <number>,
      "high_beta_equities": <number>,
      "sector_equities": <number>,
      "value_equities": <number>,
      "debt_subgroup": <number>,       // non-zero only if effective_tax_rate < 20; else 0
      "arbitrage_income": <number>,    // non-zero only if effective_tax_rate >= 20; else 0
      "gold_commodities": <number>
    }
  }
}
```
