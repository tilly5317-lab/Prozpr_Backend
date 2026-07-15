# Audit A — FOF Overseas classification

**Date:** 2026-05-21
**Trigger:** Vikram chat-test feedback PQ06 — "Maybe it's treating it as FOF overseas which is not equity as per SEBI definition. Check how FoF are treated in our logic."
**Scope:** How FOF Overseas funds are classified today and what changes if we align with SEBI's non-equity tax treatment.

---

## TL;DR

There are **two distinct defects**, and they should be addressed separately:

1. **Tax math defect (high priority, low blast radius).** The Rebalancing engine hard-codes equity STCG/LTCG rates and a 12-month equity holding-period threshold ([Rebalancing/config.py:24-28](AI_Agents/src/Rebalancing/config.py:24)). FOF Overseas holdings are taxed as equity in our recommendations, but SEBI/IT rules treat them as non-equity (slab-rate STCG, 12.5% LTCG after 24 months, no STT). This produces **wrong sell-prioritisation and wrong tax-cost numbers** in the rebalance plan.

2. **Display defect (lower priority, higher blast radius).** US Linked FoF currently rolls up to `asset_class=equity` in the portfolio breakdown ([common.py:29-50](app/services/ai_bridge/common.py:29), [tables.py:99-117](AI_Agents/src/asset_allocation_pydantic/steps/tables.py:99)). China Linked and "Others (FoF)" already roll to `others`. This is a **labelling inconsistency** that confuses users when they reconcile against their CAS.

I recommend Option 1 below (fix the tax math first, leave allocation/display alone) and treat allocation reclassification as a separate, larger work item.

## Current state — concise map

| Layer | File | Current behaviour |
|---|---|---|
| Subgroup → asset_class rollup | [tables.py:99-117](AI_Agents/src/asset_allocation_pydantic/steps/tables.py:99), [common.py:29-50](app/services/ai_bridge/common.py:29) | `us_equities` → `equity`; `china_equities`, `others_fofs` → `others` |
| Phase 5 equity sub-bounds | [tables.py:45-77](AI_Agents/src/asset_allocation_pydantic/steps/tables.py:45) | `us_equities` carved out 20–40% of equity bucket; bounds vary by risk score |
| Market-view scores | [tables.py:131-140](AI_Agents/src/asset_allocation_pydantic/steps/tables.py:131) | `us_equities: 5.0` (neutral) — used by AA engine for tilts |
| Rebalancing tax rates | [Rebalancing/config.py:24-28](AI_Agents/src/Rebalancing/config.py:24) | `STCG_RATE_EQUITY_PCT=20`, `LTCG_RATE_EQUITY_PCT=12.5`, `ST_THRESHOLD_MONTHS_EQUITY=12` — **no non-equity branch** |
| Tax classification | [step3_tax_classification.py:29-57](AI_Agents/src/Rebalancing/steps/step3_tax_classification.py:29) | Splits ST/LT by month threshold; calls equity-only rate constants |
| Trade prioritisation under STCG cap | [step4_initial_trades_under_stcg_cap.py](AI_Agents/src/Rebalancing/steps/step4_initial_trades_under_stcg_cap.py) | Sorts sells by tax-cheapness using the equity rates above |
| Portfolio summary | [portfolio_query/models.py](AI_Agents/src/portfolio_query/models.py), [rebalancing/service.py:339-350](app/services/ai_bridge/rebalancing/service.py:339) | Rolls holdings into equity/debt/others via `asset_class_for_subgroup` |
| Fund metadata source | [mf_fund_metadata.py:59](app/models/mf/mf_fund_metadata.py:59) | `asset_class` column populated from CSV — US Linked currently `"equities"` |

## Defect 1 — Tax math (the engine charges equity tax on non-equity funds)

The Rebalancing tax module is **structurally equity-only**. There is no `STCG_RATE_NONEQUITY_PCT` constant, no asset-class branch in step3/step4, and no 24-month threshold anywhere. Per the audit, every sell-decision sort key in [step4](AI_Agents/src/Rebalancing/steps/step4_initial_trades_under_stcg_cap.py) is computed at equity rates. Consequence:

- A FOF Overseas holding held 13 months is treated as **LTCG-eligible** (12-month threshold), so the engine values its gain at 12.5%. Real-world: STCG slab rate applies until 24 months — the gain is much costlier to realise. Engine will **under-price the tax bill** and over-recommend the sell.
- A FOF Overseas loss is valued at STCG-offset of 20%, when actual offset is at slab rate (often 30%). Engine will **under-value the loss-harvest opportunity**.

This is a correctness bug. Fixing it does not require changing allocation behaviour or display.

### Recommended fix for Defect 1 (Option 1, ~6 files)

1. Add to [Rebalancing/config.py](AI_Agents/src/Rebalancing/config.py): `STCG_RATE_NONEQUITY_PCT` (= user's slab rate, plumbed from input), `LTCG_RATE_NONEQUITY_PCT = 12.5`, `ST_THRESHOLD_MONTHS_NONEQUITY = 24`.
2. Add `asset_class` to `FundRowInput` (already present in [input_builder.py:215,241](app/services/ai_bridge/rebalancing/input_builder.py:215) — it just isn't plumbed deeper).
3. In [step3_tax_classification.py](AI_Agents/src/Rebalancing/steps/step3_tax_classification.py) branch the month-threshold on `asset_class`.
4. In [step4_initial_trades_under_stcg_cap.py](AI_Agents/src/Rebalancing/steps/step4_initial_trades_under_stcg_cap.py) branch the rate constants used in the sort key.
5. Tag US Linked, China Linked, Others (FoF) as `asset_class="others"` (or a new `"non_equity"` tag) in the DB column [mf_fund_metadata.asset_class](app/models/mf/mf_fund_metadata.py:59) — **only for the tax engine's purposes** — and leave the AA-side subgroup mapping (`us_equities` → equity) untouched for now.
6. Tests: golden Rebalancing fixtures for a portfolio containing FOF Overseas across ST/LT cutoffs.

**Blast radius:** ~6 files, all inside Rebalancing/its facts-pack. Allocation output, AA chat formatter, and PQ summary are unaffected.

**Catch:** Step 5 above couples `asset_class` semantics across the Rebalancing engine and the AA engine. If we ever do the full reclassification (Defect 2), we'll need to split these into two columns or two enums. Document this in the migration note when shipping.

## Defect 2 — Display rollup (US Linked shown as Equity)

Currently US Linked rolls into the equity bucket of the portfolio summary; China Linked and Others (FoF) roll into `others`. Net effect: a user with FOF Overseas exposure sees their "equity %" overstate domestic equity. Whether this is a defect depends on stance:

- **Stance A — keep as equity for display.** Justification: from an underlying-exposure perspective, US Linked *is* equity risk. Indian retail users often think of FOF Overseas as their "US stocks". Reclassifying it to `others` will surprise users who expected to see overseas under equity. Trade-off: the asset_class label diverges from the SEBI tax label.
- **Stance B — move to `others` (or a new `overseas` class).** Justification: aligns with SEBI's scheme classification (FoF Overseas sits under "Other Schemes"). Trade-off: large cascade — Phase 5 equity sub-bounds (which currently slot `us_equities` inside the 6-subgroup equity decomposition with a 20–40% band) would need rework, the engine could lose its ability to deliberately allocate to overseas if it's not in the equity decomposition, and ~30 files are touched per the audit.

### Recommended fix for Defect 2

Defer until we have a clear product decision. The user-visible effect (Vikram seeing "₹0 in overseas" in Rb05) is more likely a *fund-selection* issue (no FOF Overseas fund mapped in his ranking-table universe) than a *classification* issue. We should investigate Rb05 directly before deciding to reclassify.

## Surface-area summary

| Option | Files touched | Risk | What it buys |
|---|---|---|---|
| **1. Tax math only** (recommended) | ~6 | Low | Correct tax cost for FOF Overseas in rebalancing |
| **2. Full reclassification to `others`** | ~30 | High | Consistent display + SEBI alignment everywhere |
| **3. New `overseas` pseudo-class** | ~40+ | High | Cleanest long-term; needs UI work |

## Open questions

1. **Slab rate for non-equity STCG**: we already pass `effective_tax_rate` into AA via `ClientProfile.effective_tax_rate`. Should Rebalancing read the same field as the slab rate for non-equity STCG, or should this come from the user's income-tax bracket directly?
2. **Holding-period reset**: FOF Overseas units acquired before April 2023 follow different rules. Out of scope for now — flag in release notes.
3. **Other "non-equity" funds**: gold ETFs, international ETFs, debt funds — they all share the slab-rate / 24-month rule today. Should the new non-equity branch in step3/step4 also apply to debt funds, or do debt funds already get correct treatment? **(needs a follow-up check before implementation.)**
