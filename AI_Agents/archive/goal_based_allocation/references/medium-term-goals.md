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
- If `horizon_years < 3` (i.e. 24–35 months): use 3-year row.
- If `horizon_years > 5`: use 5-year row (should not happen given bucket bounds).
- Others = always 0%.

| Horizon | Low Risk | Medium Risk | High Risk |
|---------|----------|-------------|-----------|
| 5 years | 50% E / 50% D | 70% E / 30% D | 80% E / 20% D |
| 4 years | 35% E / 65% D | 50% E / 50% D | 65% E / 35% D |
| 3 years | 0% E / 100% D | 0% E / 100% D | 0% E / 100% D |

---

## Step C — Equity asset_subgroup

For the equity portion (when > 0%), always use `multi_asset`.

---

## Step D — Debt Instrument Preference (applies to ALL medium-term goals)

```
if effective_tax_rate < 20:
    asset_subgroup = "arbitrage_plus_income"
else:
    asset_subgroup = "debt_subgroup"
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
    "asset_subgroup": "<arbitrage_plus_income | debt_subgroup>",
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
      "<arbitrage_plus_income | debt_subgroup>": <number>
    }
  }
}
```
