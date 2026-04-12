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
`debt_subgroup`, `short_debt`, `arbitrage_income`, `arbitrage_plus_income`,
`tax_efficient_equities`, `multi_asset`,
`low_beta_equities`, `medium_beta_equities`, `high_beta_equities`,
`value_equities`, `sector_equities`, `us_equities`,
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
