# Step 5 — Presentation

Produce the final JSON output that the frontend will consume directly. The JSON must include:

1. **Allocation data** — all numbers (carve-outs, asset class splits, subgroup splits, amounts)
2. **Rationale** — plain-language, customer-friendly explanations for every key decision

---

## Inputs Required

- Validated allocation from Step 4 (asset class + subgroup percentages)
- Carve-out allocations from Step 1
- Scheme classification mapping from `scheme_classification.md`
- Client profile summary (age, risk score, investment horizon, etc.)

---

## Rationale Guidelines

The `rationale` section must explain every decision in **simple, everyday language** — no finance jargon. Write as if explaining to a friend who has never invested before.

### Tone & Style Rules

- Use "you / your" — speak directly to the customer
- No terms like "alpha", "beta", "duration risk", "NAV", or "asset class" — translate them to plain English
- Keep each explanation to 1–3 short sentences
- Focus on the *why* — what benefit does this give the customer, what risk does it protect against
- Be warm and reassuring, not clinical

### What to Explain

**Carve-outs rationale** — one explanation per carve-out:
- Emergency fund: why money is set aside for emergencies, how many months it covers and why
- Additional emergency (if applicable): why extra safety net is needed when income comes from investments
- Short-term expenses (if applicable): why upcoming known expenses are kept separate and safe
- Negative NFA carve-out (if applicable): why this safety buffer exists
- Tax saving / ELSS (if applicable): why this saves tax and how it works

**Asset allocation rationale** — why the overall split between growth investments (equities), stable investments (debt), and diversifiers (gold/others) was chosen:
- How the customer's risk comfort level influenced the split
- How the investment time horizon played a role
- How the investment goal shaped the decision

**Subgroup allocation rationale** — one explanation per non-zero subgroup:
- What this type of investment does in simple terms
- Why it was included for *this* customer specifically
- If a subgroup is 0%, no rationale needed

---

## Output Structure

### JSON Output

**IMPORTANT — JSON Completeness Rule:** The JSON output must ALWAYS include every field shown in the schema below, in the same structure, every time. Every subgroup must appear in the output even if its allocation is 0%. If a carve-out type is not applicable, omit it from the `carve_outs` array, but all subgroup entries under `subgroup_allocation` must always be present with `pct` set to `0` and `amount` set to `0` if not allocated. Never omit subgroup fields.

Store the following JSON object after completing Step 5. This is the final consolidated output that combines all prior steps.

```json
{
  "step": 6,
  "step_name": "presentation",
  "client_summary": {
    "age": <number>,
    "occupation": "<string>",
    "investment_horizon": "<string>",
    "investment_goal": "<string>",
    "effective_risk_score": <number>,
    "total_corpus": <number>
  },
  "carve_outs": [
    {
      "type": "<string>",
      "amount": <number>,
      "fund_type": "<string>",
      "asset_subgroup": "<string>"
    }
  ],
  "total_carve_outs": <number>,
  "remaining_investable_corpus": <number>,
  "asset_class_allocation": {
    "equities": {"pct": <number>, "amount": <number>},
    "debt": {"pct": <number>, "amount": <number>},
    "others": {"pct": <number>, "amount": <number>}
  },
  "subgroup_allocation": {
    "equity": [
      {"subgroup": "low_beta_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "value_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "dividend_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "medium_beta_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "high_beta_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "sector_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "tax_efficient_equities", "asset_class": "equities", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>}
    ],
    "debt": [
      {"subgroup": "high_risk_debt", "asset_class": "debt", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "long_duration_debt", "asset_class": "debt", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "floating_debt", "asset_class": "debt", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>},
      {"subgroup": "medium_debt", "asset_class": "debt", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>}
    ],
    "others": [
      {"subgroup": "gold_commodities", "asset_class": "others", "recommended_fund": "<from scheme_classification.md>", "asset_class_subcategory": "<from scheme_classification.md>", "isin": "<from scheme_classification.md>", "pct": <number>, "amount": <number>}
    ]
  },
  "rationale": {
    "carve_outs": {
      "emergency_fund": "<string — plain-language explanation of why this amount is set aside, or null if not applicable>",
      "additional_emergency": "<string | null>",
      "short_term_expenses": "<string | null>",
      "negative_nfa_carveout": "<string | null>",
      "tax_saving_elss": "<string | null>"
    },
    "asset_allocation": "<string — 2-4 sentences explaining why the overall growth vs. stability vs. diversification split was chosen for this customer>",
    "subgroup_allocation": {
      "low_beta_equities": "<string | null — explain only if pct > 0>",
      "value_equities": "<string | null>",
      "dividend_equities": "<string | null>",
      "medium_beta_equities": "<string | null>",
      "high_beta_equities": "<string | null>",
      "sector_equities": "<string | null>",
      "tax_efficient_equities": "<string | null>",
      "high_risk_debt": "<string | null>",
      "long_duration_debt": "<string | null>",
      "floating_debt": "<string | null>",
      "medium_debt": "<string | null>",
      "gold_commodities": "<string | null>"
    }
  },
  "all_amounts_in_multiples_of_100": <boolean>,
  "grand_total": <number>
}
```
