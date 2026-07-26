# Portfolio vs Nifty 50 Benchmark (money-weighted) — Design Spec

**Date:** 2026-06-05
**Status:** Approved design, pending spec review
**Scope:** Part 2 of 2. Builds the customer-vs-Nifty-50 comparison (the line chart data + headline numbers) on top of Part 1 (the `index_tri_history` TRI store).

---

## 1. Goal

Tell a customer **whether their actual money has out- or under-performed Nifty 50**, over time and as a single number. The judgment is about *their outcome including their timing of contributions* — not whether their funds beat the index. That makes the metric **money-weighted**.

## 2. The metric (decided)

Run **two portfolios off the exact same cashflows**:
- **Customer portfolio:** their real transactions → units × real fund NAV.
- **Nifty clone:** every customer cashflow (same ₹, same date) redirected into Nifty 50 TRI.

Express two outputs:
- **Line (over time):** cumulative money-weighted return at each day *t*:
  `return%(t) = (value(t) − invested_to_date(t)) / invested_to_date(t)`, for both portfolios.
- **Headline (single number, as-of today):** XIRR for customer vs Nifty clone.

Both are money-weighted, so they answer the same question and never contradict each other. The gap between the two lines *is* the out/under-performance.

**Rejected alternatives:** Time-Weighted Return (answers "are my funds good", ignores timing — not the question); annualized-XIRR-as-a-line (volatile, unintuitive as a series).

## 3. What we reuse (do NOT rebuild)

| Need | Reuse |
|---|---|
| XIRR solver | `financial_primitives.xirr.xirr(cashflows, guess=0.1)` |
| Customer XIRR headline | `app/domains/mutual_funds/services/xirr_service.compute_portfolio_xirr` |
| Cashflow + switch rules | `xirr_service._build_cashflows` logic (BUY/SELL signed; SWITCH_IN/OUT cancel at portfolio grain; DIVIDEND_REINVEST adds units, zero cashflow) |
| Per-fund NAV | `mf_nav_history` via `nav_history_service` |
| TRI | Part 1 `index_tri_service` / `index_tri_history` |

## 4. Per-point mechanics (approved)

Walk a daily timeline from **first purchase date → as_of**:

- **units_held(t)** per scheme: step function from transactions (BUY/SWITCH_IN/DIVIDEND_REINVEST add units; SELL/SWITCH_OUT remove).
- **customer_value(t)** = Σ over schemes `units_held(scheme, t) × nav_on_or_before(scheme, t)`.
- **invested_to_date(t)** = Σ external cashflows with date ≤ t (BUY +amount, SELL −amount; **switches excluded** — internal money movement, same rule as portfolio XIRR).
- **Nifty clone units(t):** for each external cashflow (amount, date d): BUY → `nifty_units += amount / TRI(d)`; SELL → `nifty_units -= amount / TRI(d)` (proportional). **benchmark_value(t)** = `nifty_units(t) × TRI_on_or_before(t)`.
- **return%(t)** computed for both lines from value(t) and invested_to_date(t).

The series is cumulative **since first purchase**. A `horizon` (1M/1Y/3Y/MAX) optionally clips the returned x-window; values stay cumulative-from-inception. (Whether to *rebase* shorter windows to 0 at the window start is a presentation tweak to settle during notebook prototyping; default = no rebase.)

**Edge cases:**
- `invested_to_date(t) == 0` (before first buy): return% undefined → omit those days.
- Scheme with no NAV row on/before t: contributes 0 to value that day, logged (mirrors existing xirr_service behavior).
- Missing TRI on/before d (impossible post-backfill since TRI starts 1999): skip that cashflow's clone units, log.

## 5. Architecture

```
notebooks/benchmark_prototype.ipynb                         🆕 prototype + eyeball the series
app/domains/portfolio/services/benchmark_service.py         🆕
```

**Two layers in `benchmark_service.py`:**

1. **Pure core (no DB) — unit-testable:**
   ```
   build_comparison_series(
       txns: list[TxnLite],                       # (date, type, amount, units, scheme_code)
       nav_lookup: Callable[(scheme: str, on: date) -> float | None],   # nearest <= on
       tri_lookup: Callable[(on: date) -> float | None],                # nearest <= on
       *, as_of: date, horizon: str = "MAX",
   ) -> ComparisonResult
   ```
   `ComparisonResult` = `{ dates: list[date], customer_pct: list[float], benchmark_pct: list[float],
   summary: { customer_xirr, benchmark_xirr, customer_value, benchmark_value, invested, as_of } }`.
   Customer/benchmark XIRR computed via `financial_primitives.xirr` on the respective cashflow sets
   (customer = actual; benchmark = same dates/amounts, terminal = nifty_units × TRI(as_of)).

2. **DB adapter — `compute_portfolio_vs_nifty(db, user_id, *, horizon, as_of=None)`:**
   - `list_transactions(db, user_id)` → map to `TxnLite`.
   - Bulk-load `mf_nav_history` per held scheme over [first_purchase, as_of] and `index_tri_history` for NIFTY 50 over the same window; build in-memory **nearest-on-or-before step lookups** (one query each, not per-day).
   - Call the pure core; return `ComparisonResult`.

Keeping the math pure (lookups injected as callables) makes it testable with synthetic data and reusable.

**Out of scope:** the HTTP router/endpoint and any UI. We produce the service + validated logic; wiring an endpoint is a later, trivial step if desired.

## 6. Notebook (prototype-first, per user)

`notebooks/benchmark_prototype.ipynb`:
- Load the live TRI (reuse the validated CSV `nifty50_tri_full.csv` or `index_tri_service`).
- Load a sample of real `MfTransaction` rows (or hand-built sample if DB not populated) + relevant `mf_nav_history`.
- Implement and **eyeball** the cumulative-return series + XIRR headline; confirm the curve behaves sensibly and the customer-vs-Nifty gap reads correctly.
- Once locked, port the math verbatim into the pure core.

## 7. Testing / success criteria

Pure-core unit tests (synthetic txns + injected lookups, no DB, no network):
1. **Single lump sum:** one BUY; customer return%(t) equals `nav(t)/nav(buy) − 1`; benchmark equals `TRI(t)/TRI(buy) − 1`.
2. **SIP (monthly buys):** invested_to_date steps up correctly; return% uses money-weighted denominator; series length matches timeline.
3. **Buy then partial SELL:** units_held and nifty_units both drop proportionally; no crash; invested/realised correct.
4. **Customer beats benchmark:** construct NAV path > TRI path → customer_pct ends above benchmark_pct and `customer_xirr > benchmark_xirr`.
5. **Customer lags benchmark:** mirror of #4.
6. **Pre-first-buy days excluded:** no days with invested_to_date == 0 in output.
7. **XIRR reuse:** customer summary XIRR matches `financial_primitives.xirr` on the same cashflows (sanity equality).

Notebook validation: curve over a real sample looks correct and matches the intent of the "portfolio vs benchmark" view.

## 8. Assumptions & decisions

- Money-weighted (not TWR) — decided: the question is "did *you* beat Nifty," timing included.
- Benchmark = customer's identical cashflows redirected to Nifty 50 TRI (Gross TRI, `tri_value`).
- Switches excluded from external cashflows at the portfolio grain (matches existing portfolio XIRR).
- Series cumulative since first purchase; horizon clips x-window; rebasing deferred to notebook.
- Service only (no router/UI) in this scope.
- Notebook lives in `Prozpr_Backend/notebooks/`.
