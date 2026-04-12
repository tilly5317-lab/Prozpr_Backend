# Long-Term Goal Allocation — Full Logic Design

**Date:** 2026-04-12  
**Branch:** intent_classifer_dev_8_Apr  
**Approach:** Reference doc update (Approach A) — LLM executes all math guided by reference doc

---

## Scope

Replace the placeholder allocation logic in `references/long-term-goals.md` with the full risk-score-driven, market-commentary-adjusted, ELSS-first subgroup allocation. Update `models.py` and `prompts.py` accordingly. No new files.

---

## Files Changed

| File | Change |
|---|---|
| `references/long-term-goals.md` | Full rewrite — 4-phase allocation logic replaces placeholder |
| `models.py` | Add `investment_goal` to `Goal`; add `MarketCommentaryScores` model + field on `AllocationInput` |
| `prompts.py` | Update step 4 human prompt instruction (one line) to reference asset-class + subgroup logic |

---

## Model Changes

### `Goal` — new optional field

```python
investment_goal: Literal[
    "wealth_creation", "retirement", "intergenerational_transfer",
    "education", "home_purchase", "other"
] = "wealth_creation"
```

Defaults to `"wealth_creation"` so existing test data requires no changes.

### New `MarketCommentaryScores` model

Flat Pydantic model. All fields default to 5 (neutral). Range 1–10.

Fields:
- Asset class level: `equities`, `debt`, `others`
- Equity subgroup level: `low_beta_equities`, `value_equities`, `dividend_equities`, `medium_beta_equities`, `high_beta_equities`, `sector_equities`, `us_equities`

### `AllocationInput` — new field

```python
market_commentary: MarketCommentaryScores = Field(default_factory=MarketCommentaryScores)
```

---

## `long-term-goals.md` Logic — 4 Phases

### Phase 1 — Asset Class Min/Max (from risk score)

1. Look up `effective_risk_score` in the 10-row min/max table (equities, debt, others).
2. If non-integer score, interpolate linearly between adjacent integer rows.
3. **Intergenerational transfer override:** If `age > 60` AND any goal has `investment_goal = "intergenerational_transfer"`:
   - Use `adjusted_score = min(effective_risk_score + 2, 9)` to look up the **min** values only.
   - Keep original score's **max** values unchanged.

### Phase 2 — Market Commentary Proportional Scaling

For each of equities, debt, others:
```
midpoint = (Min + Max) / 2
range_half = (Max - Min) / 2
normalized_view = (view_score - 5) / 5
raw_target = midpoint + normalized_view * range_half
```

Where `view_score` comes from `market_commentary.equities / .debt / .others`.

After computing all three raw targets:
1. Sum raw targets.
2. Scale each proportionally so total = 100%.
3. Clamp any value that breaches its Min or Max; redistribute excess/deficit proportionally among remaining.
4. Round to nearest integer; adjust largest allocation by ±1 if sum ≠ 100.

### Phase 3 — ELSS First-Pass

**Condition:** `tax_regime = "old"` AND `section_80c_utilized < 150000`

```
elss_headroom = 150000 - section_80c_utilized
equity_corpus = remaining_corpus × (equities_pct / 100)
elss_amount = min(elss_headroom, equity_corpus)
residual_equity_corpus = equity_corpus - elss_amount
```

If condition not met: `elss_amount = 0`, `residual_equity_corpus = equity_corpus`.

`tax_efficient_equities` counts fully toward the equity total.

### Phase 4 — Equity Subgroup Allocation

**Pool:** `total_equity_for_subgroups = residual_equity_corpus`

Use the 7-subgroup min/max guardrail table (embedded in the reference doc) at `effective_risk_score`. Interpolate for non-integer scores.

Apply proportional scaling per subgroup using `market_commentary.<subgroup_name>` view scores (same formula as Phase 2).

Normalize so the 7 subgroup targets sum to `total_equity_for_subgroups`. Clamp and redistribute if needed. Drop any subgroup < 1% (after rounding) and redistribute to remaining.

**Debt:** entire `debt_amount` → single `debt` subgroup key (income arbitrage fund). No subgroup table applied.

**Others:** entire `others_amount` → `gold_commodities`.

---

## JSON Output Schema — `subgroup_amounts` keys

```
tax_efficient_equities   ← ELSS (Phase 3)
low_beta_equities
value_equities
dividend_equities
medium_beta_equities
high_beta_equities
sector_equities
us_equities
debt                     ← entire debt allocation (income arbitrage fund)
gold_commodities
```

The 4 old debt subgroup keys (`high_risk_debt`, `long_duration_debt`, `floating_debt`, `medium_debt`) are removed from the long-term goals schema. Debt is a single group.

---

## Downstream Compatibility

- Step 5 (aggregation) consolidates `subgroup_amounts` from all buckets. The `debt` key will appear as a new row in the matrix. No schema change needed in `aggregation.md` — it handles arbitrary subgroup keys.
- Step 6 (guardrails + fund mapping) maps `debt` → Nippon India Arbitrage Fund - Direct Plan - Growth (ISIN: INF204K01XZ7). A new row must be added to `scheme_classification.md`: `debt | debt | Nippon India Arbitrage Fund - Direct Plan - Growth | Arbitrage Fund | INF204K01XZ7`.
- Step 7 (presentation) consumes the aggregated matrix — no change needed.

---

## Invariants

- `equities_pct + debt_pct + others_pct = 100` (after normalization + rounding)
- `sum(all equity subgroups including tax_efficient_equities) = equities_pct` of remaining corpus
- All amounts rounded to nearest 100
- All percentages rounded to nearest integer
