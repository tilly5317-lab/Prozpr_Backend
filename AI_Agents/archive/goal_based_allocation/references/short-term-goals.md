# Step 2 — Short-Term Goals (< 24 Months)

Allocate all goals with `time_to_goal_months < 24` from the remaining corpus after Step 1.

---

## Inputs Required

- `goals` — filter to goals where `time_to_goal_months < 24`
- `effective_tax_rate` — percentage (0–100)
- `remaining_corpus` — from Step 1 output

---

## Asset Subgroup Selection (Tax-Rate Driven)

The asset_subgroup choice is the same for ALL short-term goals regardless of their individual timeline:

```
if effective_tax_rate < 20:
    asset_subgroup = "debt_subgroup"
else:
    asset_subgroup = "arbitrage_income"
```

**Rationale:** Arbitrage funds are taxed as equity (STCG ~15–20%), making them more tax-efficient than debt funds (taxed at slab rate) for investors in higher brackets. For low-tax clients, plain near-debt is simpler and equally effective.

---

## Allocation

1. Sum all short-term goal amounts: `total_goal_amount = sum(goal.amount_needed)`
2. Check shortfall:
   - If `total_goal_amount > remaining_corpus`: allocated_amount = remaining_corpus, flag shortfall
   - Else: allocated_amount = total_goal_amount
3. Place `allocated_amount` entirely into the chosen asset_subgroup.

---

## Shortfall Message

If shortfall, the message must list negotiable goals first as candidates to reduce or defer.

---

```json
{
  "step": 2,
  "step_name": "short_term_goals",
  "output": {
    "goals_in_bucket": [
      {"goal_name": "<string>", "time_to_goal_months": <number>, "amount_needed": <number>, "goal_priority": "<string>"}
    ],
    "asset_subgroup": "<debt_subgroup | arbitrage_income>",
    "total_goal_amount": <number>,
    "allocated_amount": <number>,
    "remaining_corpus": <number>,
    "shortfall": null,
    "subgroup_amounts": {
      "<asset_subgroup>": <number>
    }
  }
}
```
