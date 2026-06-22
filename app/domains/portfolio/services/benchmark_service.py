"""Customer portfolio vs Nifty-50 benchmark (money-weighted).

Produces the comparison *chart* — two cumulative money-weighted return series:
the customer portfolio and a Nifty-50 "clone" driven by identical cashflows —
plus the benchmark (Nifty-clone) XIRR.

Scope split (decided): the **customer** XIRR headline is NOT computed here; the
backend reuses ``mutual_funds.services.xirr_service.compute_portfolio_xirr`` for
that. This service owns only what is benchmark-specific + the time series.

The math core (``build_comparison_series``) is pure: it takes typed transactions
and ``nav_lookup`` / ``tri_lookup`` callables, so it is fully unit-testable. The
async DB adapter (``compute_portfolio_vs_nifty``) bulk-loads rows and builds the
lookups.
"""

from __future__ import annotations

import bisect
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financial_primitives.xirr import xirr

from app.domains.benchmarks.services.benchmark_data_service import (
    NIFTY_50_CODE,
    load_value_series,
)
from app.domains.mutual_funds.models import MfNavHistory, MfTransaction

# Horizon -> trailing days; MAX = since first purchase.
HORIZON_DAYS: dict[str, Optional[int]] = {
    "1M": 30,
    "1Y": 365,
    "3Y": 365 * 3,
    "MAX": None,
}

# Transaction-type handling (string values of MfTransactionType).
ADD_UNIT_TYPES = {"BUY", "SWITCH_IN", "DIVIDEND_REINVEST"}  # increase units held
REMOVE_UNIT_TYPES = {"SELL", "SWITCH_OUT"}  # decrease units held
EXTERNAL_IN_TYPES = {"BUY"}  # external money in (+invested)
EXTERNAL_OUT_TYPES = {"SELL"}  # external money out (-invested)
# SWITCH_IN/OUT are internal at the portfolio grain: units move, no external cashflow,
# no effect on the Nifty clone. DIVIDEND_REINVEST: units grow, zero cashflow.


@dataclass
class TxnLite:
    txn_date: date
    txn_type: str
    amount: float
    units: float
    scheme_code: str


@dataclass
class ComparisonResult:
    dates: list[date] = field(default_factory=list)
    customer_pct: list[float] = field(default_factory=list)
    benchmark_pct: list[float] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def _window_start(first_purchase: date, as_of: date, horizon: str) -> date:
    days = HORIZON_DAYS.get(horizon.upper(), None)
    if days is None:
        return first_purchase
    return max(first_purchase, as_of - timedelta(days=days))


def build_step_lookup(
    rows: list[tuple[date, float]],
) -> Callable[[date], Optional[float]]:
    """Return f(on) -> value of the nearest row with date <= on, else None."""
    ordered = sorted(rows, key=lambda r: r[0])
    ds = [r[0] for r in ordered]
    vs = [r[1] for r in ordered]

    def lookup(on: date) -> Optional[float]:
        i = bisect.bisect_right(ds, on) - 1
        if i < 0:
            return None
        return vs[i]

    return lookup


def build_comparison_series(
    txns: list[TxnLite],
    nav_lookup: Callable[[str, date], Optional[float]],
    tri_lookup: Callable[[date], Optional[float]],
    *,
    as_of: date,
    horizon: str = "MAX",
) -> ComparisonResult:
    """Daily cumulative money-weighted return for the customer and a Nifty clone.

    return%(t) = (value(t) - invested_to_date(t)) / invested_to_date(t), emitted
    only for days with invested_to_date > 0. Switches move units but are excluded
    from external cashflows and from the Nifty clone (portfolio grain).

    Customer XIRR is intentionally NOT computed here (reuse
    ``xirr_service.compute_portfolio_xirr``); only the benchmark XIRR is, since
    this is the only place the Nifty-clone cashflows exist.
    """
    if not txns:
        return ComparisonResult(summary=_empty_summary(as_of))

    txns = sorted(txns, key=lambda t: t.txn_date)
    first = txns[0].txn_date
    start = _window_start(first, as_of, horizon)
    by_date: dict[date, list[TxnLite]] = defaultdict(list)
    for t in txns:
        by_date[t.txn_date].append(t)

    units_per_scheme: dict[str, float] = defaultdict(float)
    nifty_units = 0.0
    invested = 0.0
    bench_cf: list[tuple[date, float]] = []  # for benchmark XIRR (Nifty clone)

    out = ComparisonResult()
    cust_val = bench_val = 0.0
    d = first
    one = timedelta(days=1)
    while d <= as_of:
        for t in by_date.get(d, []):
            # CAS stores redemption units as a *negative* number — use the magnitude and
            # let the type decide direction so a SELL can't add units back.
            if t.txn_type in ADD_UNIT_TYPES:
                units_per_scheme[t.scheme_code] += abs(t.units)
            elif t.txn_type in REMOVE_UNIT_TYPES:
                units_per_scheme[t.scheme_code] -= abs(t.units)

            tri_d = tri_lookup(d)
            if t.txn_type in EXTERNAL_IN_TYPES:
                invested += t.amount
                bench_cf.append((d, -t.amount))
                if tri_d:
                    nifty_units += t.amount / tri_d
            elif t.txn_type in EXTERNAL_OUT_TYPES:
                invested -= t.amount
                bench_cf.append((d, t.amount))
                if tri_d:
                    nifty_units -= t.amount / tri_d

        if invested > 0:
            cust_val = sum(
                u * (nav_lookup(s, d) or 0.0)
                for s, u in units_per_scheme.items()
                if u > 0
            )
            tri_d = tri_lookup(d)
            bench_val = nifty_units * (tri_d or 0.0)
            if d >= start:
                out.dates.append(d)
                out.customer_pct.append((cust_val - invested) / invested)
                out.benchmark_pct.append((bench_val - invested) / invested)
        d += one

    out.summary = _build_summary(bench_cf, cust_val, bench_val, invested, as_of)
    return out


def _empty_summary(as_of: date) -> dict:
    return {
        "benchmark_xirr": None,
        "customer_value": 0.0,
        "benchmark_value": 0.0,
        "invested": 0.0,
        "as_of": as_of,
    }


def _build_summary(bench_cf, cust_val, bench_val, invested, as_of) -> dict:
    bench_flows = list(bench_cf)
    if bench_val > 0:
        bench_flows.append((as_of, bench_val))
    return {
        "benchmark_xirr": xirr(bench_flows),
        "customer_value": round(cust_val, 2),
        "benchmark_value": round(bench_val, 2),
        "invested": round(invested, 2),
        "as_of": as_of,
    }


# ---------------------------------------------------------------------------
# Windowed XIRR (Performance-tab headline, per selected timeframe)
# ---------------------------------------------------------------------------

# Analysis-tab windows — mirror the frontend range selector exactly so the XIRR
# headline matches the chart window the user sees. YTD is calendar year-to-date;
# the rest are trailing-day counts; "ALL"/"MAX" = since first purchase.
ANALYSIS_WINDOW_DAYS: dict[str, int] = {"1M": 30, "3M": 90, "1Y": 365, "3Y": 365 * 3}


def _analysis_window_start(first_purchase: date, as_of: date, label: str) -> date:
    """Start date for a windowed-XIRR label, never earlier than first purchase."""
    u = label.upper()
    if u in ("ALL", "MAX"):
        return first_purchase
    if u == "YTD":
        return max(first_purchase, date(as_of.year, 1, 1))
    days = ANALYSIS_WINDOW_DAYS.get(u)
    if days is None:
        return first_purchase
    return max(first_purchase, as_of - timedelta(days=days))


def _customer_window_xirr(
    txns: list[TxnLite],
    nav_lookup: Callable[[str, date], Optional[float]],
    *,
    start: date,
    as_of: date,
) -> Optional[float]:
    """Money-weighted XIRR for the customer over ``[start, as_of]``.

    The position held *before* the window enters as one negative cashflow at
    ``start`` (units priced at the window-start NAV); buys/sells *inside* the
    window are signed external flows; the current value at ``as_of`` is the
    terminal inflow. CAS stores redemptions negative — magnitudes are taken and
    the type decides direction (matches ``build_comparison_series``).
    """
    units: dict[str, float] = defaultdict(float)

    def value_on(on: date) -> float:
        return sum(u * (nav_lookup(s, on) or 0.0) for s, u in units.items() if u > 0)

    for t in txns:  # units carried in before the window opens
        if t.txn_date >= start:
            continue
        if t.txn_type in ADD_UNIT_TYPES:
            units[t.scheme_code] += abs(t.units)
        elif t.txn_type in REMOVE_UNIT_TYPES:
            units[t.scheme_code] -= abs(t.units)

    cfs: list[tuple[date, float]] = []
    opening = value_on(start)
    if opening > 0:
        cfs.append((start, -opening))

    for t in txns:  # flows inside the window; units roll forward to as_of
        if t.txn_date < start:
            continue
        if t.txn_type in ADD_UNIT_TYPES:
            units[t.scheme_code] += abs(t.units)
        elif t.txn_type in REMOVE_UNIT_TYPES:
            units[t.scheme_code] -= abs(t.units)
        if t.txn_type in EXTERNAL_IN_TYPES:
            cfs.append((t.txn_date, -abs(t.amount)))
        elif t.txn_type in EXTERNAL_OUT_TYPES:
            cfs.append((t.txn_date, abs(t.amount)))

    terminal = value_on(as_of)
    if terminal > 0:
        cfs.append((as_of, terminal))

    return xirr(cfs)


def _nifty_window_xirr(
    txns: list[TxnLite],
    tri_lookup: Callable[[date], Optional[float]],
    *,
    start: date,
    as_of: date,
) -> Optional[float]:
    """Same window math for a Nifty-50 clone driven by identical external flows.

    Clone units bought/sold at each external flow = amount / TRI(flow date);
    opening and terminal value the accumulated clone units at the window bounds.
    """
    nifty_units = 0.0
    for t in txns:  # clone units carried in before the window opens
        if t.txn_date >= start:
            continue
        tri = tri_lookup(t.txn_date)
        if not tri:
            continue
        if t.txn_type in EXTERNAL_IN_TYPES:
            nifty_units += abs(t.amount) / tri
        elif t.txn_type in EXTERNAL_OUT_TYPES:
            nifty_units -= abs(t.amount) / tri

    cfs: list[tuple[date, float]] = []
    opening = nifty_units * (tri_lookup(start) or 0.0)
    if opening > 0:
        cfs.append((start, -opening))

    for t in txns:  # external flows inside the window; clone units roll forward
        if t.txn_date < start:
            continue
        tri = tri_lookup(t.txn_date)
        if t.txn_type in EXTERNAL_IN_TYPES:
            cfs.append((t.txn_date, -abs(t.amount)))
            if tri:
                nifty_units += abs(t.amount) / tri
        elif t.txn_type in EXTERNAL_OUT_TYPES:
            cfs.append((t.txn_date, abs(t.amount)))
            if tri:
                nifty_units -= abs(t.amount) / tri

    terminal = nifty_units * (tri_lookup(as_of) or 0.0)
    if terminal > 0:
        cfs.append((as_of, terminal))

    return xirr(cfs)


def compute_windowed_xirrs(
    txns: list[TxnLite],
    nav_lookup: Callable[[str, date], Optional[float]],
    tri_lookup: Callable[[date], Optional[float]],
    *,
    as_of: date,
    windows: list[str],
) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """Customer + Nifty-clone XIRR for each requested window.

    Returns ``{label: (customer_xirr, nifty_xirr)}`` (decimals, 0.12 == 12%);
    both ``None`` when there are no transactions or the XIRR is undefined.
    """
    if not txns:
        return {w: (None, None) for w in windows}
    txns = sorted(txns, key=lambda t: t.txn_date)
    first = txns[0].txn_date
    out: dict[str, tuple[Optional[float], Optional[float]]] = {}
    for w in windows:
        start = _analysis_window_start(first, as_of, w)
        out[w] = (
            _customer_window_xirr(txns, nav_lookup, start=start, as_of=as_of),
            _nifty_window_xirr(txns, tri_lookup, start=start, as_of=as_of),
        )
    return out


# ---------------------------------------------------------------------------
# DB adapter
# ---------------------------------------------------------------------------

# Benchmark index code lives in the benchmarks domain; re-exported here so
# ``twr_service`` (which imports it from this module) keeps working unchanged.
NIFTY_INDEX_NAME = NIFTY_50_CODE


async def _load_txns(db: AsyncSession, user_id: uuid.UUID) -> list[TxnLite]:
    rows = (
        (
            await db.execute(
                select(MfTransaction)
                .where(MfTransaction.user_id == user_id)
                .order_by(MfTransaction.transaction_date.asc())
            )
        )
        .scalars()
        .all()
    )
    return [
        TxnLite(
            txn_date=t.transaction_date,
            txn_type=t.transaction_type.value,
            amount=float(t.amount),
            units=float(t.units),
            scheme_code=t.scheme_code,
        )
        for t in rows
    ]


async def _load_nav_rows(db: AsyncSession, codes: set[str]):
    if not codes:
        return []
    return (
        await db.execute(
            select(
                MfNavHistory.scheme_code, MfNavHistory.nav_date, MfNavHistory.nav
            ).where(MfNavHistory.scheme_code.in_(codes))
        )
    ).all()


async def _load_tri_rows(db: AsyncSession) -> list[tuple[date, float]]:
    """Nifty 50 EOD (value_date, tri_value) series from the benchmarks domain."""
    return await load_value_series(db, NIFTY_INDEX_NAME)


async def compute_portfolio_vs_nifty(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    horizon: str = "MAX",
    as_of: Optional[date] = None,
) -> ComparisonResult:
    """Load a user's transactions + NAV + TRI and compute the comparison.

    Customer XIRR is intentionally not produced here — the backend pairs this
    with ``xirr_service.compute_portfolio_xirr`` for that headline.
    """
    as_of = as_of or date.today()
    txns = await _load_txns(db, user_id)
    if not txns:
        return ComparisonResult(summary=_empty_summary(as_of))

    codes = {t.scheme_code for t in txns}
    nav_rows = await _load_nav_rows(db, codes)
    per_scheme: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for scheme_code, nav_date, nav in nav_rows:
        per_scheme[scheme_code].append((nav_date, float(nav)))
    nav_lookups = {sc: build_step_lookup(rows) for sc, rows in per_scheme.items()}

    def nav_lookup(scheme: str, on: date) -> Optional[float]:
        f = nav_lookups.get(scheme)
        return f(on) if f else None

    tri_rows = await _load_tri_rows(db)
    tri_lookup = build_step_lookup([(d, float(v)) for d, v in tri_rows])

    return build_comparison_series(
        txns, nav_lookup, tri_lookup, as_of=as_of, horizon=horizon
    )


async def compute_windowed_xirrs_for_user(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    windows: list[str],
    as_of: Optional[date] = None,
) -> dict[str, Optional[float]]:
    """Customer money-weighted XIRR per window label for the Performance tab.

    Returns ``{label: customer_xirr}`` (decimal; None when undefined). The Nifty
    clone XIRR is computed but not surfaced here — the benchmark line on the chart
    is time-weighted and rebased client-side, so only the customer headline needs
    a per-window money-weighted figure.
    """
    as_of = as_of or date.today()
    txns = await _load_txns(db, user_id)
    if not txns:
        return {w: None for w in windows}

    codes = {t.scheme_code for t in txns}
    nav_rows = await _load_nav_rows(db, codes)
    per_scheme: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for scheme_code, nav_date, nav in nav_rows:
        per_scheme[scheme_code].append((nav_date, float(nav)))
    nav_lookups = {sc: build_step_lookup(rows) for sc, rows in per_scheme.items()}

    def nav_lookup(scheme: str, on: date) -> Optional[float]:
        f = nav_lookups.get(scheme)
        return f(on) if f else None

    tri_rows = await _load_tri_rows(db)
    tri_lookup = build_step_lookup([(d, float(v)) for d, v in tri_rows])

    out = compute_windowed_xirrs(
        txns, nav_lookup, tri_lookup, as_of=as_of, windows=windows
    )
    return {w: cust for w, (cust, _nifty) in out.items()}
