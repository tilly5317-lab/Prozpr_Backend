# 2-Year Portfolio Lifecycle Simulation — Design

**Date:** 2026-07-12 · **Status:** Draft (v3 — reuse-only architecture)
**Deliverable location:** `AI_Agents/lifecycle_sim_testing/` (dev-only; not imported by runtime)

> **Hard constraint (user):** reuse the existing rebalancing / SIP / lumpsum logic; write **no new
> logic except goal withdrawals**. v3 honors this by reusing the dev bridges (`build_request`,
> `_build_input`) and the engines verbatim. Consequence: the tax short/long-term split is the dev
> bridge's **fixed 60/40 approximation** (not real per-lot aging), so there is **no lot ledger** —
> the portfolio is tracked per-fund. (v1/v2 history: an 8-agent audit found 3 blockers + ~12 majors
> in a from-scratch adapter design; the reuse-only approach sidesteps most of them by construction.)

## 1. Purpose
Simulate how a Prozpr customer's mutual-fund portfolio evolves over 24 months under a realistic
advice cadence, driving the **real** `Rebalancing` and `additional_investment` engines at each step,
and render the result as a single interactive HTML report (one tab per profile).

Headline question: **what trades does the customer transact over two years — how many buys / sells /
exits at each follow-up rebalance, how the monthly SIP deploys, and how goal withdrawals and a
one-time lumpsum reshape the portfolio.** Build & validate on **Mohammed Faisal** first, then run
the identical harness across all five profiles.

## 2. Scope
**In scope:** a generic, profile-parameterized month-by-month state machine (m0–24); per-fund value
tracking; deterministic growth (equity 12% / debt 6% / gold 8% p.a., monthly-compounded); goal
withdrawals in the exact due month (debt→gold→equity); 5 rebalances (m0,6,12,18,24), 24 SIPs (m1–24),
1 lumpsum (m14); one self-contained `lifecycle_2y.html` with a tab per profile.

**Non-goals:** no lot ledger / real ST-LT aging (the reused bridge synthesizes the split); no LLM
calls; no changes to any production/agent module; no web/DB integration.

**Pool model** — `total_value` = four pools: **per-fund MF holdings** (rebalanced + SIP'd/lumpsum'd);
**direct_equity** (`non_mf_equities`, grows @12%, and **can be trimmed** by the engine's
`SELL_DIRECT_STOCKS` action); **elss** (`tax_efficient_equities`, grows @12%, frozen); **cash**
(starts 0, 0% growth, absorbs undeployed contributions + un-reinvested rebalance proceeds so no rupee
is created/lost).

### Reuse boundary (what we reuse vs. what we build)
**Reused verbatim — zero new financial/translation logic:**
- Engines: `run_rebalancing`, `run_additional_investment`, `run_practical_allocation`, `run_allocation`.
- Rebalancing input: `Rebalancing/Testing/Master_testing/bridge.py` — `build_request`, `synth_holdings`,
  `load_ranking`, `load_rejection_reasons`, `load_force_exit_isins`, `rank1_lookup`.
- Additional-investment input: `additional_investment/Master_testing/runner.py` — `_build_input`,
  `_funding_status`, `load_ranked_funds`.

**Hand-written (orchestration + state + the one allowed new behavior — no engine/translation logic):**
- The 24-month timeline that sequences the reused calls (`simulate.py`).
- Per-fund portfolio state carried between calls + monthly growth + the direct/elss/cash pools
  (`portfolio.py`). Arithmetic only.
- Applying each engine's **output** back onto the state (set fund = `final_holding_amount`; append
  `buys`; route residuals to cash). Mechanical application of the engine's decisions — not decisions.
- Per-period input refresh via `model_copy` (update corpus scalars + decrement goal horizons) —
  mirrors the production one-liner `practical_input.model_copy(update={"total_corpus": …})`
  (`app/domains/rebalancing/services/rebal_engine/input_builder.py:421`).
- **Goal withdrawals** (§8) — the only genuinely new behavior; no engine redeems to fund a goal.
- Growth rates + HTML report (sim inputs/outputs, not engine logic).

## 3. Inputs & fixed assumptions
Profiles from existing fixtures (`PROFILES`, `PRACTICAL_PROFILES`, `synth_holdings`).

| Knob | Value | Notes |
|---|---|---|
| Equity / Debt / Gold growth | 12% / 6% / 8% p.a. | monthly-compounded; applied per fund by asset class |
| Cash growth | 0% | uninvested drag, reported |
| SIP amount | `round((annual_income/12 − monthly_household_expense) × 0.70)` | Faisal ≈ ₹20k; Aarav ₹70k; Lakshmi ₹7k; Neha ₹80k; Harpreet ₹1.45L |
| Lumpsum | ₹5,00,000 fixed (all profiles), month 14 | |
| Rebalance / SIP months | 0,6,12,18,24 / 1–24 | |
| Tax ST/LT split | dev bridge's fixed **60% LT / 40% ST**, cost 0.85 (LT) / 0.95 (ST) | reused from `bridge.py`; realized STCG/LTCG are **indicative, not exact** |
| Force-exit seeding | replace one seed holding with a **low-rated fund (`fund_rating < 5`)** | `HoldingRecord.fund_rating` flows through `build_request`; `fund_rating<5` trips the engine's rating-based exit |
| Equity/debt/gold map | `*_equities`+`multi_asset`+ELSS+direct→equity 12%; `short_debt`/`long_debt`/`arbitrage*`→debt 6%; `gold_commodities`→gold 8%; unknown→assert & fail | drives growth rate + debt→gold→equity withdrawal order |
| Ranking CSV | single shared `prozpr_fund_ranking_june_2026_v2.csv` | fed to BOTH `load_ranking` and `load_ranked_funds` so SIP-mirror ISINs match rebalancing BUYs |
| net_financial_assets | bumped to `total_value()` each rebalance | preserves fixtures' NFA==total_corpus; feeds the direct-equity NFA-band cap |
| Idle cash | held at 0%, reported; never auto-swept | keeps money-conservation clean |
| Goal withdrawal tax | pre-tax (report realized gain, no tax cash-out) | matches engine's informational-tax treatment |

**Goals ≤24 mo (withdrawals fire here):** Faisal — Emergency ₹3L @m6. Neha — Business buffer ₹20L
@m18. Lakshmi — Medical ₹15L @m6, Retirement ₹80L @m12, Home ₹50L @m24 (expect a shortfall on the
last, re-derive from growth). Aarav — Car ₹12L @m24 (shortfall). Harpreet — none.

## 4. Architecture & file layout
`AI_Agents/lifecycle_sim_testing/`
- `constants.py` — §3 knobs; loads the single ranking CSV once (shared by both reused loaders).
- `portfolio.py` — per-fund `Holding` list + `direct_equity`/`elss`/`cash` scalars: `grow`, `sell`
  (debt→gold→equity, cost pro-rated), `apply_buys`, `set_from_rebalance`, and value/subgroup/class
  queries incl. `total_value()`, `to_holding_records()` (→ `bridge.HoldingRecord`),
  `current_value_by_subgroup()`.
- `engines.py` — **thin wrappers over the reused functions**: `rebalance(...)` (`to_holding_records`
  → `build_request` → `run_rebalancing`); `sip(...)` / `lumpsum(...)` (`run_practical_allocation` →
  `_funding_status` → `_build_input` [+ `model_copy` to attach the SIP mirror] →
  `run_additional_investment`); `refresh_alloc_input(...)` (`model_copy`: corpus + decremented goals).
- `simulate.py` — the month-by-month state machine → `SimulationResult`.
- `report.py` — `SimulationResult[]` → one self-contained `lifecycle_2y.html`.
- `run.py` — bootstraps `AI_Agents/src` onto `sys.path`, loops all 5 profiles, writes the HTML.

## 5. Data model
**`Holding`** (per fund): `isin, asset_subgroup, sub_category, fund_name, fund_rating, is_recommended,
asset_class ∈ {equity,debt,gold}, present_inr, cost_inr`. Growth multiplies `present_inr` by the
class factor; `cost_inr` (running sum of amounts invested) is fixed except pro-rata reduction on
sells — used only for withdrawal realized-gain reporting (the rebalancing tax split comes from the
bridge, not from this). No purchase dates / lots.

**`Portfolio`** = `list[Holding]` + `direct_equity_value` + `elss_value` + `cash`.
`total_value() = Σ present_inr + direct_equity_value + elss_value + cash`.

## 6. Month-by-month state machine (core)
**Seed (m=0):** `run_allocation(profile)` → `synth_holdings(...)` → rescale so `Σ present ==
total_corpus − non_mf_equity_corpus − elss_corpus` (tradable MF only); set `direct_equity_value =
non_mf_equity_corpus`, `elss_value = elss_corpus`, `cash = 0`; **replace one seed holding's
`fund_rating` with < 5** so an EXIT fires. Assert `total_value() == total_corpus`. Run **Rebalance #1**
(reused `build_request`+`run_rebalancing`); apply output; cache its BUY-ISIN map. No growth/SIP at m0.

For `m = 1 … 24`, strict order:
1. **Grow** — each fund `present_inr ×` its class monthly factor; grow direct_equity (12%) & elss (12%); cash ×1.
2. **Goal withdrawal** (if due at `m`): raise `amount_needed` from MF holdings, **debt→gold→equity**,
   cost pro-rated; value leaves `total_value` (pre-tax; realized gain reported). Frozen pools not
   raided; MF-exhaustion → shortfall flag.
3. **Rebalance** (if `m ∈ {6,12,18,24}`): `refresh_alloc_input` → reused `build_request` +
   `run_rebalancing` → apply (see §7); **replace** the cached BUY-ISIN map.
4. **Lumpsum** (if `m == 14`): reused `_build_input` (LUMPSUM, deficit-fill) + `run_additional_investment`; apply buys.
5. **SIP** (every month): reused `_build_input` (SIP_MONTHLY) + SIP mirror + `run_additional_investment`; apply buys.

Snapshot each month `{month, mf, direct, elss, cash, total, equity, debt, gold}`; log every event
with its trade/buy list + engine totals. Zero-trade rebalances are valid (empty BUY map → SIP rank-1 fallback).

## 7. Engine adapters (all thin wrappers over reused code)
### 7.1 Rebalance
`portfolio.to_holding_records()` → `list[HoldingRecord]`; call the reused
`build_request(profile_refreshed, alloc_out, holdings, ranking, rejection, force_exit_isins)` →
`run_rebalancing(request)`. `profile_refreshed` = `refresh_alloc_input` (corpus scalars set to current
pool values incl. `net_financial_assets=total_value()`; goal horizons decremented, ≤0 dropped —
required since `Goal.time_to_goal_months` has `ge=1`).

**Apply (bookkeeping):** for each `response.rows` fund, set the ledger fund's `present_inr =
final_holding_amount` (pro-rate `cost_inr` on any decrease; append a new holding for a cap-spill buy
into a previously-unheld fund). Then `direct_equity_value −= SELL_DIRECT_STOCKS.amount_inr` from
`trade_list`. Route the exact conservation residual to `cash`: `cash += (MF_before + direct_before) −
(MF_after + direct_after)` (≥0 = un-reinvested proceeds). Rebalance is thus value-neutral **by
construction**. Record `response.totals` + `trade_list` verbatim. **Tax is informational — never
deducted from holdings.** Extract the SIP mirror: `{subgroup: [isin,…]}` from `trade_list` where
`action=='BUY'`, grouped by `asset_subgroup`, ordered by `amount_inr` desc.

### 7.2 SIP / Lumpsum
`alloc = run_practical_allocation(profile_refreshed)`; `short/medium_fulfilled = _funding_status(alloc)`;
`ranked = load_ranked_funds(CSV)`. Call the reused `_build_input(alloc.aggregated_subgroups, ranked,
amount, cadence, short_fulfilled, medium_fulfilled, current_value_by_subgroup=…)`:
- **SIP:** `amount = monthly SIP`, `cadence=SIP_MONTHLY`, `current_value_by_subgroup=None`; then
  `inp = inp.model_copy(update={"rebal_buy_isins_by_subgroup": cached_mirror})` to activate the mirror.
- **Lumpsum:** `amount = 5_00_000`, `cadence=LUMPSUM`, `current_value_by_subgroup = current MF per
  subgroup` (triggers deficit-fill); `alloc` recomputed with `total_corpus` and `mf_corpus` bumped by
  the lumpsum (per `_build_input`/production wiring).

**Apply (bookkeeping):** contribution enters `cash`; each `FundBuy.amount_inr` moves cash → a fund's
`present_inr`+`cost_inr` (new holding if unheld); `undeployed_inr` stays as cash; if rounding
over-deploys, clamp against cash (never negative). Report `deployed_inr` (not nominal) in metrics.

## 8. Goal withdrawals (only new logic)
At the due month, raise `amount_needed` from MF holdings in **debt → gold → equity** order (within a
class, largest holdings first), decrementing `present_inr` and pro-rating `cost_inr`, dropping zeroed
holdings. Realized gain (`Σ present − cost` of sold portions) reported; **pre-tax**. Withdrawn cash
leaves `total_value`. Frozen pools stay frozen even under shortfall; MF-exhaustion → shortfall flag.

## 9. Output HTML
Single self-contained `lifecycle_2y.html`, no external assets, **tab bar of the 5 profiles**. Each tab:
header (name/age/risk/regime/corpus/SIP + goals with due-months, in-window highlighted); headline
metrics (total trades; buys/sells/exits per rebalance; total SIP **deployed**; lumpsum deployed; goal
withdrawals + shortfall; realized STCG/LTCG + tax est. [informational]; end cash; start vs end value);
value curve (monthly equity/debt/gold/total incl. cash, inline SVG, **per-tab independent y-axis, zero
floor**); event timeline (rebalance cards → `TradeAction` table + totals; SIP per-6-month block,
expandable; lumpsum/withdrawal cards). SIP buys that fell back to rank-1 (empty mirror) not labeled
"matches your rebalancing plan".

## 10. Verification & invariants
- **Exact money conservation** (asserted monthly): `total_value(m) == grown_value(m) + contribution(m)
  − withdrawal(m)`, where `grown_value` applies per-class factors (12/6/8/12/12/0%) to the prior
  snapshot, `contribution` = nominal SIP(+lumpsum) into cash, `withdrawal` = value that left. Rebalances
  contribute 0 (residual → cash). Tolerance = rupee rounding.
- **Reconciliation:** per-fund `present_inr` after apply == engine `final_holding_amount` (± rounding).
- **Determinism:** strip/pin both `computed_at` and `request_id` (uuid4) before rendering/diffing.
- **CSV coverage:** assert every subgroup the allocation produces over the horizon has a rank-1 fund
  in the pinned CSV (else money silently under-deploys → cash).
- **Faisal smoke first**, then re-verify conservation on Aarav/Neha/Harpreet (SELL_DIRECT_STOCKS and
  the NFA band are zero for Faisal, so they'd otherwise hide in the smoke).

## 11. Build order
1. `constants.py` + `portfolio.py` — per-fund state + pools + grow/sell/apply; unit-check conservation.
2. `engines.py` — thin wrappers; validate one reused rebalance (incl. a SELL_DIRECT_STOCKS profile) + one SIP on Faisal.
3. `simulate.py` — Faisal month loop; reconciliation + conservation asserts pass.
4. `report.py` — HTML for Faisal.
5. `run.py` — sweep all 5; re-verify Aarav/Neha/Harpreet; combined HTML.

## 12. Decisions (resolved)
Reuse-only (dev bridge, fixed-ratio tax, no lot ledger) · lumpsum ₹5,00,000 @m14 · seed one low-rated
fund for the exit path · gold 8% · idle cash held at 0% and reported · goal withdrawals pre-tax ·
`AI_Agents/lifecycle_sim_testing/` added to `.gitignore` (dev-only) unless you say otherwise.
