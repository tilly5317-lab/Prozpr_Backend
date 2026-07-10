# Portfolio Analysis — real TWR for the Returns tab

**Date:** 2026-06-13
**Status:** Design — pending implementation (audited; decisions folded in below)
**Scope:** Backend (new service + endpoint) and frontend (rewire the Returns tab).

## Problem

The Returns tab of Portfolio Analysis (`Prozpr_Frontend/src/components/dashboard/PortfolioAnalysisModal.tsx`) shows three things — a portfolio TWR number, a Nifty 50 TWR number, and a two-line chart — and **all three are synthetic**:

- TWR = `total_gain_percentage` × a hardcoded per-range fudge factor (`rangeScaleFactor`: 1M→0.12, 1Y→0.75, …).
- Nifty 50 TWR = that (already fake) number × `0.85`.
- The chart = `synthCurve()`, a sine/cosine wobble over an interpolated line.

The backend never computes a real Time-Weighted Return. The raw materials, however, already exist:

| Ingredient | Source | Status |
|---|---|---|
| Daily portfolio market value (`V_t`), one row per calendar day | `UserPortfolioNavHistory.total_value` (built by `networth_history_service`) | ✅ |
| External cashflows (BUY/SELL) | `MfTransaction` ledger | ✅ |
| Nifty 50 Total Return Index, daily | `IndexTriHistory` (index_name `"NIFTY 50"`, `tri_value`) | ✅ |
| TWR computation | — | ❌ build it |

## Goal

Replace the synthetic Returns tab with real numbers: a true time-weighted return, the Nifty 50's own total return as the benchmark, and a chart driven by both — across the existing 6 ranges (1M / 3M / YTD / 1Y / 3Y / All).

**Scope of the number — mutual funds only.** The daily value series (`UserPortfolioNavHistory`) is built purely from `mf_transaction` + `mf_nav_history`. Equities and other non-MF holdings never enter it, even though the dashboard's headline `total_value` sums all holdings. So this TWR reflects the **mutual-fund portion** of the portfolio. This matches the sibling **Value Build-Up** tab, which uses the same MF-only series — so the two tabs are consistent. The UI carries a quiet "Mutual funds" scope label so the number is honest about what it covers.

### Non-goals (explicitly out of scope)

- **Broadening the series to equities / other non-MF holdings.** Would require a whole equity daily-valuation pipeline (`equity_transaction` × `equity_price_history`) — a separate, much larger piece of work.
- **Payout dividends.** The transaction enum has `DIVIDEND_REINVEST` but no `DIVIDEND_PAYOUT`. For an IDCW fund that pays cash out, `total_value` dips on the ex-dividend day with no recorded cashflow, so TWR shows a small spurious loss. Pre-existing data-model limitation (already affects the net-worth history); not addressed here.
- **Annualized / CAGR returns.** TWR is shown as the **cumulative** compounded return over the selected window only.
- **New ranges, a new chart component, or changes to `benchmark_service`** (money-weighted — a different number we leave untouched).
- **No DB schema changes / no migration.** We read three existing tables.
- **Triggering the net-worth backfill job** from this endpoint. If a user's daily history isn't built yet, show the empty state; the build job is started elsewhere.

## The math

TWR removes the effect of *when* money was added or withdrawn. With daily portfolio value `V_t` and net external cashflow `C_t` on each day (BUY → `+abs(amount)`, SELL → `−abs(amount)`):

```
daily return   r_t = (V_t − C_t) / V_{t-1} − 1
wealth index   W_t = W_{t-1} × (1 + r_t)        anchored W = 1.0 on day one
```

**Why this is correct for MF pricing.** Each transaction stores `units`, `nav`, and `amount` together (`mf_transaction.py`), so `amount = units × nav` at the transaction.
- BUY day: `V_t = (existing + new) units × NAV_today = existing×NAV_today + amount`, so `(V_t − amount)/V_{t-1} − 1` is the pure return on existing holdings; new money doesn't pollute it.
- SELL day: `networth_history_service` removes the sold units *before* valuing, so `V_t` is the remaining units and `C_t = −proceeds`; `(V_t + proceeds)/V_{t-1} − 1` adds the withdrawn value back.
- SWITCH_IN/OUT and DIVIDEND_REINVEST are **not** external cashflows (`C_t` excludes them) — units move or grow internally, captured by `V_t`.

**Cashflow sign comes from the transaction type, magnitude from `abs(amount)`.** CAMS stores redemption units (and sometimes amounts) with quirky signs; the rest of the codebase defends with `abs()` and lets the type drive direction (`networth_history_service`, `benchmark_service`). We do the same: `+abs(amount)` for `BUY`, `−abs(amount)` for `SELL`.

**Cashflow timing:** end-of-day (subtract `C_t` from `V_t`). Standard for daily-valued TWR; at daily granularity the start-of-day alternative differs negligibly.

**Cashflow source must be the ledger, not `total_invested`.** `UserPortfolioNavHistory.total_invested` reduces by *average cost* on a sell (`networth_history_service.py`), not by proceeds — using it would corrupt every sell. We derive `C_t` from `MfTransaction.amount`.

**Re-entry / divide-by-zero guard.** When `V_{t-1}` is ~0 (first day, or a full exit followed by re-entry), there is no prior base: set `r_t = 0` (carry `W` flat) and let that day's investment seed a fresh base. Linking resumes the next day.

**Nifty 50 TWR.** The TRI is itself a total-return wealth index, so we normalize it to 1.0 at the same inception: `nifty_index_t = TRI_t / TRI_inception`. Both lines become "growth of ₹1," directly comparable. If no TRI exists on/before a given date, `nifty_index` for that point is `null` (a guard so the endpoint can't crash for portfolios older than our TRI history — no special UI handling beyond not drawing the missing segment).

**Range rebasing (client-side).** Because `W` is a multiplicative index, the compounded return over *any* sub-window is `W_end / W_start − 1`. The frontend picks the window's first point and divides — no per-range server logic. A range longer than the available history (e.g. "3Y" with 1Y of data) clamps to inception by starting at index 0.

## Backend

### New service: `app/domains/portfolio/services/twr_service.py`

Mirrors the `benchmark_service` shape: a pure, unit-testable core plus a thin async DB adapter. **Imports** `build_step_lookup`, `EXTERNAL_IN_TYPES`, `EXTERNAL_OUT_TYPES`, and `NIFTY_INDEX_NAME` from `benchmark_service` (deliberate reuse — these are shared cashflow/benchmark facts; duplicating them risks drift).

**Pure core:**
```
def compute_twr_wealth_index(
    daily_values: list[tuple[date, float]],   # (recorded_date, total_value), ascending
    daily_cashflows: dict[date, float],        # net external cashflow per date (+in / -out)
) -> list[tuple[date, float]]:                 # (date, W_t), W anchored 1.0 at first day
```
Algorithm: iterate days in order; `c = daily_cashflows.get(d, 0.0)`; if `prev_value is None or prev_value <= EPS` then `r = 0.0` else `r = (v - c)/prev_value - 1`; `W *= (1 + r)`; append `(d, W)`; `prev_value = v`.

**DB adapter:**
```
async def compute_twr_series(db, user_id) -> TwrSeriesResponse
```
1. Load `UserPortfolioNavHistory` rows for the user, ascending → `daily_values`.
2. Load `MfTransaction` rows; build `daily_cashflows`: `+abs(amount)` when `transaction_type.value in EXTERNAL_IN_TYPES`, `−abs(amount)` when in `EXTERNAL_OUT_TYPES`.
3. Load Nifty TRI rows (`index_name == "NIFTY 50"`); `tri_lookup = build_step_lookup(...)`. `baseline = tri_lookup(first_value_date)`.
4. `W = compute_twr_wealth_index(...)`. For each day build a point with `portfolio_index = W_t` and `nifty_index = tri_lookup(d)/baseline` (or `null` if either missing).
5. `has_data = len(points) >= 2` (TWR needs at least two valued days — single source of truth for "renderable").

### New endpoint: `GET /portfolio/twr`

In `app/domains/portfolio/routers/portfolio_router.py`, auth consistent with the other portfolio routes (effective user). Returns the full daily series since inception:

```python
class TwrPoint(BaseModel):
    date: date
    portfolio_index: float          # growth-of-1 index, 1.0 at inception
    nifty_index: float | None       # Nifty 50 TRI normalized to 1.0 at inception; null if no baseline

class TwrSeriesResponse(BaseModel):
    has_data: bool                  # len(points) >= 2
    points: list[TwrPoint]
```
Schema lives with the other portfolio schemas. No `range` param — the frontend slices.

## Frontend

### `Prozpr_Frontend/src/lib/api.ts`

Add the `TwrSeriesResponse` / `TwrPoint` types and `getPortfolioTwr(): Promise<TwrSeriesResponse>` → `request<TwrSeriesResponse>("/portfolio/twr")`. Following existing patterns.

### `PortfolioAnalysisModal.tsx`

- **Remove** `synthCurve`, `rangeScaleFactor`, the `0.85` Nifty multiplier, and the `total_gain_percentage`-derived TWR.
- **Fetch** the series when the modal opens (loading / error / empty states alongside the existing modal patterns).
- **Per selected range:** map the range to a cutoff date, find the first point with `date >= cutoff` (All → index 0) as `start`. Then:
  - headline portfolio TWR = `portfolio_index_end / portfolio_index_start − 1`
  - headline Nifty TWR = `nifty_index_end / nifty_index_start − 1` (when present)
  - chart series point `i`: `twr = (portfolio_index_i / portfolio_index_start − 1) × 100`, `bench_nifty50 = (nifty_index_i / nifty_index_start − 1) × 100` (omit when `nifty_index` is null)
- **Render the daily series directly** — no downsampling (see caveat below).
- **Feed the existing chart** the real `{ i, twr, bench_nifty50 }` points — chart component unchanged.
- **Scope label:** a quiet "Mutual funds" tag near the heading, so the number is honest about covering only the MF portion.
- **Empty state** when `has_data` is false (single flag): "Not enough history yet — import your transactions to see your returns."

## Edge cases

| Case | Behavior |
|---|---|
| Fewer than 2 valued days | `has_data: false` → empty state |
| First day (no prior value) | `r = 0`, `W = 1.0` anchor |
| Full exit then re-entry (`V_{t-1} ≈ 0`) | `r = 0`, fresh base; no divide-by-zero |
| Non-trading day (value carried forward, no cashflow) | `r = 0`, index flat |
| Range longer than history | starts at index 0 (clamps to inception) |
| No Nifty TRI on/before a date | that point's `nifty_index = null`; benchmark segment not drawn |

## Known caveats / accepted limitations

These were reviewed in the 2026-06-13 audit and consciously accepted (not fixed in this pass):

1. **Cashflow-to-day matching assumes a contiguous daily series.** Cashflows are matched to value-days by exact date. The series is normally one row per calendar day from inception, so every transaction day has a row — but if it goes **stale** (e.g. new transactions imported before the history is rebuilt) a cashflow on a date with no row would be silently dropped, spiking that day's return. *Remedy if it ever bites:* attribute each value-day every cashflow in `(prev_date, current_date]` (a no-op when the series is dense). Not implemented now.
2. **Transaction-day NAV-basis mismatch.** `C_t` uses the transaction's NAV (`amount = units × txn.nav`) while `V_t` uses the published NAV from `mf_nav_history`. When they differ (NAV not published for that exact date, carry-forward, rounding), a sub-basis-point error leaks into that one day. Negligible — Phase A backfills NAV back to each fund's first transaction date.
3. **Long-range chart render.** The "All" range can be ~1,800 daily points × 2 lines in an animated modal; it may stutter on open. *Remedy if it bites:* display-only downsampling (pick every k-th point, keep the last; headline still uses exact endpoints). Not implemented now — render daily as-is.
4. **MF-only scope** (see Goal) — equities/other holdings are excluded; consistent with the Value Build-Up tab; surfaced via a "Mutual funds" label.

## Test plan (test-first on the pure core)

Unit tests for `compute_twr_wealth_index` (no DB):
1. **No cashflows:** values `[100,110,121]` → `W = [1.0, 1.1, 1.21]`.
2. **Mid-period contribution leaves TWR unchanged:** 100 → 110 (+10%), +100 contribution (W flat), → 231 (+10%) ⇒ `W_end = 1.21` (21%), proving cashflow timing doesn't distort it (where MWR would differ).
3. **Sell doesn't distort:** a mid-period partial sell links to the same `W`.
4. **Re-entry:** full sell to 0, idle days, re-buy ⇒ no crash, index carried flat then resumes.

Adapter/endpoint:
5. Seed `UserPortfolioNavHistory` + `MfTransaction` + `IndexTriHistory` for a user → assert `points`, `nifty_index` normalization, and `abs(amount)` sign handling (a SELL with a negative stored `amount` still reduces).
6. User with <2 valued days → `has_data: false`.

(Per backend `CLAUDE.md`: sqlite tests create only the tables under test, e.g. `await conn.run_sync(UserPortfolioNavHistory.__table__.create)`.)

### Success criteria

- Pure-function unit tests (1–4) pass.
- Endpoint tests (5–6) pass.
- `grep` confirms `synthCurve` / `rangeScaleFactor` / the `0.85` multiplier are gone from the frontend.
- Running the app: the Returns tab shows real figures (with the "Mutual funds" label) for a user with history, and the empty state for one without.

## Audit log (2026-06-13)

1. TWR scope is MF-only → accept + "Mutual funds" label.
2. Cashflow drop on a gapped/stale series → documented as a known risk (caveat 1), not fixed.
3. Cashflow sign → use `abs(amount)` with sign from transaction type.
4. `has_data` → defined as `len(points) >= 2` in the backend; frontend checks the one flag.
5. Transaction-day NAV-basis mismatch → documented caveat (2), not fixed.
6. Long-range chart points → render daily as-is; documented caveat (3), no downsampling.
7. Shared helpers/constants → imported from `benchmark_service` (reuse, not duplicate).
