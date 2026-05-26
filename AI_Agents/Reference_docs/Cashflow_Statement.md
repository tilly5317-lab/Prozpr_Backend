# Ask Tilly · Cashflow & Goal-Planning Thesis

*Why we project the way we do — and what the corpus picture is really telling you*
*Engine version 1.0 · Internal & client reference · Last updated: May 2026*

---

> **Status note for follow-up questions:** This document is the client-facing thesis for the cashflow / goal-planning engine that lives in `ailax/Prozpr_Backend/AI_Agents/src/cashflow_statement/`. The conventions, time math, ROI bands, and feasibility definitions quoted below match the live engine as of v1. The developer-facing reference doc `cashflow_statement/methodology.md` (alongside the code) is the source of truth for formulas, default values and per-stage detail. Anything in this doc framed as "intent" or "what this *isn't*" is thesis context — for engine-true behaviour cross-check `engine/` (`profile.py`, `retirement.py`, `properties.py`, `goals_table.py`, `cashflow.py`, `funding.py`, `summary.py`).

---

## The one-line thesis

A financial plan should answer two questions honestly: *will every goal you've set get its money on time?* and *will your corpus survive the journey through retirement?* We build a year-by-year, month-by-month projection of your household's money — income, taxes, expenses, EMIs, SIPs, one-offs, goal payouts — from today to the end of your retirement year, run it against a single shared corpus pool, and report not just whether it works, but where and why it doesn't. The engine is deterministic and pure — every rupee in the output traces back to a documented formula, and the same input always produces the same plan.

## Seven principles that drive every projection

| Principle | What it means in practice |
| --- | --- |
| **1. One shared corpus pool, not per-goal earmarking** | Real households don't keep separate piggy banks for each goal — money is fungible. The engine maintains a single corpus that walks month by month: opening balance + monthly investment + ROI + one-off inflows − goal payouts − one-off outflows = closing balance. When a month runs short, the shortfall is *split proportionally* across that month's outflows rather than starving one goal to feed another. This is the only honest way to model what actually happens to a real plan when capital gets tight. |
| **2. Day-precise time math, symmetric in both directions** | Inflation to FV and discounting back to PV-today use the *same* day-precise exponent — `(eomonth(target_date) − today).days / 365`. No FY-boundary jumps, no rounding to whole years. A goal 2 years and 9 months out is unambiguously in the mid-term band (not "round down to near-term"). The cool consequence: every PV ↔ FV pair in the engine reconciles back to itself, which is what lets us show you "₹X today equals ₹Y at goal date" without the two numbers ever drifting apart. |
| **3. Expected ROI is a 3-band horizon lookup, not a single number** | A goal one year out cannot earn the same expected return as a goal twenty years out — pretending it can is the single most common modelling lie. We use three bands keyed to *day-precise* horizon: 5% near-term (today → today + 2y), 7% mid-term (next 3y), 9% long-term (beyond). The same band drives the per-month corpus growth rate during funding, so a long-dated portfolio compounds at long-term ROI for the years it has, then mid, then near, as time runs out. |
| **4. Two feasibility views, one canonical** | The engine surfaces two top-line numbers that *look* like they answer "is the plan feasible?" but answer different questions. The canonical one — `is_feasible` — asks: *given your full plan (income, taxes, expenses, SIPs, EMIs, one-offs, goal payouts), does every goal get its money on time AND does the corpus end up non-negative?* The PV view (`surplus_or_shortfall_today`) asks: *if you stopped all SIPs today and earned each goal's expected ROI on today's corpus alone, would there be enough?* The PV view often disagrees with the canonical view — and that's the point: it explains *how* the plan works (mostly from SIPs vs. mostly from existing corpus) rather than restating the verdict. |
| **5. Mortgage purchases are upfront + EMI stream, never a balloon** | When a goal property is bought on mortgage, the corpus pays only the *upfront* on the goal date; the remaining principal becomes a monthly EMI flowing through the cashflow projection until the loan is paid off. EMI = `pmt(monthly_rate, tenure_months, mortgage_amount)` with a monthly rate of `annual / 12` (matches Indian banking convention). This means the same property goal can be either a cliff-like corpus drain (cash purchase) or a long, even drag on monthly savings (mortgage) — and the engine shows you both shapes correctly rather than averaging them. |
| **6. The Indian Financial Year is the projection's calendar** | The FY runs April → March; `fy_for_date` returns the *closing* year (April 2026 → FY27). Income, taxes, expense step-ups, and the cashflow display are all FY-aligned, because that is the calendar on which household finance is actually planned, taxed and remembered in India. Within a FY, growth is applied annually (not compounded monthly) — your income on the 1st of January equals your income on the 1st of February of the same FY, because that is how salary actually works. |
| **7. The horizon ends at retirement, and we say so** | The customer-facing question we are answering is "can you retire at the planned age?" — not "what happens at age 90?". Goals or one-off events scheduled after retirement are dropped from the projection with an explicit warning. We do this because continuing past retirement produces stuck-corpus noise (no income, no SIPs, retirement corpus already paid out as a goal) that adds no information and a lot of false precision. The retirement-corpus lump sum *itself* is the way we represent the post-retirement years, computed as a present-value annuity at the real rate. |

We are a goal-planning engine — not a tax calculator, not a portfolio-construction engine, and not a debt-counselling tool. We surface honest numbers for the questions we were built to answer, and we flag the limits clearly when we are asked to do more.

## How a projection is built — in eight deliberate stages

Every Ask Tilly plan is the output of eight sequential, auditable stages, each one file under `engine/`. We can walk through any of them on demand.

### Stage 1 — Profile

Lift the household snapshot into the run context: starting corpus = `financial_assets − financial_liabilities_excl_mortgage`, annual income, blended effective tax rate, monthly household expense, current SIP. Anchor today's date and the projection horizon. Capture the assumption registry (inflation rates, ROI bands, growth rates) — every default is overridable per call, no hard-coded magic numbers in the inner stages.

### Stage 2 — Retirement

For each retirement scenario, build a `RetirementSnapshot`:

- Compute the retirement date from `dob + retirement_age` (or the explicit override).
- Inflate today's annual expense to the retirement date using day-precise math.
- Apply the **Fisher real rate** = `(1 + ROI_retired_portfolio) / (1 + inflation_household_expense) − 1` (currently ≈ 2.83% at 9% ROI / 6% inflation).
- Solve the annuity-PV: how big a corpus, paying inflated expenses for `lifespan − retirement_age` years at the real rate, lasts to the assumed lifespan. Round to ₹1000.
- If the client provides a PV-today override, inflate it to FV instead and use that.
- Back-discount the FV to PV today for display alongside the FV target.

### Stage 3 — Existing mortgages

For every property the client already owns with an active loan, lay out the monthly EMI from today to the user-provided `mortgage_end_date`. We trust the customer's EMI — no reverse-derivation from rate and principal. EMI rows feed straight into the cashflow stage as fixed outflows.

### Stage 4 — Goal properties

For each property the client *wants* to buy:

- **Cash purchase**: the full FV-on-goal-date price exits the corpus on `goal_date`. The corpus must have it.
- **Mortgage purchase**: an upfront amount (specified as `downpayment_pct` of FV, or as a today-rupee `upfront_amount` to be inflated) exits the corpus on `goal_date`; the remaining `mortgage_amount = max(target_fv − upfront_fv, 0)` becomes an EMI stream for `mortgage_tenure_years` (default 20 years at 7.5%).

### Stage 5 — Goals table

Combines retirement + property goals + custom goals into a single unified table with shared fields: PV value, FV value, `corpus_required_fv`, inflation rate, expected ROI (the 3-band lookup), and PV-today of the corpus requirement. `corpus_required_fv` is the *upfront-only* for mortgaged properties (the EMI lives in the cashflow), and the *full goal value* for everything else (cash properties, custom goals, retirement).

### Stage 6 — Cashflow projection

Walk every month from today to the retirement-FY end, producing a row per month with: income (stepped up annually at `annual_income_growth`, zeroed post-retirement), income tax (income × `effective_tax_rate`, zeroed post-retirement), household expense (stepped up at `inflation_household_expense`), existing-mortgage EMI, goal-mortgage EMI, savings pre- and post-EMI, one-off in / out. Months strictly after the retirement month are truncated.

### Stage 7 — Funding: the shared corpus pool

This is the heart of the engine. One pool, walked month by month:

1. `corpus_opening` from last month's `corpus_closing`.
2. Decide `monthly_investment` via a 4-branch rule: post-retirement → 0; user-set SIP capped at savings; no user SIP but savings positive → 75% of post-EMI savings; savings negative → full negative savings (a withdrawal).
3. Apply monthly ROI at `(1 + band_ROI)^(1/12) − 1`, clamped at 0 when corpus is negative (no debt-interest accrual — we do not pretend negative corpus earns or owes a portfolio-style rate).
4. Pay out any goal whose month matches, and any one-off outflows.
5. If the month is under-funded, split the shortfall proportionally across that month's outflows — each goal / one-off gets `shortfall × its_amount / total_outflow` attributed.

### Stage 8 — Summary

Aggregates into two views.

- **HeadlineStatus**: `corpus_today`, `total_corpus_required_today` (sum of `investment_required_pv` — the PV bar), `surplus_or_shortfall_today`, `corpus_closing` (the canonical end-of-horizon number), `total_shortfall_fv`, `total_funded_amount`, and `is_feasible`.
- **FundFlowSummary**: the bridge — `corpus_opening + total_investments + total_roi + total_one_off_in − total_one_off_out − total_goals_paid = corpus_closing`. Every rupee accounted for.

## Why a customer should trust this approach

| Question | Our answer |
| --- | --- |
| **Why is my retirement corpus so high?** | It is the lump-sum-today equivalent of paying inflated annual expenses for `lifespan − retirement_age` years post-retirement, at the *real* rate (nominal ROI minus inflation, Fisher equation). At 6% inflation and 9% nominal ROI, the real rate is ≈ 2.83% — so the corpus has to be roughly 25–30× the inflated annual expense for a 25-year retirement. The two levers that move this number most are inflation and the post-retirement portfolio ROI. |
| **I have a shortfall today — but you say my plan is feasible. How?** | The two numbers answer different questions. `surplus_or_shortfall_today` is a PV view: *if you stopped SIPs today and lived only off today's corpus growing at the goal's ROI, would there be enough?* `is_feasible` is the canonical end-of-horizon view: *given everything — corpus, SIPs, income growth, one-offs — does every goal get funded and does the corpus end non-negative?* Young, high-saving clients almost always show PV-shortfall but end-of-horizon feasibility; the SIP capacity does the heavy lifting. |
| **Why did you ignore my goal that was after retirement?** | The projection horizon ends at the retirement-FY end. The question we are built to answer is "can you retire at the planned age?", and continuing past retirement produces stuck-corpus noise rather than information. The dropped goal is reported as a warning, never silently. If you need a longer horizon, push the retirement-age forward. |
| **A goal 2 years and 9 months away should earn long-term returns, no?** | At day-precise math, that goal lands in the *mid-term* band (default 7%), not long-term. The bands are: today → today + 2y near-term, then + 3y mid-term, then long-term. We deliberately do not let a goal close to a boundary "pick the better band" — the math has to stay symmetric for the PV / FV pair to reconcile. |
| **Why didn't you split corpus equally across my goals when there was a shortfall?** | The engine maintains a single shared corpus, not per-goal balances — that is how real household money works. When a month under-funds, the shortfall is split *proportionally* to each outflow's size, not equally and not by priority. If you want goal-priority routing, that is a deliberate change in policy, not a default in the engine. |
| **Why isn't my SIP showing tax savings (e.g. 80C / ELSS)?** | The engine uses `effective_tax_rate` only on gross income, not on the SIP itself. Tax-shield effects on investments live in the allocation engine, not the cashflow engine. This keeps the projection deterministic and avoids double-counting between modules. |
| **What if my income jumps in FY30 — does the engine know?** | Not unless you tell it. Year-on-year income growth follows `annual_income_growth` (default 8%) uniformly. A specific event — bonus, salary jump, property sale, maturity, medical event — should be entered as a `one_off_inflow` or `one_off_outflow` on the exact date. One-offs are not inflated; the rupee amount is the actual rupees on the day. |

## What this thesis is — and is not

This document is a reference. It is not a prediction, not a guarantee of returns, and not a substitute for the actual plan, which is always personalised. The engine does not currently model: portfolio rebalancing or allocation drift (see the Rebalancing thesis), tax-shield effects on SIPs, per-goal SIP earmarking, debt-cost accrual on negative corpus, or mid-projection life events that aren't in the input. Defaults, inflation rates, and ROI bands are reviewed periodically and may evolve. When they do, this thesis is updated and dated.

---

*Ask Tilly · Cashflow & Goal-Planning Thesis · Engine v1.0 · Owner: Investment Research · Cycle: reviewed quarterly*
