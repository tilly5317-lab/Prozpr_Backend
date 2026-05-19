# Goal Planning Methodology

This document explains how the `cashflow_statement` module turns a client's profile, goals, and assumptions into a multi-year financial-plan projection. It is a reference for both developers (to understand the engine) and customer-facing chat surfaces (to answer "how is this calculated?" questions).

The engine is pure Python and deterministic. Every rupee in the output traces back to a documented formula.

---

## Overview

The engine takes a `GoalPlanningInput` and produces a `GoalPlanningOutput`. The plan answers two questions:

1. **Is each goal funded?** — Per-goal status (funded / partially / unfunded) with rupee shortfalls.
2. **Is the household's cashflow viable through retirement?** — Year-by-year corpus evolution, ending with a closing-corpus figure at retirement.

The horizon runs from today to the end of the retirement financial year. Goals or one-off events scheduled after retirement are dropped from the projection with a warning — the engine's question is "is retirement feasible at the planned age?", not "what happens post-retirement?".

The engine runs in 8 stages, executed in order. Each stage is one Python file under `engine/`.

| Stage | File | Produces |
|---|---|---|
| 1. Profile | `profile.py` | `RunContext`: corpus, FY anchors, growth/ROI rates |
| 2. Retirement | `retirement.py` | `RetirementSnapshot`: required corpus at retirement (FV + PV-today) |
| 3. Existing mortgages | `mortgages.py` | Per-FY EMI outflows for each existing home loan |
| 4. Goal properties | `properties.py` | FV target, upfront, mortgage schedule for each property goal |
| 5. Goals table | `goals_table.py` | Unified list: retirement + property + custom goals |
| 6. Cashflow | `cashflow.py` | Monthly income / tax / expense / EMI / savings rows |
| 7. Funding | `funding.py` | Walks the corpus pool month by month, pays out goals |
| 8. Summary | `summary.py` | Headline numbers + fund-flow bridge |

---

## Inputs

A `GoalPlanningInput` is composed of seven sections.

### Profile (`ClientProfile`)

| Field | Meaning |
|---|---|
| `annual_income` | Gross annual income in ₹ |
| `effective_tax_rate` | Average post-deduction blended income-tax rate as fraction (e.g., 0.22 = 22%). NOT the marginal slab rate |
| `financial_assets` | Total financial assets in ₹ (excludes real-estate equity) |
| `financial_liabilities_excl_mortgage` | Non-mortgage liabilities in ₹ |
| `monthly_household_expense` | Today's monthly household expense in ₹ |
| `starting_monthly_investment` | Current monthly SIP in ₹ (optional — if absent, SIP is derived from savings) |

**Starting corpus** = `financial_assets − financial_liabilities_excl_mortgage`.

### Retirement (`RetirementInput`)

| Field | Default | Meaning |
|---|---|---|
| `date_of_birth` | required | Client's DOB |
| `retirement_age` | 60 | Planned retirement age |
| `assumed_lifespan_years` | 85 | Used to compute post-retirement drawdown years |
| `retirement_date_override` | None | If set, overrides DOB + retirement_age |
| `retirement_corpus_pv_today_override` | None | Optional corpus target in TODAY's ₹. The engine inflates it to retirement-date FV |

### Existing properties (`CurrentProperty[]`)

For each existing property the client owns:

| Field | Meaning |
|---|---|
| `name` | Display name (must be unique across all inputs) |
| `property_value` | Current market value in ₹ (optional, not consumed by the engine yet) |
| `has_mortgage` | Whether the property has an active home loan |
| `mortgage_emi` | Current monthly EMI in ₹ (required if `has_mortgage=True`) |
| `mortgage_end_date` | Date of the final EMI payment (required if `has_mortgage=True`) |

The engine trusts user-supplied EMI and end-date. No reverse-derivation from principal/rate — the customer knows their own EMI.

### Goal properties (`GoalProperty[]`)

Property purchases the client wants to make. Two paths:

**Cash purchase**: `is_downpayment_only=False`. The full property price is paid out of corpus on `goal_date`.

**Mortgage purchase**: `is_downpayment_only=True`. The client pays an upfront amount out of corpus on `goal_date`; the rest becomes a mortgage with monthly EMIs flowing from `goal_date` for `mortgage_tenure_years`.

| Field | Meaning |
|---|---|
| `name` | Display name |
| `target_pv` and/or `target_fv` | Property price; at least one is required |
| `inflation_annual` | Per-property override; defaults to `assumptions.inflation_property` |
| `is_downpayment_only` | True for mortgage path |
| `upfront_amount` | Downpayment in today's ₹ (mortgage path; XOR with `downpayment_pct`) |
| `downpayment_pct` | Downpayment as fraction of FV property price, 0.0–1.0 (mortgage path; XOR with `upfront_amount`) |
| `goal_date` | When the property is bought |
| `mortgage_tenure_years` | Defaults to `assumptions.default_mortgage_tenure_years` (20) |
| `mortgage_interest_annual` | Defaults to `assumptions.default_mortgage_interest_annual` (7.5%) |

### Custom goals (`CustomGoal[]`)

Non-property life goals (education, marriage, retirement-corpus override, generic).

| Field | Meaning |
|---|---|
| `name` | Display name |
| `goal_type` | One of: `child_abroad_education`, `child_local_education`, `child_marriage`, `custom`. (`property` reserved for property goals, `retirement` for retirement.) |
| `goal_value_pv` and/or `goal_value_fv` | Goal value; at least one is required |
| `goal_date` | Target date |
| `inflation_rate_override` | Per-goal override; defaults to the rate keyed by `goal_type` |

### One-off cashflows (`OneOffEvent[]`)

Inflows (`one_off_inflows`) and outflows (`one_off_outflows`) at specific dates. Examples: bonus, property sale, medical event. **No inflation is applied** — the amount is the actual rupees at that date. Pre-inflate yourself if the client says "in today's money".

### Assumptions (`Assumptions`)

Canonical registry of every client-overridable financial default. Pass a custom `Assumptions(...)` instance to override any of these per call. Anything not in this table (algorithm tuning, model names, system safety caps, etc.) is engineering config and lives local to the relevant module.

| Field | Default | Where it's consumed |
|---|---|---|
| `inflation_property` | 6% | Property-goal FV inflation (fallback when `GoalProperty.inflation_annual` not set) |
| `inflation_child_abroad_education` | 8% | Custom goals of type `child_abroad_education` |
| `inflation_child_local_education` | 6% | Custom goals of type `child_local_education` |
| `inflation_child_marriage` | 6% | Custom goals of type `child_marriage` |
| `inflation_household_expense` | 6% | Retirement expense FV; real-rate for retirement-corpus PV; cashflow expense FY step-up; fallback for `GoalType.custom` |
| `inflation_post_retirement` | 6% | Reserved — not consumed by the engine today |
| `annual_income_growth` | 8% | FY step-up of income in the cashflow projection |
| `annual_invested_amount_growth` | 8% | FY step-up of the user's monthly SIP |
| `roi_near_term_post_tax` | 5% | Expected ROI for goals / months in the near-term band |
| `roi_mid_term_post_tax` | 7% | Expected ROI for goals / months in the mid-term band |
| `roi_long_term_post_tax` | 9% | Expected ROI for goals / months in the long-term band |
| `roi_retired_portfolio_annual` | 9% | Nominal ROI used in retirement-corpus PV math (feeds `real_rate(...)`) |
| `near_term_horizon_years` | 2 | Near-term band length: today → today + N years (day-precise) |
| `medium_term_horizon_years` | 3 | Mid-term band length: near_term_end → near_term_end + N years |
| `default_mortgage_tenure_years` | 20 | Goal-property mortgage tenure fallback when `GoalProperty.mortgage_tenure_years` not set; also injected into the extractor prompt as the default the LLM may fill in |
| `default_mortgage_interest_annual` | 7.5% | Goal-property mortgage rate fallback; also extractor prompt default |
| `default_property_downpayment_pct` | 20% | Extractor prompt default the LLM uses when the user mentions a mortgage without specifying a downpayment |
| `default_sip_share` | 75% | When the user has not set `starting_monthly_investment`, the engine invests this fraction of each month's post-EMI savings as SIP. (Withdraws full negative savings on negative months, regardless.) |

The Inflation Rates and Expected ROI Bands sections below give the methodology context for how these rates are applied; the table above is the canonical source for default *values*.

---

## Time Conventions

### Indian Financial Year (FY)

The Indian FY runs **April through March**. `fy_for_date(d)` returns the closing year (April 2026 → FY27). Each FY is identified by its end-date (March 31).

### Day-precise inflation and PV-discount

For property and custom goals, the inflation FV calculation uses a **day-precise exponent**:

```
N = (eomonth(goal_date) - latest_update_date).days / 365
target_fv = target_pv * (1 + inflation) ** N        # then rounded to nearest ₹1000
```

The PV-discount of goal payouts back to today uses the **same** convention:

```
investment_required_pv = corpus_required_fv / (1 + expected_roi) ** N
```

This means the FV-inflation exponent and the PV-discount exponent are symmetric — there are no FY-boundary jumps.

### Retirement inflation

Retirement uses the same day-precise convention as the rest of the engine: `inflation_years = (eomonth(retirement_date) − today).days / 365`. The same exponent is used to (1) inflate today's expenses to retirement-date FV, (2) inflate a user PV override to FV, and (3) back-discount the FV corpus to PV today.

### Rounding

All FV cashflow anchors (`corpus_required_fv`, `target_fv`, `upfront_fv`, retirement corpus) are rounded to the nearest ₹1000 using half-away-from-zero. Display-only PV fields (e.g., `goal_value_pv`) are unrounded.

---

## Inflation Rates (defaults)

| Rate | Default | Applied to |
|---|---|---|
| `inflation_property` | 6% | Property goal FV (fallback when `GoalProperty.inflation_annual` not set) |
| `inflation_child_abroad_education` | **8%** | Custom goals of type `child_abroad_education` |
| `inflation_child_local_education` | 6% | Custom goals of type `child_local_education` |
| `inflation_child_marriage` | 6% | Custom goals of type `child_marriage` |
| `inflation_household_expense` | 6% | (1) Inflates annual expense to FV at retirement; (2) feeds `real_rate` for retirement-corpus math; (3) FY step-up of expense in cashflow projection; (4) fallback for `GoalType.custom` |
| `inflation_post_retirement` | 6% | Reserved input — not currently consumed by the engine |

Per-instance overrides bypass the table:

- `GoalProperty.inflation_annual` — overrides `inflation_property` for that property
- `CustomGoal.inflation_rate_override` — overrides the type-keyed rate for that goal

---

## Expected ROI Bands

The engine uses a 3-band horizon-based expected ROI:

| Band | Default | Default Range (today + N years) |
|---|---|---|
| Near-term | 5% | 0–2 years |
| Mid-term | 7% | 2–5 years |
| Long-term | 9% | 5+ years |
| Retired portfolio | 9% | Post-retirement (used in the retirement-corpus calc only) |

Band cutoffs are **day-precise**: `near_term_end = today + 2y`, `medium_term_end = near_term_end + 3y` (exact-date arithmetic, no FY rounding).

A goal's expected ROI is the band its `goal_date` falls into. This same band drives the per-month corpus growth rate during the funding stage — the corpus grows at near-term ROI for months up to `near_term_end`, then mid-term, then long-term.

---

## Stage Walk-throughs

### Stage 2: Retirement

For each retirement scenario, the engine produces a `RetirementSnapshot`:

1. **Retirement date.** `dob + retirement_age` (with `retirement_date_override` winning if set).
2. **Annual expense at retirement.** `monthly_household_expense * 12`, inflated by `inflation_household_expense` over day-precise years to `eomonth(retirement_date)`.
3. **Real ROI.** `real_rate(roi_retired_portfolio_annual, inflation_household_expense)` — the Fisher equation.
4. **Corpus required at retirement (FV).** Computed via annuity-PV math on `annual_expense_fv` over `(lifespan − retirement_age)` post-retirement years at real ROI. Then rounded to ₹1000.
5. **User override.** If `retirement_corpus_pv_today_override` is set, it is inflated to retirement-date FV and used instead of the computed value.
6. **PV today.** The "used" FV is back-discounted at `inflation_household_expense` to today's ₹ for display.

If the client is already retired (`retirement_date ≤ today`), the engine switches to the drawdown branch: `post_retirement_years = lifespan − current_age`, `years_to_retire = 0`, with a warning.

### Stage 4: Goal properties

For each `GoalProperty`:

1. **Inflate target to FV** at `goal_date` using day-precise math (see Time Conventions).
2. **If `is_downpayment_only`**:
   - `upfront_fv = round1000(target_fv × downpayment_pct)` or `round1000(inflate(upfront_amount, inflation, N))`.
   - `mortgage_amount = max(target_fv − upfront_fv, 0)`.
   - Build a mortgage schedule: `EMI = pmt(monthly_rate, tenure_months, mortgage_amount)` where `monthly_rate = annual_interest / 12` (simple, matches Indian banking).
3. **Else** (cash purchase):
   - `corpus_required_fv = target_fv` (full price out of corpus on `goal_date`).
   - No mortgage.

### Stage 5: Goals table

Combines retirement, property goals, and custom goals into a single table with shared fields: `goal_value_pv`, `goal_value_fv`, `corpus_required_fv`, `inflation_rate`, `expected_roi`, `investment_required_pv`.

**`corpus_required_fv` semantics**: For mortgaged property goals, this is the upfront only — EMIs flow separately through the cashflow. For everything else (cash properties, custom goals, retirement), it is the full goal value at goal date.

**`investment_required_pv`**: PV-today of `corpus_required_fv`, discounted at the goal's expected_roi over day-precise years.

### Stage 6: Cashflow projection

Walks every month from today to the retirement-FY end, producing a `MonthlyCashflowRow` with:

- **Income**: `annual_income * (1 + annual_income_growth)^i / 12` where `i` is the FY index (0 for current FY). Step-up annually, not monthly compounded. Zeroed for months after `retirement_date`.
- **Income tax**: `income * effective_tax_rate`. Also zeroed post-retirement.
- **Household expense**: `annual_household_expense * (1 + inflation_household_expense)^i / 12`. NOT zeroed post-retirement (the retirement-corpus drawdown handles those, but in the lump-sum model they sit in the truncated tail).
- **Existing mortgage EMI**: per-FY EMI sum divided evenly across the FY's display months.
- **Goal mortgage EMI**: same.
- **Savings pre-EMI / post-EMI**: derived.
- **One-off in / out**: amounts from `OneOffEvent` lists, matched by `(year, month)`.

The projection ends at the retirement-FY end; months strictly after the retirement month are truncated before the funding stage.

### Stage 7: Funding (shared corpus pool)

This is the heart of the engine. One shared corpus pool — not per-goal balances — walks the monthly cashflow:

For each month `m`:

1. **`corpus_opening`** = `corpus` carried over from last month (or `ctx.corpus` for month 0).
2. **Decide monthly investment** via the 4-branch rule (`monthly_invest_or_withdraw`):
   - Post-retirement (`m > retirement_date`): zero.
   - User specified an SIP and savings can support it: grown SIP, capped at `savings_post_emi`.
   - No user SIP, savings positive: `savings_post_emi × sip_share` (default `sip_share = 0.75`).
   - Else (savings negative): `savings_post_emi` (a negative number — i.e., withdrawal).
3. **Monthly ROI**: `corpus_opening × ((1 + band_ROI)^(1/12) − 1)`, clamped at 0 when corpus is negative. The band is the same 3-band horizon lookup as the goals.
4. **Outflows this month**:
   - Goal payouts: any goal whose `(goal_date.year, goal_date.month) == (m.year, m.month)` pays out `corpus_required_fv`.
   - One-off outflows: any event in the same month.
5. **`corpus_closing`** = `corpus_opening + monthly_investment + roi + one_off_in − goal_payouts − one_off_out`.
6. **Shortfall split**: if the total outflow exceeds available money (`corpus_opening + investment + roi + inflow`), the shortfall is split proportionally across all outflows in that month — each goal / one-off gets `shortfall × (its_amount / total_outflow)` attributed to it.

The final `corpus_closing` is the headline closing-corpus number.

### Stage 8: Summary

Aggregates into two views:

- **HeadlineStatus**: top-line numbers. `corpus_today`, `total_corpus_required_today` (sum of `investment_required_pv` across goals — the PV-today feasibility metric), `surplus_or_shortfall_today` (corpus minus PV-required), `corpus_closing` (end-of-horizon view), `total_shortfall_fv`, `total_funded_amount`.
- **FundFlowSummary**: the horizon bridge — `corpus_opening + total_investments + total_roi + total_one_off_in − total_one_off_out − total_goals_paid = corpus_closing`.

---

## Outputs

### `HeadlineStatus`

| Field | Meaning |
|---|---|
| `years_to_last_goal` | FY span from today to the last goal/event |
| `last_goal_date` | Date of the latest goal or one-off outflow |
| `number_of_goals` | Count |
| `corpus_today` | Starting corpus |
| `total_corpus_required_today` | Sum of `investment_required_pv` across all goals — the PV-today bar |
| `surplus_or_shortfall_today` | `corpus_today − total_corpus_required_today`. Negative = shortfall today |
| `corpus_closing` | End-of-horizon corpus after all goals paid |
| `total_shortfall_fv` | Sum of `shortfall_fv` across goals — total FV gap |
| `total_funded_amount` | Sum of `funded_amount` across goals |

### Per-goal status (`GoalFundingStatus[]`)

| Field | Meaning |
|---|---|
| `name`, `goal_type`, `goal_date` | Goal identity |
| `goal_value_pv` | Full goal value in today's ₹ (unrounded) |
| `goal_value_fv` | Full goal value at goal date (rounded for properties; matches `corpus_required_fv` for non-property) |
| `corpus_required_fv` | What the corpus actually pays at goal date (= upfront for mortgaged property, else the full FV) |
| `investment_required_pv` | PV-today of `corpus_required_fv` at the goal's expected ROI |
| `funded_amount` / `is_funded` / `shortfall_fv` | Funding outcome |
| `expected_roi` | The 3-band ROI rate used |

### `AnnualCashflowRow[]` and `MonthlyCashflowRow[]`

Per-FY aggregates of the monthly cashflow. P&L columns are pure sums; corpus columns are: `corpus_opening` = first row of the FY, `corpus_closing` = last row, others are sums. `is_funded` is True only if every month in the FY was funded.

Monthly rows are returned only when `input.detail_level == "full"`.

### `FundFlowSummary`

The horizon-bridge view — every monthly investment, ROI accrual, and outflow rolled up. Reconciles `corpus_opening` to `corpus_closing` via the formula in the previous section.

### `GoalPropertyDetail[]`

Public-facing per-property details: target_pv, target_fv, corpus_required_fv, mortgage amount / tenure / interest / EMI / total-interest / payoff-date. Useful when a customer asks "what's my EMI on this house?".

---

## Feasibility — two views, one canonical

The engine surfaces two top-line numbers that each look like they could answer "is the plan feasible?". They answer different questions and **must not be confused**.

| Field | What it answers | Use it when |
|---|---|---|
| `HeadlineStatus.is_feasible` | Canonical end-of-horizon view: *"Given your full plan — income, taxes, expenses, SIPs, EMIs, one-offs, goal payouts — will every goal get its money on time AND will the corpus end up non-negative?"* (`all(goal.is_funded) AND corpus_closing >= 0`) | Any general question about plan feasibility. This is the default verdict the chat should report. |
| `HeadlineStatus.surplus_or_shortfall_today` | PV-view "as-of-today": *"If you stopped all SIPs today and earned each goal's expected ROI on every rupee of your current corpus, would you have enough to fund everything?"* (`corpus_today − total_corpus_required_today`) | Only when the customer explicitly asks "can I fund this from today's corpus alone?" or "as of today, am I able to fulfill my goals?". Never as a default feasibility verdict. |

**Why the two can disagree**: the PV view ignores SIPs and one-off inflows. A young client with small corpus but large SIP capacity often shows PV-shortfall but end-of-horizon feasibility. Conversely, an HNI with a big near-term goal can show PV-surplus but end-of-horizon shortfall (the near-term drain prevents the corpus from compounding enough to cover later goals).

**Mid-projection corpus dips**: `is_feasible=True` does NOT guarantee the corpus stays non-negative for every month in between — only that it ends non-negative and every goal payout was funded. If you need a stricter "never goes into debt" check, scan `monthly_cashflow` directly for `corpus_closing < 0` rows. The lever engine also uses the end-of-horizon rule, so a proposed lever is accepted as long as end-state is healthy, even if mid-projection corpus dips briefly.

## Sign and Magnitude Conventions

- **Shortfalls** are stored as **positive** magnitudes (`shortfall_fv > 0` means underfunded).
- **EMI**, **household expense**, **goal payouts**, **one-off outflows** are stored as **positive** magnitudes. The corpus formula subtracts them.
- **`monthly_investment`** is **signed**: positive when investing, negative when withdrawing (`savings_post_emi < 0` case).
- **`surplus_or_shortfall_today`** is **signed**: negative means a shortfall.

---

## FAQ

### Why is my retirement-corpus number so high?

Retirement corpus is the lump-sum-today equivalent of paying inflated annual expenses for `lifespan − retirement_age` years post-retirement. The math is a present-value annuity at the real rate (nominal ROI minus inflation, Fisher equation). Two levers move it most: `inflation_household_expense` (compounds the expense FV) and `roi_retired_portfolio_annual` (the discount rate during retirement). At 6% inflation and 9% nominal ROI, the real rate is `(1.09 / 1.06) − 1 ≈ 2.83%` — so the corpus has to be roughly 25-30× the inflated annual expense for a 25-year retirement.

### Why is a goal 2 years and 9 months away getting near-term ROI?

It isn't, after the day-precise band-cutoff change. Near-term is `today + 2y` exact; mid-term is `near_term_end + 3y` exact. A 2y-9m goal lands in mid-term (default 7%).

### Why does inflation use end-of-month for the goal date?

The funding stage lands goal payouts at the month-end of the goal-date's month — `eomonth(goal_date)`. Inflating to the same instant (and discounting back to today from the same instant) keeps the math symmetric.

### My goal is on March 31, 2030. Is that FY30 or FY31?

`fy_for_date(2030-03-31)` returns 2030. March 31 is the last day of FY30. April 1, 2030 is the first day of FY31. (Indian FY runs Apr-Mar.)

### Why does the engine drop my goal after retirement?

The projection horizon ends at the retirement-FY end. The customer-facing question is "is retirement feasible at the planned age?", read from `corpus_closing` at retirement. Continuing past retirement only produces stuck-corpus noise (no income, no SIPs, the retirement corpus already paid out as a goal). The engine emits a warning whenever a goal is dropped this way.

### What happens if the user provides a target FV directly (not PV)?

The provided FV is used as the cashflow anchor verbatim. For property goals it is rounded to ₹1000; for custom goals it passes through unrounded. The PV display field is reverse-derived for information only and is not used in any calculation.

### What happens if my mortgage outlasts the projection horizon?

The mortgage's EMI accrual is capped at horizon end. `mortgage_payoff_date` is computed as `goal_date + tenure_months` regardless — so the EMI / total-interest / payoff-date displayed in `GoalPropertyDetail` reflect the full mortgage life, but the cashflow rows only carry EMIs up to horizon.

### Why does "starting_monthly_investment = ₹100" behave the same as "not set"?

The funding rule treats `user_sip ≤ 100` as effectively zero (falls through to the savings-fraction branch). A trivial value would otherwise produce a near-zero SIP capped at savings — not what the user means. The threshold lives in `funding.py:monthly_invest_or_withdraw`.

### What is the SIP share constant (0.75)?

When the user has not set a SIP, the engine invests 75% of post-EMI savings each month. The default lives in `Assumptions.default_sip_share` and can be overridden per call.

### What is `inflation_post_retirement` for?

It is a reserved input field — defined on `Assumptions` with default 6% but not yet consumed anywhere in the engine. Future use will let the model use a different inflation rate during retirement years than during accumulation years (the current code conflates both under `inflation_household_expense`).

---

## What is **not** in the engine

- **Tax-shield effects** on SIPs (e.g., 80C savings, ELSS). The engine uses `effective_tax_rate` only on gross income, not on investments.
- **Asset-allocation drift / rebalancing**. The expected ROI is a single 3-band lookup, not a portfolio-construction output. The `Rebalancing` agent (separate module) handles that.
- **Per-goal SIP earmarking**. The corpus is a single shared pool; shortfalls are split proportionally. The funding stage does not track which rupee was earmarked for which goal.
- **Mid-projection life events** that aren't already in the input. If a client expects a salary jump in FY30, model it as a `one_off_inflow` on the relevant date — or override `annual_income_growth` to fit the trajectory.
- **Detailed mortgage amortization** (interest-vs-principal split per month). `GoalPropertyDetail` exposes `mortgage_total_interest` for the whole life, but the cashflow only carries the EMI total, not its components.
- **Debt cost on negative corpus.** When corpus goes negative (rare — starting net non-mortgage liabilities exceed financial assets, or mid-projection outflows exceed available funds), the engine does not accrue interest cost on that debt. The 3-band portfolio ROI is clamped at zero for negative corpus, and no separate debt-cost rate is applied. Customers in negative-corpus territory typically need debt counseling beyond goal-planning scope; the shortfall machinery (`is_funded=False`, `shortfall_fv`) already communicates the "you can't afford this plan" signal without needing a separately modeled debt-compound.
