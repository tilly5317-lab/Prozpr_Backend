# Ask PI · Cashflow & Goal-Planning Thesis

*Why we project the way we do — and what the corpus picture is really telling you*
*Thesis version 1.2 · Internal & client reference · Last updated: July 2026*

---

> **About this document:** This is a directional reference for how we build a cashflow and goal plan — the philosophy, not the formula. The exact return assumptions, inflation rates, growth defaults and solver mechanics are proprietary and are deliberately not reproduced here. Conventions that are public (the Indian financial-year calendar, standard mortgage mechanics) are stated plainly.

---

## The one-line thesis

A financial plan should answer two questions honestly: *will every goal you've set get its money on time?* and *what corpus will retirement require of you?* We build a year-by-year, month-by-month projection of your household's money — income, taxes, expenses, EMIs, investments, one-offs, goal payouts — and run it against a single shared corpus, then report not just whether the goals work, but where and why they don't. The retirement corpus is shown alongside the plan as a clear reference target rather than folded invisibly into the verdict. The projection is deterministic: the same inputs always produce the same plan, and every rupee in the output traces back to a documented step.

## Seven principles that drive every projection

| Principle | What it means in practice |
| --- | --- |
| **1. One shared corpus pool, not per-goal earmarking** | Real households don't keep separate piggy banks for each goal — money is fungible. We maintain a single corpus that walks forward in time: opening balance, plus contributions and returns and one-off inflows, minus goal payouts and one-off outflows, gives the closing balance. When a period runs short, the shortfall is split *proportionally* across that period's outflows rather than starving one goal to feed another. This is the only honest way to model what actually happens when capital gets tight. |
| **2. Time is measured precisely, and symmetrically** | Inflating a goal to its future cost and discounting it back to today use the *same* precise measure of elapsed time — no rounding to whole years, no calendar-boundary jumps. The payoff: every "today's rupees ↔ future rupees" pair reconciles back to itself, so we can show you "₹X today equals ₹Y at the goal date" without the two numbers ever drifting apart. |
| **3. Expected return depends on the horizon, not a single number** | A goal a year away cannot prudently assume the same return as one twenty years out — pretending it can is the most common modelling error. We use lower expected returns for near-term goals and higher ones for long-dated goals, and the same horizon-appropriate return drives how the corpus is assumed to grow while it funds each goal. |
| **4. Two feasibility views, one canonical** | We surface two numbers that look like they answer "is the plan feasible?" but answer different questions. The canonical one asks: *given your full plan — income, taxes, expenses, contributions, EMIs, one-offs, goal payouts — does every goal get funded on time and does the corpus end non-negative?* The present-value view asks: *if you stopped contributing today and lived only off today's corpus, would there be enough?* The second often disagrees with the first — and that's the point: it explains *how* the plan works (from future contributions vs. from existing corpus) rather than restating the verdict. |
| **5. Mortgage purchases are upfront + EMI stream, never a balloon** | When a goal property is bought on a mortgage, the corpus pays only the *upfront* on the goal date; the rest becomes a monthly EMI flowing through the projection until the loan is paid off (using standard Indian EMI mechanics). The same property goal can be a sharp one-time corpus drain (cash purchase) or a long, even drag on monthly savings (mortgage) — and we show you both shapes correctly rather than averaging them. (Plans set up through chat currently capture a property goal as a cash purchase; the mortgage shape is an engine capability not yet exposed there.) |
| **6. The Indian Financial Year is the projection's calendar** | The financial year runs April to March, because that's the calendar on which household income is earned, taxed and remembered in India. Income, taxes, expense step-ups and the cashflow display are all aligned to it. Within a year, income is treated as level — because that's how a salary actually works. |
| **7. The horizon stretches as far as your latest goal — no further** | The projection runs out to your latest goal, so a goal dated after your retirement age is never dropped — income, contributions and corpus growth are modelled all the way out to it. Your retirement corpus is presented as a separate reference target next to the plan: we show what retirement will require, but we don't deduct it from the pool as a payout, and the feasibility verdict scores the goals you've explicitly set. One-off outflows scheduled beyond the plan's end are dropped, with a clear warning. |

We are a goal-planning approach — not a tax calculator, not a portfolio-construction engine, and not a debt-counselling tool. We surface honest numbers for the questions we were built to answer, and flag the limits clearly when asked to do more.

## How a projection is built — at a high level

Every Ask PI plan moves through eight deliberate, auditable stages. We can walk through the reasoning behind any of them on demand.

### Stage 1 — Profile

We lift your household snapshot into the projection: starting corpus, income, effective tax rate, monthly expenses and current contributions. **The starting corpus is everything you hold** — your linked mutual-fund portfolio *plus* directly-held equity *plus* cash and debt holdings — so it is normally larger than the portfolio figure on your dashboard, and we're careful to call it your total investments rather than your portfolio. The projection then models that combined pool as one; there is no separate year-by-year forecast for the portfolio, the shares or the cash on their own, so we won't tell you what any single component will be worth in a given year. We anchor today's date and the planning horizon, and record the assumptions in use — every assumption is reviewable and overridable, with no hidden magic numbers in the inner stages.

### Stage 2 — Retirement

For each retirement scenario we work out the retirement date, inflate today's living costs to that date, and solve for the corpus that — earning a sensible real (after-inflation) return — can pay those inflated costs through a conservative assumed lifespan. We then show that target both in future-value and in today's-rupees terms. This target is a reference figure shown alongside the plan — it is not deducted from the corpus as a payout, and the feasibility verdict scores your explicit goals against the corpus, not this target.

### Stage 3 — Existing mortgages

For any property you already own with an active loan, we lay out the monthly EMI to its end date. We trust the EMI you give us rather than reverse-engineering it, and feed it straight into the cashflow as a fixed outflow.

### Stage 4 — Goal properties

For each property you *want* to buy: a cash purchase takes the full price out of the corpus on the goal date; a mortgage purchase takes only the upfront amount then, with the remainder becoming an EMI stream over the loan's life. Today, a property goal set up through chat is modelled as a cash purchase on its date; the upfront-plus-EMI mortgage shape is an engine capability we haven't yet exposed there.

### Stage 5 — Goals table

We combine your property goals and custom goals into a single table with shared fields — today's value, future value, the corpus required, the horizon-appropriate return, and the present-value of what's needed — so every goal is compared on the same footing. The retirement target sits alongside this table as its own reference figure.

### Stage 6 — Cashflow projection

We walk every month from today to the end of the horizon, producing a row per month: income (growing year over year across the horizon), taxes, living costs (stepped up with inflation), mortgage EMIs, savings before and after EMIs, and any one-off flows.

### Stage 7 — Funding: the shared corpus pool

This is the heart of the plan: one pool, walked forward month by month. The opening balance carries from the prior month; a sensible portion of each month's savings is invested (or, if savings are negative, drawn down); the corpus grows at its horizon-appropriate return; goal payouts and one-offs are paid as they fall due; and any month that comes up short splits the shortfall proportionally across that month's outflows.

### Stage 8 — Summary

We aggregate into two views: a headline status (today's corpus, what's required, the surplus or shortfall, the projected end-of-horizon balance, and the feasibility verdict) and a fund-flow reconciliation that ties opening balance, contributions, returns, inflows, outflows and goal payouts to the closing balance — every rupee accounted for.

## Exploring and changing your plan in chat

- **What-if questions are live but hypothetical.** Ask "what if I retire at 50?", "what if I invested ₹50,000 more a month?", "what if my expenses rose to ₹3 lakh a month?", or "can I afford a ₹10 lakh trip next year?" and we re-run the full eight-stage projection with that one change applied — but the result is never saved.
- **Chat does not save changes.** A what-if answer is a look, not a commitment. To change your plan durably, update your profile — the plan regenerates from the updated inputs.
- **We don't let you shop for a rosier assumption.** Return rates, inflation and income growth are not explorable this way — only inputs you actually control (when you retire, how much you invest or spend, a specific one-off) can be changed.

## Why a customer should trust this approach

| Question | Our answer |
| --- | --- |
| **Why is my retirement corpus so high?** | It's the lump-sum-today equivalent of paying your *inflated* living costs every year through retirement, earning only a modest *real* (after-inflation) return on what's left. Because inflation eats much of the nominal return, the corpus has to be a large multiple of your annual expense. The two levers that move it most are inflation and your post-retirement return. |
| **I have a shortfall today — but you say my plan is feasible. How?** | The two numbers answer different questions. The present-value view asks whether today's corpus alone could fund everything if you stopped contributing now. The canonical view asks whether everything works *given your future contributions, income growth and one-offs*. Young, high-saving clients almost always show a present-value shortfall but full feasibility — their future contributions do the heavy lifting. |
| **Will my goal scheduled after retirement still be funded?** | Yes. The projection runs out to your latest goal, with income, contributions and corpus growth modelled all the way to it, and the goal is paid from the projected corpus on its date. Your retirement corpus is shown alongside as a reference target rather than deducted from the pool, so a late goal isn't competing with a retirement payout. One-off outflows past the plan's end are dropped, with a warning. |
| **A goal a few years away — shouldn't it earn long-term returns?** | We assign returns by horizon, and a few years out is treated as medium-term, not long-term. We deliberately don't let a goal near a boundary "pick the better band" — the time math has to stay consistent so the today ↔ future-value pair reconciles. |
| **Why didn't you split corpus equally across my goals in a shortfall?** | We keep a single shared corpus, not per-goal balances — that's how real household money works. When a period under-funds, the shortfall is split *proportionally* to each outflow's size. Goal-priority routing would be a deliberate policy change, not the default. |
| **Why isn't my contribution showing tax savings?** | This plan applies your effective tax rate to gross income only. Tax-shield effects on investments live in the allocation approach, not here — keeping the projection deterministic and avoiding double-counting between the two. |
| **What if my income jumps in a few years — does the plan know?** | Not unless you tell it. Year-on-year income growth is applied uniformly. A specific event — a bonus, a salary jump, a property sale, a maturity — should be entered as a one-off inflow or outflow on its exact date. (The projection engine models one-offs precisely. You can ask a one-off "what if" in chat — e.g. "can I afford a ₹10 lakh trip next year?" — and get a real answer on the spot; making it a permanent part of your saved plan isn't available through chat yet.) |

## What this thesis is — and is not

This document is a directional reference. It is not a prediction, not a guarantee of returns, and not a substitute for the actual plan, which is always personalised. The plan does not currently model: portfolio rebalancing or allocation drift (see the Rebalancing thesis), tax-shield effects on contributions, per-goal earmarking, debt-cost accrual on a negative corpus, retirement as a deducted payout (the retirement corpus is a reference target, not a line in the funding walk), or mid-projection life events that aren't in the input. The assumptions, inflation rates and return bands behind it are reviewed periodically and may evolve. When they do, this thesis is updated and dated.

---

*Ask PI · Cashflow & Goal-Planning Thesis v1.3 · Owner: Investment Research · Cycle: reviewed quarterly · last reconciled with production wiring 2026-08-04*
