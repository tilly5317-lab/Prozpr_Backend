# Step 2 — Asset Class Level Allocation

For the `remaining_investable_corpus` (after Step 1 carve-outs), determine target percentages for the three parent asset classes only:
- **Equities %**
- **Debt %**
- **Others %**

No subgroup detail in this step — that happens in Step 3.

---

## Inputs Required

- `effective_risk_score` (from risk-profiling)
- `investment_horizon` (short/medium/long or specific years)
- `investment_goal` (wealth creation, regular income, intergenerational transfer, etc.)
- `age` (for intergenerational transfer special case)
- Market commentary view scores (from the market commentary section below)

---

## Asset Class Min/Max Ranges by Risk Score

Single reference table for all three asset classes. Each row defines the Min% and Max% for Equities, Debt, and Others at that risk score.

| Score | Equities Min | Equities Max | Debt Min | Debt Max | Others Min | Others Max |
|-------|---------------|---------------|-----------|-----------|-------------|-------------|
| 10    | 50            | 90            | 5         | 30        | 5           | 10          |
| 9     | 50            | 85            | 10        | 40        | 5           | 10          |
| 8     | 45            | 75            | 20        | 50        | 5           | 15          |
| 7     | 35            | 65            | 25        | 60        | 5           | 15          |
| 6     | 30            | 60            | 30        | 65        | 5           | 15          |
| 5     | 25            | 55            | 30        | 70        | 5           | 15          |
| 4     | 20            | 50            | 35        | 75        | 5           | 15          |
| 3     | 15            | 45            | 35        | 80        | 5           | 15          |
| 2     | 5             | 35            | 40        | 90        | 5           | 10          |
| 1     | 5             | 25            | 40        | 100       | 5           | 10          |

---

## Investment Horizon Adjustments

### Long-term (>10 years)
- Use the age-based guardrails directly from the tables above.
- The effective_risk_score from risk-profiling drives the min/max ranges.

### Medium-term (2–10 years)
Apply the equity formula:
```
H = investment_horizon_years
H_min = 3, H_max = 10
Equity_min = 20%, Equity_max = 70%

Total_equities = Equity_min + (Equity_max - Equity_min) * (H - H_min) / (H_max - H_min)
```

**Constraints:**
- The allocation to equities must NOT exceed the maximum equity allocation allowed as per the applicable age of the client (from the equities total table above).
- The thresholds for subgroups of equities, debt, and other allocation can be similar to the guardrails for age groups 50–55 with risk tolerance of 5–6. The exact allocation will depend on the risk profile and return objectives.

### Short-term (<3 years)
Already handled in Step 1 carve-outs. No further allocation from the remaining corpus for short-term goals.

### Intergenerational Transfer (age >60)
When the investment horizon is "long term" for a client aged >60 years where the investment goal/purpose is for inter-generational wealth transfer:
- The risk tolerance increases to the age range of 45, i.e., risk_tolerance_score of approximately 8.
- Use the guardrails for score 8 instead of the age-derived score.

---

## Market Commentary Overlay (Asset Class Level)

Apply proportional scaling using asset-class-level view scores:

| Asset Class | View Score |
|-------------|------------|
| equities    | 5          |
| debt        | 5          |
| gold_commodities (others) | 5 |

### Proportional Scaling Formula

For each asset class:
```
midpoint = (Min + Max) / 2
range_half = (Max - Min) / 2

# view_score is 1–10, normalize to -1 to +1
normalized_view = (view_score - 5) / 5

target_allocation = midpoint + normalized_view * range_half
```

This maps:
- view_score = 1 → target = Min
- view_score = 5 → target = midpoint
- view_score = 10 → target = Max

### Normalize to 100%
After computing target allocations for all three asset classes:
1. Sum all targets.
2. Scale each proportionally so the total = 100%.
3. Verify that the scaled values still fall within their respective Min–Max bounds.
4. If any scaled value breaches a bound, clamp it and redistribute the excess/deficit proportionally among the remaining categories.

---

## Interpolation Rule

The risk score table uses only integer scores (1–10). If the effective_risk_score is a non-integer (e.g., 7.3), interpolate linearly between the two adjacent integer scores:
```
lower_score = floor(actual_score)   e.g., 7
upper_score = ceil(actual_score)    e.g., 8
t = actual_score - lower_score      e.g., 0.3

interpolated_min = min_at_lower + t * (min_at_upper - min_at_lower)
interpolated_max = max_at_lower + t * (max_at_upper - max_at_lower)
```

Apply this for each asset class (equities, debt, others). Round interpolated min/max to the nearest integer.

---

## Output of Step 2

Target percentages for:
- **Equities: X%**
- **Debt: Y%**
- **Others: Z%**

Where X + Y + Z = 100% (of the remaining investable corpus).

**Rounding Rule:** All allocation percentages in this step must be rounded to the nearest whole number (integer). After rounding, if the total does not equal 100%, adjust the largest allocation by 1% to reconcile. All intermediate calculations (midpoints, targets, normalized values) may use decimals, but the final output percentages must be whole numbers.

These feed into Step 3 for subgroup-level breakdown.

### JSON Output

**IMPORTANT — JSON Completeness Rule:** The JSON output must ALWAYS include every field shown in the schema below, in the same structure, every time. If a field is not applicable (e.g., no interpolation needed, no medium-term cap), set numeric values to `0` or `null` as indicated, and string values to `"none"`. Never omit fields. This ensures a consistent, predictable JSON format regardless of the client's situation.

Store the following JSON object after completing Step 2.

```json
{
  "step": 2,
  "step_name": "asset_class_allocation",
  "inputs": {
    "effective_risk_score": <number>,
    "investment_horizon": "<string>",
    "investment_horizon_years": <number | null>,
    "investment_goal": "<string>",
    "age": <number>,
    "remaining_investable_corpus": <number>
  },
  "calculations": {
    "interpolation_t": <number | null>,
    "interpolation_lower_score": <number | null>,
    "interpolation_upper_score": <number | null>,
    "equities_min": <number>,
    "equities_max": <number>,
    "debt_min": <number>,
    "debt_max": <number>,
    "others_min": <number>,
    "others_max": <number>,
    "horizon_adjustment_applied": "<string: 'none' | 'medium_term_formula' | 'intergenerational_override'>",
    "medium_term_equity_cap": <number | null>,
    "market_view_scores": {
      "equities": <number>,
      "debt": <number>,
      "others": <number>
    },
    "raw_targets_before_normalization": {
      "equities": <number>,
      "debt": <number>,
      "others": <number>
    },
    "normalized_targets": {
      "equities": <number>,
      "debt": <number>,
      "others": <number>
    },
    "savings_rate_adjustment_applied": <boolean>,
    "any_clamping_after_normalization": <boolean>
  },
  "output": {
    "equities_pct": <number>,
    "debt_pct": <number>,
    "others_pct": <number>,
    "equities_amount": <number>,
    "debt_amount": <number>,
    "others_amount": <number>
  }
}
```
