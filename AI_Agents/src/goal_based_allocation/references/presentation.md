# Step 7 — Presentation

Produce the final JSON output for the frontend. Combine all prior step outputs into
a single client-facing document.

---

## Inputs Required

- Client profile: `age`, `occupation_type`, `effective_risk_score`, `total_corpus`, `goals`
- Step 1–4 outputs: bucket allocations, subgroup amounts, shortfall flags
- Step 5 output: aggregated subgroup × investment_type matrix
- Step 6 output: fund mappings, validated long-term allocation

---

## Rationale Guidelines

Write all rationale text in plain, everyday language. No finance jargon.
Use "you / your". Each explanation: 1–3 short sentences. Focus on the *why*.

Do NOT use: alpha, beta, duration risk, NAV, asset class, volatility, liquidity,
corpus, portfolio rebalancing.

Explain:
- **Emergency bucket**: why money is set aside, how many months it covers
- **Short-term bucket**: why each goal uses the chosen instrument (tax reasoning in simple terms)
- **Medium-term bucket**: why the equity/debt split was chosen for each goal's timeline
- **Long-term bucket**: why the allocation is growth-oriented for long horizons
- **Shortfalls** (if any): friendly message suggesting either more investment or cutting negotiable goals

---

## JSON Output Schema

```json
{
  "step": 7,
  "step_name": "presentation",
  "client_summary": {
    "age": <number>,
    "occupation": "<string | null>",
    "effective_risk_score": <number>,
    "total_corpus": <number>,
    "goals": [
      {"goal_name": "<string>", "time_to_goal_months": <number>,
       "amount_needed": <number>, "goal_priority": "<string>"}
    ]
  },
  "bucket_allocations": [
    {
      "bucket": "<emergency | short_term | medium_term | long_term>",
      "goals": [...],
      "total_goal_amount": <number>,
      "allocated_amount": <number>,
      "shortfall": null,
      "subgroup_amounts": {"<subgroup>": <number>},
      "rationale": "<plain-language explanation>"
    }
  ],
  "aggregated_subgroups": [
    {
      "subgroup": "<string>",
      "sub_category": "<string>",
      "emergency": <number>,
      "short_term": <number>,
      "medium_term": <number>,
      "long_term": <number>,
      "total": <number>,
      "fund_mapping": {
        "asset_class": "<equity | debt | others>",
        "asset_subgroup": "<string>",
        "sub_category": "<string>",
        "recommended_fund": "<string>",
        "isin": "<string>",
        "amount": <number>
      }
    }
  ],
  "shortfall_summary": [
    {
      "bucket": "<emergency | short_term | medium_term | long_term>",
      "shortfall_amount": <number>,
      "message": "<plain-language explanation>"
    }
  ],
  "grand_total": <number>,
  "all_amounts_in_multiples_of_100": <boolean>
}
```
