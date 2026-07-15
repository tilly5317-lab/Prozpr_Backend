# Audit B — Liquid → Arbitrage / Growth switch behaviour

**Date:** 2026-05-21
**Trigger:** Vikram chat-test feedback — "Should there be restriction on switch from Liquid to arb. Also the idea is not to move from liquid to growth but liquid to arb I think. The idea behind moving from one fund to another should be clear. Right now it's confusing. Also add tax efficient logic in."
**Scope:** What actually happens today when the engine sees a Liquid fund holding during rebalancing, and why the user perceives a "Liquid → Growth" flow.

---

## TL;DR

The user's perception is partly right and partly an artefact of how trades are presented:

- **The engine does NOT move money from Liquid to Growth as a single decision.** It makes three independent decisions in the same rebalance: (i) exit the Liquid fund because it isn't on the recommended list, (ii) buy Arbitrage to hit the allocation target, (iii) buy Growth to hit the allocation target. The user reads the sell + the buys together and infers a "Liquid → Growth" flow that doesn't exist in code.
- **However**, the user is correct that the engine has no concept of "tax-efficient subgroup substitution". It cannot earmark Liquid sale proceeds for Arbitrage. It also can't reason "this Liquid holding is essentially an arbitrage candidate — should I keep it instead of switching?".
- **There is also no `liquid` subgroup** in the production allocation universe. The engine only knows `arbitrage`, `arbitrage_plus_income`, `short_debt`, `other_debt`. A user's Liquid mutual fund is bucketed (via [fund_rank.py](app/services/ai_bridge/rebalancing/fund_rank.py)) into `short_debt`, which is why it ends up labelled as a generic short-debt holding.

The fastest credible fix is **presentation-side narration** — group related trades and explain the "why" — without changing engine arithmetic. A deeper engine fix (cross-subgroup substitution) is a separate, larger effort.

## Current state — concise map

| Question | Answer | Reference |
|---|---|---|
| Does an `arbitrage` subgroup exist? | Yes, distinct from `short_debt`. Also `arbitrage_plus_income`. | `AI_Agents/Reference_docs/prozpr_fund_ranking_may_2026.csv` |
| Does a `liquid` subgroup exist? | **No**, not in the active fund-ranking CSV. Liquid funds are classified as `short_debt` via metadata. | [fund_rank.py](app/services/ai_bridge/rebalancing/fund_rank.py) |
| When does AA pick `arbitrage` vs `short_debt`? | Tax-rate threshold. Emergency / short-term: `effective_tax_rate > 20%` → `arbitrage`. Medium-term: `>= 15%` → `arbitrage_plus_income`. Long-term: no debt at all. | [step1_emergency.py:43-47](AI_Agents/src/asset_allocation_pydantic/steps/step1_emergency.py:43), [step2_short_term.py:12-16](AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py:12), [step3_medium_term.py:39-43](AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py:39), [tables.py:168-169](AI_Agents/src/asset_allocation_pydantic/steps/tables.py:168) |
| Does Rebalancing redirect a Liquid exit's proceeds to Arbitrage? | **No.** Each subgroup target is independent. Sells and buys are computed per-subgroup and never linked. | [input_builder.py:179-201](app/services/ai_bridge/rebalancing/input_builder.py:179), [step1_cap_and_spill.py:30-86](AI_Agents/src/Rebalancing/steps/step1_cap_and_spill.py:30) |
| Are there guardrails against "Liquid → Growth" specifically? | None. The engine sees an exit and several buys; user perceives causation. | [step6_presentation.py:63-94](AI_Agents/src/Rebalancing/steps/step6_presentation.py:63) |
| What rationale text does the user see? | One of five canned strings: `add_to_target`, `cap_spill_buy`, `trim_over_target`, `exit_bad_fund`, `exit_low_rated`. No "redirect" code. | [rationales.py:15-55](AI_Agents/src/Rebalancing/rationales.py:15) |
| Is there cross-subgroup tax-aware substitution? | **No.** STCG/LTCG prioritisation operates **within** a subgroup only. | [step4_initial_trades_under_stcg_cap.py](AI_Agents/src/Rebalancing/steps/step4_initial_trades_under_stcg_cap.py) |

## How a "Liquid → Growth" recommendation arises today (walk-through)

User has 30% tax bracket and an existing Liquid fund worth ₹100k. New corpus arriving. AA produces:

- `arbitrage`: ₹30k (because tax-rate > 20%)
- `short_debt`: ₹20k (residual debt slot)
- `high_beta_equities`: ₹50k (long-term)

Rebalancing input-builder loads the ranking CSV. For each subgroup, it looks up the rank-1 fund. It does **not** check "does the user already hold a fund in `short_debt`?". The user's Liquid fund is in `short_debt` but is not rank-1 (or not on the recommended list at all), so it gets marked BAD via the `is_recommended=False` flag.

Step 1–5 produce:
- SELL Liquid ₹100k — reason `exit_bad_fund` ("Not in recommended list; exiting frees capital.")
- BUY Arbitrage rank-1 ₹30k — reason `add_to_target`
- BUY Short-debt rank-1 ₹20k — reason `add_to_target`
- BUY High-beta equity rank-1 ₹50k — reason `add_to_target`

The user reads this as four bullets, with the Liquid sale appearing to "fund" the high-beta buy. The engine doesn't think this way — it computes deltas per subgroup independently.

## Three options to address this

### Option 1 — Presentation-only narration (smallest)

**What:** Add a post-processing step in [step6_presentation.py](AI_Agents/src/Rebalancing/steps/step6_presentation.py) that groups trades into stories: when a fund is exited in subgroup X and a fund is bought in a related subgroup Y, attach a paired rationale that explains the cash flow. Add new rationale codes: `redirect_to_arbitrage`, `redirect_to_short_debt`, `redirect_to_arbitrage_plus_income`. Keep all engine arithmetic unchanged.

**Why this might be enough:** The Liquid → Growth perception is largely a narration failure, not an arithmetic failure. The engine's per-subgroup decisions are defensible if they're explained.

**Blast radius:** ~3 files (rationales.py, step6, formatter). Easy regression — fixtures stay the same arithmetic-wise, only the rationale text changes.

**What it doesn't fix:** If the engine genuinely should hold the Liquid as Arbitrage proxy (instead of selling and re-buying), this option doesn't help. Also doesn't fix cross-subgroup STCG optimisation.

### Option 2 — Source→sink earmarking + presentation (medium)

**What:** Extend `FundRowInput` with an optional `redirect_target_subgroup` hint. When input_builder sees a BAD fund in `short_debt` and a positive target in `arbitrage`, it tags the Liquid row with `redirect_target_subgroup="arbitrage"`. Step 6 surfaces this as "Redeploy ₹X from Liquid into tax-efficient Arbitrage." Engine arithmetic still computes independently, but the user-facing narrative becomes accurate.

**Blast radius:** ~6 files. Adds a hint column through input → step1 → step5 → step6. Doesn't change buy/sell amounts.

**Catch:** This is partly a UX patch over a deeper question — should we ever recommend selling a Liquid fund just because it isn't rank-1 in `short_debt`? If the holding period is < 7 days the exit-load can be material; if it's a low-cost reputable Liquid, churning it for a rank-1 alternative may be net-negative after tax/fees. Worth a guardrail here.

### Option 3 — Full cross-subgroup substitution logic (largest)

**What:** Make Rebalancing actually optimise: "given user's holdings and allocation targets, pick the minimum-tax-cost set of trades", allowing cross-subgroup substitution (e.g., keep a held Liquid as a partial filler for the `short_debt` target, even if it isn't rank-1). Touches step1–step6 and adds new objective-function machinery.

**Blast radius:** 15+ files, golden tests rewritten, requires a clear specification of the optimisation problem.

**When to consider:** Once Options 1 and 2 ship and we know whether the residual user complaints are about *narrative* or *actual trades*.

## Recommendation

Ship Option 1 first (≤ 1 day of work). Re-run Vikram's transcript against the new narration. If the resulting trade list still feels wrong, escalate to Option 2. Treat Option 3 as a Q3-2026 candidate, not now.

## Open questions before implementation

1. **Do we want a guardrail against "exit-just-because-not-rank-1" for short-debt-class funds?** Liquid funds often have low TERs already — the rank delta may not justify churn. A simple rule: don't exit if the fund's `selection_reason` is non-empty and within 1 rank of the recommended target. **(needs a small spec.)**
2. **Threshold for adding the `redirect_*` rationale**: should the engine attach it only when sale ₹ approximately matches a buy ₹, or always when a related-subgroup buy exists in the same plan?
3. **Tax-bracket sourcing**: where does Rebalancing read `effective_tax_rate` from today? The audit found it's not plumbed into the tax-cheapness sort in step4. **(needs check before Option 2.)**
