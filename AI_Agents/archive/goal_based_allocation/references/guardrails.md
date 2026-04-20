# Step 6 — Guardrails Validation + Fund Mapping

Two responsibilities:
1. Validate the long-term subgroup allocation (Step 4) against min/max bounds.
2. Map each allocated subgroup to a recommended mutual fund using the Mutual Fund Type Reference below.

---

## Part 1 — Guardrails Validation (Long-Term Only)

Validate `step4_long_term.output.subgroup_amounts` against the bounds tables.
Use `effective_risk_score` with ceiling lookup (round up to nearest 0.5) to find the row:

- Rule 1: All subgroup amounts sum to `step4_long_term.output.total_allocated`.
- Rule 2: Asset class totals (equities / debt / others) fall within the Phase 1 min/max bounds
  for the risk score (same table as in `long-term-goals.md`).
- Rule 3: Each equity subgroup's share of `equities_amount` falls within Phase 5 subgroup bounds
  (same table as in `long-term-goals.md`).

If any rule is violated, correct and re-check. Apply the same violation resolution process
(clamp → redistribute → re-sum) as in the standard guardrails.

---

## Part 2 — Fund Mapping

For every subgroup in the aggregation output (Step 5), look up the `sub_category` and
a recommended fund from the Mutual Fund Type Reference in this system prompt.

### Mapping Rule

For each `asset_subgroup`:
1. Find matching entries in the reference.
2. Pick one representative fund (prefer Growth option with isinGrowth not null).
3. Return: `asset_class`, `sub_category`, `recommended_fund` (schemeName), `isin` (isinGrowth).

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
        "asset_class": "<equity | debt | others>",
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
