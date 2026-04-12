# Step 3 — Subgroup Level Allocation

Within each asset class total from Step 2, break down into detailed subgroups. This step runs in two sequential phases:

1. **Phase A — ELSS First-Pass** (lock tax saving equity before anything else)
2. **Phase B — Equity Subgroup Allocation** (allocate residual equity across standard subgroups)

---

## Inputs Required

- `effective_risk_score` (from risk-profiling)
- Asset class totals: Equities %, Debt %, Others % (from Step 2)
- `tax_regime`, `section_80c_utilized`, `annual_income` (from client intake)
- `remaining_investable_corpus` (from Step 1)
- Market commentary subgroup-level view scores (below)

---

## Phase A — ELSS First-Pass (tax_efficient_equities)

Run this **before** any other subgroup allocation.

### Condition
If `tax_regime = old` AND `section_80c_utilized < 150000`:

1. Compute `elss_headroom = 150000 − section_80c_utilized`
2. Compute `equity_corpus = remaining_investable_corpus × (equities_pct / 100)`
3. Set `elss_amount = min(elss_headroom, equity_corpus)`
4. Assign `elss_amount` entirely to the **`tax_efficient_equities`** subgroup (ELSS Tax Saver Fund).
5. Compute `residual_equity_corpus = equity_corpus − elss_amount`
6. All Phase C equity subgroup allocation runs on `residual_equity_corpus` (plus any hybrid equity component from Phase B).

### If condition is NOT met
- `elss_amount = 0`, `tax_efficient_equities = 0`
- `residual_equity_corpus = equity_corpus`
- Proceed directly to Phase B (equity subgroup allocation).

### Key rule
`tax_efficient_equities` counts fully toward the equity total. The ELSS amount is part of equities, not separate from it.

---

## Phase B — Equity Subgroup Allocation

Allocate equity across the 7 standard equity subgroups using the min/max guardrail table and market commentary overlay below.

### Equity Pool for Phase B
The total equity corpus available for subgroup allocation:
```
base_equity_corpus = remaining_investable_corpus × (equities_pct / 100)
residual_equity_corpus = base_equity_corpus − elss_amount (from Phase A)
total_equity_for_subgroups = residual_equity_corpus
```

`total_equity_for_subgroups` amount is distributed across the 7 standard equity subgroups. All percentages in Phase B are expressed as a **% of the equity allocation**, applied against `total_equity_for_subgroups`.

### Equity Subgroup Guardrail Table

Each cell shows Min%–Max% of the total corpus allocation at the given risk score.

| Score | low_beta_equities | value_equities | dividend_equities | medium_beta_equities | high_beta_equities | sector_equities | us_equities |
|-------|-------------------|----------------|-------------------|----------------------|--------------------|-----------------|-------------|
| 10    | 10–20             | 0–25           | 0–20              | 10–25                | 10–25              | 0–10            | 10–25       |
| 9     | 10–20             | 5–25           | 0–20              | 10–25                | 10–25              | 0–10            | 10–25       |
| 8     | 10–20             | 5–25           | 0–20              | 5–25                 | 5–20               | 0–10            | 10–20       |
| 7     | 5–20              | 2–20           | 0–25              | 5–20                 | 5–20               | 0–10            | 10–20       |
| 6     | 5–20              | 2–20           | 0–25              | 5–20                 | 2–15               | 0–10            | 5–20        |
| 5     | 2–20              | 2–20           | 0–20              | 5–15                 | 0–15               | 0–10            | 5–15        |
| 4     | 2–15              | 2–15           | 0–20              | 0–15                 | 0–10               | 0–5             | 5–15        |
| 3     | 2–15              | 0–15           | 0–20              | 0–15                 | 0–10               | 0–5             | 0–10        |
| 2     | 0–10              | 0–10           | 0–20              | 0–15                 | 0–5                | 0–5             | 0–5         |
| 1     | 0–10              | 0–10           | 0–15              | 0–15                 | 0–5                | 0–0             | 0–5         |

### Equity Subgroup Market Commentary View Scores

| Subgroup              | View Score |
|-----------------------|------------|
| low_beta_equities     | 5          |
| value_equities        | 5          |
| dividend_equities     | 5          |
| medium_beta_equities  | 5          |
| high_beta_equities    | 5          |
| sector_equities       | 5          |
| us_equities           | 5          |

### Proportional Scaling Formula

For each equity subgroup fo this:
```
midpoint = (Min + Max) / 2
range_half = (Max - Min) / 2
normalized_view = (view_score - 5) / 5
target_allocation = midpoint + normalized_view * range_half
```

After scaling, ensure sum of the 7 standard subgroup allocation percentage equals `equities_pct`. Adjust proportionally within subgroup min/max bounds if needed.

### Final equity subgroup totals (as % of total corpus):
```
tax_efficient_equities_pct = elss_amount / remaining_investable_corpus × 100
each_standard_subgroup_pct = (subgroup amount for each subgroup) / remaining_investable_corpus × 100
sum of all equity subgroups = `equities_pct` (from Step 2) ✓
```

---

## Debt Subgroups
The sum of all debt subgroup allocations must equal the `debt_pct` total from Step 2.

### Debt Subgroup Guardrail Table
Each cell shows Min%–Max% of the total corpus allocation at the given risk score.

| Score | high_risk_debt | long_duration_debt | floating_debt | medium_debt |
|-------|----------------|--------------------|---------------|-------------|
| 10    | 0–10           | 5–10               | 0–10          | 0–20        |
| 9     | 0–10           | 5–10               | 5–10          | 0–20        |
| 8     | 0–10           | 5–15               | 5–15          | 5–20        |
| 7     | 0–10           | 5–20               | 10–25         | 5–25        |
| 6     | 0–10           | 5–20               | 10–25         | 10–25       |
| 5     | 0–10           | 5–20               | 10–30         | 10–30       |
| 4     | 0–10           | 5–20               | 15–30         | 10–30       |
| 3     | 0–10           | 5–20               | 15–35         | 10–30       |
| 2     | 0–5            | 5–20               | 20–45         | 10–35       |
| 1     | 0–5            | 5–20               | 20–50         | 10–35       |

### Debt Subgroup Market Commentary View Scores

| Subgroup              | View Score |
|-----------------------|------------|
| high_risk_debt        | 5          |
| long_duration_debt    | 5          |
| floating_debt         | 5          |
| medium_debt           | 5          |

Apply the same proportional scaling formula as debt subgroups.
### Proportional Scaling Formula

For each equity subgroup fo this:
```
midpoint = (Min + Max) / 2
range_half = (Max - Min) / 2
normalized_view = (view_score - 5) / 5
target_allocation = midpoint + normalized_view * range_half
```

After scaling, ensure sum of the 4 debt_subgroup allocation percentage equals `debt_pct`. Adjust proportionally within subgroup min/max bounds if needed.

---

## Others Subgroups

### gold_commodities

Already covered at the asset class level in Step 2. The Others % from Step 2 maps directly to `gold_commodities` unless silver or other categories are relevant.


## Output of Step 3

**Rounding Rule:** All subgroup allocation percentages must be rounded to the nearest whole number (integer). After rounding, verify that each asset class's subgroups still sum to their parent asset class total. If not, adjust the largest subgroup within that class by 1% to reconcile.

**Minimum Allocation Threshold:** If any subgroup's final allocation is less than 1% (after rounding), drop that subgroup entirely (set it to 0%) and redistribute its allocation proportionally among the remaining subgroups within the same asset class. Do not recommend any subgroup with less than 1% allocation.

Full detailed allocation across all subgroups, in percentage:
- tax_efficient_equities_per: X% (ELSS — fixed from Phase A)
- low_beta_equities_per: X%
- value_equities_per: X%
- dividend_equities_per: X%
- medium_beta_equities_per: X%
- high_beta_equities_per: X%
- sector_equities_per: X%
- us_equities_per: X%
- high_risk_debt_per: X%
- long_duration_debt_per: X%
- floating_debt_per: X%
- medium_debt_per: X%
- gold_commodities_per: X%

Full detailed allocation across all subgroups in amount, use the above percenrtages to come to the absolute amounts. Round of amounts to nearest 100. 

- tax_efficient_equities_amount: Y (ELSS — fixed from Phase A)
- low_beta_equities_amount: Y
- value_equities_amount: Y
- dividend_equities_amount: Y
- medium_beta_equities_amount: Y
- high_beta_equities_amount: Y
- sector_equities_amount: Y
- us_equities_amount: Y
- high_risk_debt_amount: Y
- long_duration_debt_amount: Y
- floating_debt_amount: Y
- medium_debt_amount: Y
- gold_commodities_amount: Y
These feed into Step 4 for guardrail validation.

### JSON Output

**IMPORTANT — JSON Completeness Rule:** The JSON output must ALWAYS include every field shown in the schema below, in the same structure, every time. If a field is not applicable (e.g., ELSS not applicable, hybrid funds not used), set numeric values to `0` and boolean values to `false`. Never omit fields. This ensures a consistent, predictable JSON format regardless of the client's situation.

Store the following JSON object after completing Step 3.

```json
{
  "step": 3,
  "step_name": "subgroup_allocation",
  "inputs": {
    "effective_risk_score": <number>,
    "equities_pct": <number>,
    "debt_pct": <number>,
    "others_pct": <number>,
    "remaining_investable_corpus": <number>,
    "tax_regime": "<string: 'old' | 'new'>",
    "section_80c_utilized": <number>,
    "annual_income": <number>
  },
  "phase_a_elss": {
    "elss_applicable": <boolean>,
    "elss_headroom": <number | null>,
    "equity_corpus": <number>,
    "elss_amount": <number>,
    "residual_equity_corpus": <number>
  },
  "phase_b_equity_subgroups": {
    "total_equity_for_subgroups": <number>,
    "note": "residual_equity_corpus"
  },
  "calculations": {
    "equity_subgroups": {
      "tax_efficient_equities": {"amount": <number>, "note": "fixed from Phase A"},
      "low_beta_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "value_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "dividend_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "medium_beta_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "high_beta_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "sector_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "us_equities": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>}
    },
    "debt_subgroups": {
      "high_risk_debt": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "long_duration_debt": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "floating_debt": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>},
      "medium_debt": {"min": <number>, "max": <number>, "view_score": <number>, "raw_target": <number>, "scaled_target": <number>}
    },
    "others_subgroups": {
      "gold_commodities": {"min": <number>, "max": <number>, "raw_target": <number>, "scaled_target": <number>}
    },
    "equity_subgroup_sum_check": <number>,
    "debt_subgroup_sum_check": <number>
  },
  "output": {
    "tax_efficient_equities_pct": <number>,
    "tax_efficient_equities_amount": <number>,
    "low_beta_equities_pct": <number>,
    "low_beta_equities_amount": <number>,
    "value_equities_pct": <number>,
    "value_equities_amount": <number>,
    "dividend_equities_pct": <number>,
    "dividend_equities_amount": <number>,
    "medium_beta_equities_pct": <number>,
    "medium_beta_equities_amount": <number>,
    "high_beta_equities_pct": <number>,
    "high_beta_equities_amount": <number>,
    "sector_equities_pct": <number>,
    "sector_equities_amount": <number>,
    "us_equities_pct": <number>,
    "us_equities_amount": <number>,
    "high_risk_debt_pct": <number>,
    "high_risk_debt_amount": <number>,
    "long_duration_debt_pct": <number>,
    "long_duration_debt_amount": <number>,
    "floating_debt_pct": <number>,
    "floating_debt_amount": <number>,
    "medium_debt_pct": <number>,
    "medium_debt_amount": <number>,
    "gold_commodities_pct": <number>,
    "gold_commodities_amount": <number>
  }
}
```
