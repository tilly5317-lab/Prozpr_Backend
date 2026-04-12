---
age_brackets:
  "<30":
    conservative: {equity_min: 30, equity_max: 50, debt_min: 35, debt_max: 55, gold_min: 0, gold_max: 20}
    moderate:     {equity_min: 50, equity_max: 70, debt_min: 20, debt_max: 35, gold_min:  0, gold_max: 15}
    aggressive:   {equity_min: 70, equity_max: 85, debt_min: 10, debt_max: 20, gold_min:  0, gold_max: 10}
  "30-45":
    conservative: {equity_min: 25, equity_max: 45, debt_min: 40, debt_max: 60, gold_min: 0, gold_max: 20}
    moderate:     {equity_min: 40, equity_max: 60, debt_min: 25, debt_max: 40, gold_min:  0, gold_max: 15}
    aggressive:   {equity_min: 60, equity_max: 75, debt_min: 15, debt_max: 25, gold_min:  0, gold_max: 15}
  "46-60":
    conservative: {equity_min: 15, equity_max: 30, debt_min: 50, debt_max: 70, gold_min: 0, gold_max: 20}
    moderate:     {equity_min: 30, equity_max: 50, debt_min: 35, debt_max: 50, gold_min: 0, gold_max: 20}
    aggressive:   {equity_min: 45, equity_max: 60, debt_min: 25, debt_max: 35, gold_min: 0, gold_max: 15}
  ">60":
    conservative: {equity_min: 10, equity_max: 20, debt_min: 60, debt_max: 80, gold_min: 0, gold_max: 20}
    moderate:     {equity_min: 20, equity_max: 35, debt_min: 45, debt_max: 60, gold_min: 0, gold_max: 20}
    aggressive:   {equity_min: 30, equity_max: 45, debt_min: 35, debt_max: 50, gold_min: 0, gold_max: 20}

equity_splits:
  large_cap: {min_factor: 0.50, max_factor: 0.70}
  mid_cap:   {min_factor: 0.20, max_factor: 0.35}
  small_cap: {min_factor: 0.05, max_factor: 0.20}

small_cap_risk_caps:
  conservative: 0.10
  moderate:     0.15
  aggressive:   0.20

validation:
  sum_min: 99
  sum_max: 101
---

# Prozper Guardrail Rules

Edit the YAML front matter above to change any rule. No Python code changes needed.

## Rule Matrix

Defines total equity, debt, and gold ranges by age bracket and risk profile.

| Age     | Risk         | Equity Min | Equity Max | Debt Min | Debt Max | Gold Min | Gold Max |
|---------|--------------|------------|------------|----------|----------|----------|----------|
| <30     | conservative | 30         | 50         | 35       | 55       | 0       | 20       |
| <30     | moderate     | 50         | 70         | 20       | 35       | 0        | 15       |
| <30     | aggressive   | 70         | 85         | 10       | 20       | 0        | 10       |
| 30-45   | conservative | 25         | 45         | 40       | 60       | 0       | 20       |
| 30-45   | moderate     | 40         | 60         | 25       | 40       | 0        | 15       |
| 30-45   | aggressive   | 60         | 75         | 15       | 25       | 0        | 15       |
| 46-60   | conservative | 15         | 30         | 50       | 70       | 0       | 20       |
| 46-60   | moderate     | 30         | 50         | 35       | 50       | 0       | 20       |
| 46-60   | aggressive   | 45         | 60         | 25       | 35       | 0       | 15       |
| >60     | conservative | 10         | 20         | 60       | 80       | 0       | 20       |
| >60     | moderate     | 20         | 35         | 45       | 60       | 0       | 20       |
| >60     | aggressive   | 30         | 45         | 35       | 50       | 0       | 20       |

## Equity Sub-Asset Split Logic

Total equity is split into large_cap, mid_cap, and small_cap using the factors in `equity_splits`:

- **large_cap**: min = equity_min × 0.50, max = equity_max × 0.70
- **mid_cap**: min = equity_min × 0.20, max = equity_max × 0.35
- **small_cap**: min = equity_min × 0.05, max = equity_max × 0.20

## Small Cap Risk Caps

small_cap max is further capped based on risk profile (from `small_cap_risk_caps`):

- conservative: small_cap max = min(small_cap_max, equity_max × 0.10)
- moderate: small_cap max = min(small_cap_max, equity_max × 0.15)
- aggressive: small_cap max = min(small_cap_max, equity_max × 0.20)

All bounds are rounded to 1 decimal place.

## Validation Rules

An allocation is valid when:
1. Each of the 5 asset classes (large_cap, mid_cap, small_cap, debt, gold) is within its computed min/max bounds.
2. The sum of all 5 percentages is between `sum_min` (99) and `sum_max` (101) — allowing ±1% tolerance.
