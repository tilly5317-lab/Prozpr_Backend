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
