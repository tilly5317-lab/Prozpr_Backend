# Ask Tilly · Portfolio Rebalancing Thesis

*Why we change the portfolio when we do — and why we leave it alone the rest of the time*
*Engine version 1.0.0 · Internal & client reference · Last updated: May 2026*

---

> **Status note for follow-up questions:** This document is the client-facing thesis for the rebalancing engine that lives in `ailax/Prozpr_Backend/AI_Agents/src/Rebalancing/`. All thresholds, caps and tax rates quoted below match the live engine (config in `config.py`, lookups in `tables.py`, action codes in `rationales.py`) and reflect the FY25–26 equity tax regime. The companion technical specs in `Rebalancing/Reference_docs/` (`input_parameter_spec.md`, `logical_flow.md`, `output_spec.md`) are the source of truth for implementation detail. Anything in this doc framed as "intent" rather than a number is thesis context and may run ahead of what v1 exposes today.

---

## The one-line thesis

A rebalance is a **disciplined return to the plan** — not a reaction to markets, not a punishment for last quarter's laggards, and not a way to look busy. Your portfolio drifts because markets move, contributions arrive, and our recommended-fund list evolves. We bring the portfolio back to the shape we set up for your goals, but with a deliberate bias against unnecessary trading and a strict eye on the tax bill. Rebalancing should make your plan more like the plan — quietly, cheaply, and with the tax-cheapest units going first.

## Seven principles that drive every rebalancing decision

| Principle | What it means in practice |
| --- | --- |
| **1. Rebalance to the plan, not to the market** | The targets we measure against are the goal-based allocation we computed for *you* — large-cap %, mid-cap %, debt %, etc. We do not raise equity because equity has run, and we do not cut equity because equity has fallen. If your goals and risk profile have not changed, the target shape has not changed, and rebalancing is simply pulling the portfolio back onto that shape. |
| **2. Don't trade for trading's sake** | Every trade costs friction (exit loads, STT, bid–ask) and tax. We refuse to pay those costs for cosmetic alignment. A fund whose holding is within 10% of its target — measured as the absolute gap divided by the larger of (target, present) — is left alone. The 10% threshold (`REBAL_MIN_CHANGE_PCT = 0.10`) is configuration, not folklore: it can be tightened or loosened as the policy view evolves. |
| **3. Quality bar trumps comfort** | Two things turn a fund into a must-sell regardless of how small the drift looks: it has dropped off our recommended list ("BAD" — typically because our research team has replaced it or it has failed an ongoing screen), or its quality rating has fallen below 5 (the exit-floor, `REBAL_EXIT_FLOOR_RATING`). We do not keep a sub-standard fund around just because exiting it would be inconvenient or tax-expensive. |
| **4. Concentration caps over single-fund bets** | No single fund is allowed to hold more than 20% of total corpus when it sits in a wider-mandate sub-category (today: Multi Cap Fund and Multi Asset Allocation) or more than 10% otherwise. When the rank-1 fund in a subgroup hits its cap, the overflow walks to rank-2, then rank-3 — staying within the same asset subgroup, but happy to cross sub-categories if the next rank lives in a different one. Caps protect you from single-manager risk; the rank ladder makes sure the displaced money still goes somewhere we have conviction in. |
| **5. Tax-aware selling, always in the same order** | When we have to sell — to trim, to exit, or to fund a buy — the order in which units leave is the single biggest lever on the tax bill. The engine applies the same ladder in every run, lot by lot: long-term lots first (LTCG at 12.5% above the ₹1,25,000 annual exemption), then realised losses (they offset gains and unlock more rebalancing), then short-term lots outside the exit-load window, and finally short-term lots still inside the exit-load window. A BAD or low-rated fund still gets sold even if it's the most tax-expensive — but among funds we *could* sell, the tax-cheap ones always go first. |
| **6. Respect your STCG budget; put losses to work** | If you have told us how much short-term capital gains you are willing to realise this year (`stcg_offset_budget`), the engine stops adding new STCG once that budget is hit and parks the remaining demand under `undersell_due_to_stcg_cap`. In a second pass, carryforward losses from prior years plus losses already realised in this run are pooled into a loss-offset budget, and as much of the parked demand as that budget can absorb is converted into actual sells. This is the central tax trick of the engine: losses don't reduce taxes on past returns, but they unlock additional rebalancing without raising the current tax bill. |
| **7. The engine never invents cash** | v1 treats every rebalance as a closed system: Σ buys = Σ sells. If demanded buys exceed allowed sells, buys are scaled down proportionally and the shortfall is recorded as an underbuy. Fresh inflows (a salary jump, a maturity, a windfall) and outflows for goals are handled by a separate allocation step *before* rebalancing — the engine does not pretend to find money that isn't on the table. |

We are a rebalancing engine, not a market-timing engine. We do not raise cash before a perceived correction, we do not lever up before a perceived rally, and we do not chase whichever subgroup is currently in vogue. The plan that walked into this rebalance is the plan that walks out — better aligned, tax-efficient, and on the right side of every guardrail.

## How a rebalance is built — in six deliberate steps

Every Ask Tilly rebalance is the output of six sequential, auditable steps. We can walk through any of them on demand.

### Step 1 — Size each fund under concentration caps

Each asset subgroup in your plan maps to one or more recommended funds, ordered by rank — rank 1 is the primary pick, ranks 2 and 3 are alternates in the same sub-category.

- **Per-fund cap.** No single fund may hold more than 20% of total corpus when it sits in a wider-cap sub-category, or 10% otherwise.
- **Spillover by rank.** When the rank-1 fund's target exceeds its cap, the overflow is routed to rank-2 in the same asset subgroup; if rank-2 also caps out, it spills to rank-3.
- **Closed redistribution.** Sizing only redistributes amounts across slots that already exist in the input — the engine never invents new fund rows.
- **Unrebalanced remainder.** If overflow can't be absorbed even at the last rank, the engine flags `unrebalanced_remainder` rather than silently dropping it.

### Step 2 — Compare to present holdings: hold, top up, trim or exit

After sizing, the engine joins the targets to your present holdings on ISIN and labels every fund with one of four actions.

- **Threshold check.** A drift triggers action only when the absolute gap between target and present is at least 10% of the larger of the two.
- **Forced exits.** Two situations override the threshold: the fund is not on our recommended list (target set to zero, tagged "BAD", warning raised), or its rating has fallen below the exit-floor.
- **Hold rows are suppressed.** Funds inside the threshold and not forced out emit no row — the recommendation lists only the funds that are actually changing.

### Step 3 — Classify every lot for tax

Before any sell happens, every lot of every sell-candidate fund is bucketed by tax behaviour: long-term vs short-term (using a 12-month threshold for equity, 24 months for debt funds-of-funds), realised gain vs realised loss, inside vs outside the exit-load window. This is the input the sell ladder operates on.

### Step 4 — Pass 1: initial trades under the STCG budget

Walks sells in the priority ladder (LT → losses → ST no-load → ST with load) and stops adding new STCG once `stcg_offset_budget` is exhausted. Blocked demand is recorded as `undersell_due_to_stcg_cap` — not lost, just deferred. Buys are sized from the resulting sell pool; if buys exceed sells, all buys are scaled down proportionally.

### Step 5 — Pass 2: loss-offset top-up

Carryforward losses from prior years, plus losses already realised in Pass 1, form a loss-offset budget. The engine takes the demand that was blocked by the STCG cap and converts as much of it as the loss budget can absorb into actual sells. This is the second-pass mechanism that puts past-year losses to productive work in the current run.

### Step 6 — Presentation: trades, totals and rationale

Outputs the final trade list with per-row reason codes (`add_to_target`, `cap_spill_buy`, `trim_over_target`, `exit_bad_fund`, `exit_low_rated`) and human-readable rationales, plus totals (gross buys, gross sells, realised STCG, realised LTCG, realised losses) and warnings (unrebalanced remainder, undersell, scaled-down buys). Every changed line traces back to a documented rule.

## Why a customer should trust this approach

| Question | Our answer |
| --- | --- |
| **You're not touching fund X — shouldn't we rebalance it?** | The gap between its present value and its target is under 10% of the larger of the two. Trading it would cost friction and possibly tax, for cosmetic precision. We act when it matters; we don't trade for the sake of trading. |
| **Why are you exiting fund Y? It seems to be doing fine to me.** | Either it is no longer on our recommended list (our research team has replaced or de-listed it), or its quality rating has dropped below our floor of 5. Both are quality signals independent of recent performance, and they override the 10% threshold by design. |
| **Why are you selling my oldest units first?** | Tax. Long-term capital gains are taxed at 12.5% above a ₹1,25,000 annual exemption — typically the cheapest units to liquidate. Then we use realised losses (they offset gains), then short-term units outside the exit-load window, and only as a last resort short-term units still inside the exit-load window. The order is the same every run. |
| **You said you wanted to trim fund Z by ₹X but the trade is smaller — why?** | Most likely your STCG budget for the year was hit before the full demand could be executed. The deferred amount is recorded as `undersell_due_to_stcg_cap` and is re-attempted in Pass 2 against any carryforward losses you have. If both passes still leave it short, the residual is reported, not hidden. |
| **Why are my buys smaller than what the plan said?** | v1 funds every buy from a sell — Σ buys = Σ sells. If allowed sells fell short of demanded buys (because the STCG cap bit, because losses ran out, or because you held mostly fresh units with no LT lots), buys are scaled down proportionally. The shortfall is reported as an underbuy. Fresh capital is handled in a separate step before rebalancing. |
| **What if I add money next month — will you re-run?** | Yes — rebalancing is event-based in v1. There is no fixed monthly cadence; the engine runs when an advisor asks for it. Inflows that change the corpus go through a fresh allocation step first, and rebalancing then aligns the resulting plan with your holdings. |

## What this thesis is — and is not

This document is a reference for the *why* of rebalancing decisions. It is not a market call, not a guarantee that trimming a winner will look smart in three months, and not a substitute for the actual trade list, which is always personalised to your holdings, your tax state, and your goals. Thresholds, caps and tax rates are reviewed periodically and may evolve as policy and tax law change. When they do, this thesis is updated and dated.

---

*Ask Tilly · Rebalancing Thesis · Engine v1.0.0 · Owner: Investment Research · Cycle: reviewed quarterly*
