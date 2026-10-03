# Ask PI · Cashflow & Goal-Planning Thesis

*Why we project the way we do — and what the corpus picture is really telling you*
*Thesis version 1.4 · Internal & client reference · Last updated: 3 October 2026*

---

> **About this document:** This is a directional reference for how we build a cashflow and goal plan — the philosophy, not the formula. The exact return assumptions, inflation rates and growth defaults are proprietary and are deliberately not reproduced here. Conventions that are public (the Indian financial-year calendar) are stated plainly.

---

## The one-line thesis

A financial plan should answer two questions honestly: *will every goal you've set get its money on time?* and *how does your total corpus evolve on the way there?* We build a year-by-year, month-by-month projection of your household's money — income, taxes, expenses, EMIs, investments, one-offs, goal payouts — and run it against a single shared corpus, then report not just whether the goals work, but where and why they don't. Retirement enters the plan as a goal you set — funded from the same pool as every other goal — rather than as a separate target of our own. The projection is deterministic: the same inputs always produce the same plan, and every rupee in the output traces back to a documented step.

## Seven principles that drive every projection

| Principle | What it means in practice |
| --- | --- |
| **1. One shared corpus pool, not per-goal earmarking** | Real households don't keep separate piggy banks for each goal — money is fungible. We maintain a single corpus that walks forward in time: opening balance, plus contributions and returns and one-off inflows, minus goal payouts and one-off outflows, gives the closing balance. When a period runs short, the shortfall is split *proportionally* across that period's outflows rather than starving one goal to feed another. This is the only honest way to model what actually happens when capital gets tight. |
| **2. Time is measured precisely, and symmetrically** | Inflating a goal to its future cost and discounting it back to today use the *same* precise measure of elapsed time — no rounding to whole years, no calendar-boundary jumps. The payoff: every "today's rupees ↔ future rupees" pair reconciles back to itself, so we can show you "₹X today equals ₹Y at the goal date" without the two numbers ever drifting apart. |
| **3. Expected return depends on the horizon, not a single number** | A goal a year away cannot prudently assume the same return as one twenty years out — pretending it can is the most common modelling error. We use lower expected returns for near-term goals and higher ones for long-dated goals. The shared corpus follows the same bands over time: it is assumed to earn the near-term return in the early years of the projection, the medium-term return after that, and the long-term return beyond. |
| **4. Two feasibility views, one canonical** | We surface two numbers that look like they answer "is the plan feasible?" but answer different questions. The canonical one asks: *given your full plan — income, taxes, expenses, contributions, EMIs, one-offs, goal payouts — does every goal get funded on time and does the corpus end non-negative?* The present-value view asks: *if you stopped contributing today, could today's corpus alone, left to grow at horizon-appropriate returns, pay for every goal?* The second often disagrees with the first — and that's the point: it explains *how* the plan works (from future contributions vs. from existing corpus) rather than restating the verdict. |
| **5. Home loans run as an EMI stream; planned purchases are paid in cash** | A home loan you already have flows through the projection from the EMI and end date you give us, taken as stated rather than recomputed: the EMIs that fall in each financial year are added up and spread evenly across that year's months. A property you plan to buy is a goal like any other: its full inflated price comes out of the corpus on the goal date. The plan doesn't capture a down-payment or a new loan for a future purchase, so a planned property shows as one sharp corpus drain on its date rather than a long, even drag on monthly savings. |
| **6. The Indian Financial Year is the projection's calendar** | The financial year runs April to March, because that's the calendar on which household income is earned, taxed and remembered in India. Income, taxes, expense step-ups, the yearly step-up in your monthly investment and the cashflow display are all aligned to it. Within a year, income is treated as level — because that's how a salary actually works. |
| **7. The horizon runs to your retirement date or your latest goal, whichever is later** | The projection always runs at least to your planned retirement date, and further when a goal is dated after it — so a late goal is never dropped. Salary income and contributions continue to the end of the projection: the plan doesn't stop your income at retirement age. Retirement is funded only through a Retirement goal you add, which the pool pays on its date like any other goal, and the feasibility verdict scores the goals you've explicitly set. One-off outflows scheduled beyond the plan's end are dropped from the projection, and the plan records a warning. |

We are a goal-planning approach — not a tax calculator, not a portfolio-construction engine, and not a debt-counselling tool. We surface honest numbers for the questions we were built to answer, and flag the limits clearly when asked to do more.

## How a projection is built — at a high level

Every Ask PI plan moves through eight deliberate, auditable stages. We can walk through the reasoning behind any of them on demand.

### Stage 1 — Profile

We lift your household snapshot into the projection: starting corpus, income, tax rate, monthly expenses and your monthly investment. The required inputs (date of birth, income, monthly expenses) must come from you: until they're filled in, we ask for them rather than guess. Optional inputs you leave blank take a standard value (tax rate, retirement age) or count as zero (cash, shares, other debts). **The starting corpus is your total investments** — your linked mutual-fund portfolio (or, until one is linked, the portfolio value you've entered) *plus* the directly-held shares and the cash and debt holdings on your profile, *less* any debts other than a home loan. Other assets such as gold or unlisted shares are not part of it. It is usually larger than the portfolio figure on your dashboard, so we're careful to call it your total investments rather than your portfolio. The projection then models that combined pool as one; there is no separate year-by-year forecast for the portfolio, the shares or the cash on their own, so we won't tell you what any single component will be worth in a given year. We anchor today's date and the planning horizon. The return, inflation and income-growth assumptions are house assumptions, held in one place and applied the same way for every customer — no hidden magic numbers in the inner stages. The one you can tailor is a goal's own inflation rate, set on the goal.

### Stage 2 — Retirement

We fix your planned retirement date: from your Retirement goal's target year when you have one, otherwise the retirement age on your profile, otherwise a standard retirement age. That date sets the minimum length of the projection. Retirement is funded only through a Retirement goal you add — the amount you set in today's rupees, inflated to its date and paid from the shared corpus like any other goal. The plan doesn't compute a separate retirement-corpus target of its own, and it doesn't stop salary income at the retirement date.

### Stage 3 — Existing mortgages

For any property you already own with an active loan, we take the EMI and end date you give us rather than reverse-engineering them, add up the EMIs that fall in each financial year, and spread that total evenly across the year's months as an outflow in the cashflow.

### Stage 4 — Goal properties

A property you *want* to buy is a cash purchase: its price, inflated at the goal's own inflation rate to the goal date, comes out of the corpus in full on that date. The plan doesn't capture a down-payment or a new home loan for a future purchase.

### Stage 5 — Goals table

We bring every active goal on your goals list that is dated in the future — property, education, retirement and your own goals — into a single table with shared fields: today's value, future value, the corpus required, the horizon-appropriate return, and the present value of what's needed, so every goal is compared on the same footing. Each goal is inflated at the rate stored on the goal: a standard house rate filled in when the goal is created, which you can change on the goal. The plan doesn't currently vary the inflation rate by goal type. Two goals with the same name are planned once.

### Stage 6 — Cashflow projection

We walk every month from today to the end of the horizon, producing a row per month: income (growing year over year across the horizon), taxes, living costs (stepped up with inflation), mortgage EMIs, savings before and after EMIs, and any one-off flows.

### Stage 7 — Funding: the shared corpus pool

This is the heart of the plan: one pool, walked forward month by month. The opening balance carries from the prior month. Your monthly investment goes in — the amount you've set, stepped up each financial year and never more than that month's savings, or a default share of the month's savings if you haven't set one; savings you don't invest aren't counted. If a month's savings are negative, the gap is drawn from the corpus. The corpus grows at its horizon-appropriate return from the first full financial year: the months left in the current financial year establish your starting position, with no return assumed on them. Goal payouts and one-offs are paid as they fall due, and any month that comes up short splits the shortfall proportionally across that month's outflows. A corpus that runs negative earns nothing, and no borrowing cost is charged on it.

### Stage 8 — Summary

We aggregate into two views: a headline status (today's corpus, what's required, the surplus or shortfall, the projected end-of-horizon balance, and the feasibility verdict) and a fund-flow reconciliation that ties opening balance, contributions, returns, inflows, outflows and goal payouts to the closing balance — every rupee accounted for.

## Exploring and changing your plan in chat

- **What-if questions are live but hypothetical.** Ask "what if I retire at 50?", "what if I invested ₹50,000 more a month?", "what if my expenses rose to ₹3 lakh a month?", or "can I afford a ₹10 lakh trip next year?" and we re-run the full eight-stage projection with that one change applied — but the result is never saved. If the change is missing a number ("what if I retire earlier?"), we ask one short question first. A retirement-age what-if moves your planned retirement date, which sets how far the projection runs; it doesn't move the date of a Retirement goal you've set or stop your salary, so your goal verdicts usually stay the same.
- **Milestones come straight from the projection.** Ask "when will I reach ₹1 crore?" and the answer is the first financial year in which your total investments cross that figure — or that you're already there, or that the projection doesn't get there.
- **A goal-planning what-if is never saved.** It is a look, not a commitment. To change your plan durably, update your profile or goals — the plan regenerates from the updated inputs, and it also refreshes when a new statement arrives and at most once a day so the starting corpus tracks your latest portfolio value. The one investment decision that does flow into the plan is a monthly SIP you set up through an investment recommendation, in chat or on the Invest page: that amount becomes the monthly investment your plan projects.
- **We don't let you shop for a rosier assumption.** Return rates, inflation and income growth are not explorable this way — only inputs you actually control (when you retire, how much you invest or spend, a specific one-off expense) can be changed.

## Why a customer should trust this approach

| Question | Our answer |
| --- | --- |
| **Why does my Retirement goal look so large?** | The plan funds your Retirement goal at its *future* cost: the amount you set in today's rupees, inflated to the goal's date. Over a horizon of decades, inflation multiplies that figure several times, so the future-rupee amount the corpus has to pay is far larger than the number you entered. The plan doesn't add a retirement target of its own on top — the goal you set is the figure it funds. |
| **I have a shortfall today — but you say my plan is feasible. How?** | The two numbers answer different questions. The present-value view asks whether today's corpus alone could fund everything if you stopped contributing now. The canonical view asks whether everything works *given your future contributions, income growth and one-offs*. Young, high-saving clients almost always show a present-value shortfall but full feasibility — their future contributions do the heavy lifting. |
| **Will my goal scheduled after retirement still be funded?** | It stays in the plan. The projection runs out to your latest goal, with income, contributions and corpus growth modelled all the way to it, and the goal is paid from the projected corpus on its date. If you've added a Retirement goal, the pool pays it on its own date, so a later goal is funded from what remains after it. Because the plan doesn't stop salary income at retirement age, a goal dated after retirement is assessed as if your earnings continue to that date. One-off outflows past the plan's end are dropped from the projection, and the plan records a warning. |
| **A goal a few years away — shouldn't it earn long-term returns?** | We assign returns by horizon, and a few years out is treated as medium-term, not long-term. We deliberately don't let a goal near a boundary "pick the better band" — the time math has to stay consistent so the today ↔ future-value pair reconciles. |
| **Why didn't you split corpus equally across my goals in a shortfall?** | We keep a single shared corpus, not per-goal balances — that's how real household money works. When a period under-funds, the shortfall is split *proportionally* to each outflow's size. Goal-priority routing would be a deliberate policy change, not the default. |
| **Why isn't my contribution showing tax savings?** | This plan applies a single tax rate — the income-tax slab rate on your tax profile, or a standard rate if you haven't given one — to your gross income, and nothing more. Tax-shield effects on investments live in the allocation approach, not here — keeping the projection deterministic and avoiding double-counting between the two. |
| **What if my income jumps in a few years — does the plan know?** | No. Year-on-year income growth is applied uniformly, and your saved plan doesn't capture one-off events — a bonus, a salary jump, a property sale or a maturity isn't part of it. In chat you can test a one-off expense as a what-if — e.g. "can I afford a ₹10 lakh trip next year?" — and get a real answer on the spot; one-off inflows can't be tested this way. |

## What this thesis is — and is not

This document is a directional reference. It is not a prediction, not a guarantee of returns, and not a substitute for the actual plan, which is always personalised. The plan does not model: portfolio rebalancing or allocation drift (see the Rebalancing thesis), tax-shield effects on contributions, per-goal earmarking, debt-cost accrual on a negative corpus, a retirement-corpus target of its own or the end of salary income at retirement (retirement is funded only through a Retirement goal you add), a down-payment and loan for a future property purchase, or one-off events and other mid-projection life events that aren't in your saved inputs. The assumptions, inflation rates and return bands behind it are reviewed periodically and may evolve. When they do, this thesis is updated and dated.

---

*Ask PI · Cashflow & Goal-Planning Thesis v1.4 · Owner: Investment Research · Cycle: reviewed quarterly · last reconciled with production wiring 2026-10-03*
