# Ask PI · Portfolio Rebalancing Thesis

*Why we change the portfolio when we do — and why we leave it alone the rest of the time*
*Thesis version 1.3 · Internal & client reference · Last updated: July 2026*

---

> **About this document:** This is a directional reference for how we think about rebalancing — the philosophy, not the formula. The exact drift thresholds, concentration caps and rating floors are proprietary and are deliberately not reproduced here. Statutory tax facts (capital-gains rates, holding-period thresholds) are public and are stated plainly. Rebalancing runs *after* the practical allocation step (see `Practical_Asset_Allocation.md`), which translates your ideal targets into a holdings-aware plan.

---

## The one-line thesis

A rebalance is a **disciplined return to the plan** — not a reaction to markets, not a punishment for last quarter's laggards, and not a way to look busy. Your portfolio drifts because markets move, contributions arrive, and our recommended-fund list evolves. We bring the portfolio back to the shape we set up for your goals, but with a deliberate bias against unnecessary trading and a strict eye on the tax bill. Rebalancing should make your plan more like the plan — quietly, cheaply, and with the tax-cheapest units going first.

## Eight principles that drive every rebalancing decision

| Principle | What it means in practice |
| --- | --- |
| **1. Rebalance to the plan, not to the market** | The targets we measure against are the goal-based allocation we computed for *you*. We do not raise equity because equity has run, and we do not cut equity because equity has fallen. If your goals and risk profile have not changed, the target shape has not changed, and rebalancing simply pulls the portfolio back onto that shape. |
| **2. Don't trade for trading's sake** | Every trade costs friction (exit loads, transaction taxes, spreads) and capital-gains tax. We refuse to pay those costs for cosmetic alignment. A holding that is already close to its target is left alone. The "close enough" threshold is a deliberate policy choice, reviewed over time — not folklore. |
| **3. Quality bar trumps comfort** | Two things make a fund a must-sell regardless of how small the drift looks: our research team has explicitly marked it for exit (typically because they have replaced it or it failed an ongoing screen), or its quality rating has fallen below our floor. We do not keep a sub-standard fund around just because exiting it is inconvenient or tax-expensive. A fund that has merely slipped off our active recommended list without an exit call is treated more gently — see Step 2. |
| **4. Concentration caps over single-fund bets** | No single fund is allowed to dominate the portfolio. Wider-mandate funds are allowed a larger share than narrow ones, but all are capped. When a top-ranked fund in a category hits its cap, the overflow walks to the next-ranked fund we have conviction in, within the same asset subgroup. The cap is deliberately generous for smaller portfolios: we don't shatter a modest corpus into tiny fund fragments, and we don't recommend a sell — with its tax cost — just to satisfy a percentage on a small holding. Caps protect you from single-manager risk; the rank ladder makes sure displaced money still goes somewhere we believe in. |
| **5. Tax-aware selling, cheapest units first** | When we sell — to trim, to exit, or to fund a buy — which units leave is the single biggest lever on the tax bill. We always sell the tax-cheapest units first: long-term lots lead (long-term capital gains are taxed at 12.5% above a ₹1,25,000 annual exemption), and lots sitting on a realised loss are cheaper still (losses offset gains and unlock more rebalancing). Routine trims go one step further: they only ever touch long-term units — short-term units are sold only when a fund must be exited outright. A delisted or low-rated fund still gets sold even if it's the most tax-expensive — but among funds we *could* sell, the tax-cheap ones always go first. |
| **6. Respect your short-term-gains budget; put losses to work** | If you tell us how much short-term capital gain you're willing to realise this year, we stop adding new short-term gains once that budget is hit and defer the rest. In a second pass, carryforward losses from prior years plus losses realised in this run are pooled, and as much of the deferred demand as those losses can absorb is converted into actual sells. This is the central tax move of the engine: losses don't cut tax on past returns, but they unlock additional rebalancing without raising this year's tax bill. |
| **7. The engine never invents cash** | We treat every rebalance as a closed system: every buy is funded from a sell, so total buys never exceed total sells. If demanded buys exceed allowed sells, buys are scaled down proportionally and the shortfall is reported, never hidden. If a must-exit fund raises more cash than the buys need, the surplus is released to you as cash and reported. Fresh inflows and goal outflows are handled by a separate allocation step *before* rebalancing — we don't pretend to find money that isn't on the table. |
| **8. Frozen holdings stay frozen; concentrated stocks get one instruction** | Tax-saving (ELSS) units under their statutory lock-in are shown but never traded by this engine until they unlock. Direct stocks and PMS, which we can't trim fund-by-fund, are handled as a separate envelope; when they're over-concentrated, we issue a single instruction to reduce them by a given amount and leave the choice of *which* names to sell to you and your advisor. |

We are a rebalancing approach, not a market-timing engine. We do not raise cash before a perceived correction, lever up before a perceived rally, or chase whichever subgroup is in vogue. The plan that walks into a rebalance is the plan that walks out — better aligned, tax-efficient, and on the right side of every guardrail.

## How a rebalance is built — at a high level

Every Ask PI rebalance is the output of a holdings-aware pre-stage and six deliberate, auditable steps. We can walk through the reasoning behind any of them on demand.

### Step 0 — Practical pre-stage: translate ideal into a holdings-aware plan

Before anything is traded, the practical allocation step translates your ideal targets into the portfolio you actually hold: it accounts for locked tax-saving units, recognises direct-stock and PMS holdings up to a sensible ceiling, and flags any over-concentration to reduce. The output of that step is what this engine works from. See `Practical_Asset_Allocation.md`.

### Step 1 — Size each fund under concentration caps

Each asset subgroup in your plan maps to one or more recommended funds, ordered by rank. No single fund may dominate; when a top-ranked fund's target exceeds its cap, the overflow walks to the next-ranked fund in the same subgroup. The cap has a sensible minimum, so smaller portfolios keep a handful of meaningful positions instead of many small fragments. Sizing only redistributes across funds that already exist in your plan — we never invent new positions — and if overflow can't be absorbed, it's flagged rather than silently dropped.

### Step 2 — Compare to present holdings: hold, top up, trim or exit

We join the targets to your current holdings and label each fund: hold, top up, trim or exit. A holding already close to its target emits no action — the recommendation lists only what is actually changing. Two situations override that: a fund our research team has explicitly marked for exit, or one whose quality has fallen below our floor, is exited regardless of how small the drift looks. A fund you hold that is merely no longer on our active recommended list sits in between: it gets no fresh money, its long-term units migrate to recommended funds when buys need funding, and its short-term units are left alone.

### Step 3 — Classify every lot for tax

Before any sell, every lot of every sell-candidate fund is bucketed by tax behaviour: long-term vs short-term (using the statutory holding-period thresholds — 12 months for equity, 24 months for debt-style funds), and realised gain vs realised loss. This is the input the tax-aware sell ordering works from.

### Step 4 — First pass: trade within your short-term-gains budget

We walk sells tax-cheapest first (long-term lots, with loss-making lots cheapest of all; short-term units are touched only on forced exits) and stop adding new short-term gains once your stated budget is exhausted. Blocked demand is deferred, not lost. Buys are funded from the resulting sell pool, scaled down proportionally if they exceed it.

### Step 5 — Second pass: put losses to work

Carryforward losses from prior years, plus losses realised in the first pass, form a loss-offset budget. We take the demand that was deferred by the gains budget and convert as much of it as those losses can absorb into actual sells — additional rebalancing without raising this year's tax bill.

### Step 6 — Presentation: trades, totals and rationale

We output the final trade list, each line with a plain-English reason, plus totals (gross buys and sells, realised gains and losses) and any warnings (unabsorbed overflow, deferred demand, scaled-down buys). Frozen tax-saving units and untradeable direct holdings are shown but carry no trade lines; over-concentrated direct stocks get a single "reduce by this amount" instruction. Every changed line traces back to a documented rule.

## Shaping the plan on request — "fewer funds" and "only these categories"

A customer can ask us to reshape a plan we've just shown: *"that's too many trades — keep it to five funds,"* or *"put all the new money into large-cap."* We honour these, but only on the **buy side**, and always with real numbers.

- **We never re-run the engine to satisfy the ask.** The sells and the tax stay exactly as first computed — because the sells are driven by tax-aware rules and your target mix, not by which funds you'd prefer to buy. Re-solving the plan around "only large-cap" would start selling everything else to chase that target, which could hand you an avoidable tax bill. Instead we take the buy budget the plan already freed up and **redistribute only that**.
- **Fewer funds** keeps the funds the plan weighted most and folds the smaller buys into them proportionally — so you get *fewer, larger* positions, not the same money sprinkled thinner. The total amount invested is unchanged.
- **Only certain categories** sends the whole buy budget into the funds you named (as long as the plan actually buys in those categories), split in proportion to what the plan already intended there. Nothing is left sitting in cash.
- **We always say what it costs you.** Concentrating your buys pulls you away from the ideal mix, so the reply names the trade-off honestly — e.g. "done, but your new money now goes entirely into large-cap, where the plan had spread it across six categories." We comply and caution; we don't quietly refuse, and we don't pretend a constraint is free.
- **This is a conversation, not a saved change.** A reshaped view lives in the chat; your saved plan on the Invest page is untouched until you act on it.

What we don't do yet (we say so plainly when asked): rebuild your **whole portfolio** down to an exact number of funds (that needs selling good holdings purely to hit a count), or treat a one-off "only large-cap" as a standing preference for future rebalances.

## Why a customer should trust this approach

| Question | Our answer |
| --- | --- |
| **You're not touching fund X — shouldn't we rebalance it?** | Its gap to target is within our "close enough" threshold. Trading it would cost friction and possibly tax for cosmetic precision. We act when it matters; we don't trade for the sake of trading. |
| **Why are you exiting fund Y? It seems to be doing fine.** | Either our research team has explicitly marked it for exit (replaced or de-listed it), or its quality rating has dropped below our floor. Both are quality signals independent of recent performance, and they override the "close enough" threshold by design. |
| **Fund W isn't on your recommended list any more — why aren't you exiting it?** | Not every off-list fund is a must-sell. Unless our research has marked it for exit or its rating breaks our floor, we simply stop adding to it: its long-term units migrate to recommended funds when buys need funding, and its short-term units stay put so you don't pay avoidable short-term tax on a fund that's still fine to hold. |
| **Why are you selling my oldest units first?** | Tax. Long-term gains are taxed at 12.5% above a ₹1,25,000 annual exemption — usually the cheapest units to liquidate — and units sitting on a loss are cheaper still, because losses offset gains. For a routine trim we stop there: short-term units are sold only when a fund has to be exited outright. The policy is the same every run. |
| **You wanted to trim fund Z but the trade is smaller — why?** | Most likely your short-term-gains budget for the year was hit before the full demand could execute. The deferred amount is re-attempted in the second pass against any carryforward losses you have. If both passes still leave it short, the residual is reported, not hidden. |
| **Why are my buys smaller than the plan said?** | We fund every buy from a sell — buys never exceed sells. If allowed sells fell short (because the gains budget bit, losses ran out, or you held mostly fresh units), buys are scaled down proportionally and the shortfall reported. Fresh capital is handled in a separate step before rebalancing. |
| **Why aren't you touching my ELSS even though it's off-target?** | Tax-saving (ELSS) units carry a statutory 3-year lock-in. We show the locked amount and count it toward your plan, but generate no trades for it until the units unlock. |
| **You asked me to reduce my direct stocks without saying which — why?** | Single stocks and PMS sit outside our fund-level mandate. We can tell you *how much* to reduce based on your concentration, but picking *which* names to trim is a decision for you and your advisor. |
| **What if I add money next month — will you re-run?** | Yes — rebalancing is event-based. There's no fixed cadence; we run it when an advisor asks. Inflows that change the corpus go through a fresh allocation step first, and rebalancing then aligns the result with your holdings. |

## What this thesis is — and is not

This document is a directional reference for the *why* of rebalancing decisions. It is not a market call, not a guarantee that trimming a winner will look smart in three months, and not a substitute for the actual trade list, which is always personalised to your holdings, your tax state, and your goals. Thresholds, caps and policies behind it are reviewed periodically and may evolve as markets and tax law change. When they do, this thesis is updated and dated.

---

*Ask PI · Rebalancing Thesis v1.2 · Owner: Investment Research · Cycle: reviewed quarterly*
