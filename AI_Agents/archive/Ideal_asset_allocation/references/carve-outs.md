# Step 1 — Carve-Outs

Ring-fence specific amounts from the total corpus before allocating the main portfolio.
Each carve-out is a **separate input from the consumer profile**.

---

## 1a. Emergency Fund

**Input required:** `monthly_household_expense`, `primary_income_from_portfolio`, `savings_rate` (from risk-profiling Step 1b)

### Base Emergency Fund
Allocate 3–6 months of `monthly_household_expense` into:
- Overnight Fund (asset_subgroup: near_debt), OR
- Liquid Fund (asset_subgroup: near_debt)

### Determining Months Within Range
- Low savings rate (< 1%) → upper end: 6 months
- High savings rate (> 20%) → lower end: 3 months
- Middle savings rate (1–20%) → proportionally between 3 and 6 months

### Additional Allocation for Portfolio-Dependent Income
If `primary_income_from_portfolio = yes` (the client's goal is regular income from the portfolio with no other significant source of income):
- Allocate an additional 9 months of `monthly_household_expense` into:
  - Ultra Short Fund (3–6 months) (asset_subgroup: near_debt), OR
  - Ultra Short to Short Term Fund (6–12 months) (asset_subgroup: short_debt), OR
  - Money Market Fund (asset_subgroup: short_debt)

### Rationale
1. To manage regular household expenses in case of any need.
2. To avoid the need to sell investments during exigencies and market downturns, which can lead to locking in losses and derailing long-term goals.

### Risk Tolerance for Emergency Funds
Risk tolerance is 1 — these funds must be in the safest, most liquid instruments.

---

## 1b. Short-Term Funds

**Input required:** `short_term_expenses` — a list of {amount, timeline_in_months} for known upcoming one-off expenses or goal-linked outflows.

### Allocation Rules by Timeline

**Expenses with timeline < 12 months:**
- 100% of the amount into:
  - Ultra Short Fund (3–6 months) (asset_subgroup: near_debt), OR
  - Ultra Short to Short Term Fund (6–12 months) (asset_subgroup: short_debt), OR
  - Money Market Fund (asset_subgroup: short_debt)

**Expenses with timeline 1–2 years:**
- At least 70% of the amount into:
  - Short Term Fund (1–2 years) (asset_subgroup: short_debt)
- Balance (up to 30%) allocation depends on other factors: age, occupation, ability & willingness to take risk, etc.

### Risk Tolerance for Short-Term Funds
Risk tolerance is 2–3 — to ensure near-term requirements are met without undue risk.

### Rationale
1. To ensure near-term requirements are met without taking undue risk.
2. To avoid the need to sell investments during market downturns, which can lead to locking in losses and derailing goals.

---

## 1c. Negative Net Financial Assets Carve-Out

**Input from risk-profiling Step 1c:** `net_financial_assets`

If `net_financial_assets < 0`:
- Ring-fence the absolute value of `net_financial_assets` into:
  - Ultra Short Fund (3–6 months) (asset_subgroup: near_debt), OR
  - Ultra Short to Short Term Fund (6–12 months) (asset_subgroup: short_debt), OR
  - Money Market Fund (asset_subgroup: short_debt)

---

## Output of Step 1

1. **Locked-in carve-out allocations** — specific amounts and fund types for each carve-out.
2. **Remaining investable corpus** = `total_corpus` minus all carve-out amounts.

This remaining corpus flows into Step 2 for asset class level allocation.

> **Note:** ELSS tax saving and tax-efficient debt allocation are handled in Step 3 (Subgroup Allocation), not here.

### JSON Output

**IMPORTANT — JSON Completeness Rule:** The JSON output must ALWAYS include every field shown in the schema below, in the same structure, every time. If a carve-out type is not applicable, set its amount to `0`. If `short_term_expenses` is empty, pass an empty array `[]`. If `additional_emergency_amount` or `negative_nfa_carveout` are not applicable, set them to `0`. Never omit fields.

Store the following JSON object after completing Step 1. All carve-out amounts must be rounded to the nearest multiple of 100.

```json
{
  "step": 1,
  "step_name": "carve_outs",
  "inputs": {
    "total_corpus": <number>,
    "monthly_household_expense": <number>,
    "primary_income_from_portfolio": <boolean>,
    "savings_rate": <number>,
    "short_term_expenses": [
      {"amount": <number>, "timeline_in_months": <number>}
    ],
    "net_financial_assets": <number>
  },
  "calculations": {
    "emergency_fund_months": <number>,
    "emergency_fund_amount": <number>,
    "additional_emergency_months": <number | null>,
    "additional_emergency_amount": <number | null>,
    "short_term_allocations": [
      {
        "expense_label": "<string>",
        "total_amount": <number>,
        "safe_portion_pct": <number>,
        "safe_portion_amount": <number>,
        "safe_portion_fund": "<string>",
        "safe_portion_subgroup": "<string>",
        "balance_portion_amount": <number | null>,
        "balance_portion_fund": "<string | null>",
        "balance_portion_subgroup": "<string | null>"
      }
    ],
    "negative_nfa_carveout": <number | null>
  },
  "output": {
    "carve_outs": [
      {
        "type": "<string>",
        "amount": <number>,
        "fund_type": "<string>",
        "asset_subgroup": "<string>"
      }
    ],
    "total_carve_outs": <number>,
    "remaining_investable_corpus": <number>
  }
}
```
