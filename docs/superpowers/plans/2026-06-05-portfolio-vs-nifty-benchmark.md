# Portfolio vs Nifty 50 Benchmark — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute a customer's money-weighted performance vs a Nifty-50 clone driven by identical cashflows — two cumulative-return time series (the chart) plus XIRR headlines.

**Architecture:** A pure, DB-free core (`build_comparison_series`) that walks a daily timeline and computes both lines + XIRR via injected `nav_lookup`/`tri_lookup` callables, plus a thin async DB adapter (`compute_portfolio_vs_nifty`) that bulk-loads transactions + NAV + TRI and builds nearest-on-or-before step lookups. Reuses `financial_primitives.xirr`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, `financial_primitives.xirr`, pytest (`asyncio_mode=auto`), sqlite+aiosqlite for tests, Jupyter for the prototype notebook.

**Spec:** `docs/superpowers/specs/2026-06-05-portfolio-vs-nifty-benchmark-design.md`

**Run tests with:** `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest <path> -v`

---

> ## ⚠️ Amendment — implemented as the "A-split" (source of truth = the built code)
>
> After this plan was written we decided **not to duplicate the customer XIRR** in this service.
> The implementation differs from the task bodies below as follows:
> - **No `customer_xirr`** anywhere in `benchmark_service`. The backend reuses
>   `mutual_funds.services.xirr_service.compute_portfolio_xirr` for that headline.
> - The core tracks only the **Nifty-clone** cashflows (`bench_cf`); the customer *line*
>   needs only `value(t)` / `invested(t)`, so customer-cashflow tracking was removed.
> - `summary` keys = `{ benchmark_xirr, customer_value, benchmark_value, invested, as_of }`
>   (no `customer_xirr`). `_build_summary(bench_cf, cust_val, bench_val, invested, as_of)`.
> - Task 3 computes **only** `benchmark_xirr`; its tests assert the line winner +
>   `benchmark_xirr` matching the primitive on the clone cashflows.
> - Notebook: matplotlib/nbconvert are not in the venv, so the plot cell is matplotlib-guarded
>   and the compute logic was validated by running the cells' code against the real CSV.
>
> The shipped, tested code in `app/domains/portfolio/services/benchmark_service.py` and
> `app/domains/portfolio/tests/test_benchmark_service.py` (11 passing tests) is authoritative
> where it diverges from the pre-amendment task text below.

---

## File Structure

**Create:**
- `app/domains/portfolio/services/benchmark_service.py` — pure core + helpers + DB adapter.
- `app/domains/portfolio/tests/__init__.py` — test package marker.
- `app/domains/portfolio/tests/test_benchmark_service.py` — unit tests.
- `notebooks/benchmark_prototype.ipynb` — prototype/eyeball on real TRI + synthetic txns.

**Modify:** none (service-only; no router/__init__ exports needed for this scope).

---

## Task 1: Module scaffold — dataclasses, constants, pure helpers

**Files:**
- Create: `app/domains/portfolio/services/benchmark_service.py`
- Create: `app/domains/portfolio/tests/__init__.py`
- Create: `app/domains/portfolio/tests/test_benchmark_service.py`

- [ ] **Step 1: Write the failing tests** — create `app/domains/portfolio/tests/__init__.py` (empty) and `app/domains/portfolio/tests/test_benchmark_service.py`:

```python
"""Tests for portfolio-vs-Nifty benchmark service."""

from __future__ import annotations

from datetime import date

import pytest

from app.domains.portfolio.services import benchmark_service as bs


def test_window_start_max_uses_first_purchase():
    assert bs._window_start(date(2020, 1, 1), date(2026, 6, 5), "MAX") == date(2020, 1, 1)


def test_window_start_1y_clips_to_window():
    assert bs._window_start(date(2020, 1, 1), date(2026, 6, 5), "1Y") == date(2025, 6, 5)


def test_window_start_1y_not_before_first_purchase():
    assert bs._window_start(date(2026, 1, 1), date(2026, 6, 5), "1Y") == date(2026, 1, 1)


def test_build_step_lookup_nearest_on_or_before():
    f = bs.build_step_lookup([(date(2024, 1, 5), 100.0), (date(2024, 1, 8), 110.0)])
    assert f(date(2024, 1, 4)) is None        # before first
    assert f(date(2024, 1, 5)) == 100.0       # exact
    assert f(date(2024, 1, 7)) == 100.0       # between -> prior
    assert f(date(2024, 1, 8)) == 110.0       # exact
    assert f(date(2024, 1, 20)) == 110.0      # after last -> last
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -v`
Expected: FAIL — `ModuleNotFoundError`/`AttributeError` (module + helpers don't exist).

- [ ] **Step 3: Write the scaffold** — create `app/domains/portfolio/services/benchmark_service.py`:

```python
"""Customer portfolio vs Nifty-50 benchmark (money-weighted).

Two outputs from the customer's actual cashflows:
- a cumulative money-weighted return series for the customer portfolio and a
  Nifty-50 "clone" driven by identical cashflows (the chart), and
- XIRR headlines for both.

The math core (``build_comparison_series``) is pure: it takes typed transactions
and ``nav_lookup`` / ``tri_lookup`` callables, so it is fully unit-testable. The
async DB adapter (``compute_portfolio_vs_nifty``) bulk-loads rows and builds the
lookups.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Optional

from financial_primitives.xirr import xirr

# Horizon -> trailing days; MAX = since first purchase.
HORIZON_DAYS: dict[str, Optional[int]] = {"1M": 30, "1Y": 365, "3Y": 365 * 3, "MAX": None}

# Transaction-type handling (string values of MfTransactionType).
ADD_UNIT_TYPES = {"BUY", "SWITCH_IN", "DIVIDEND_REINVEST"}  # increase units held
REMOVE_UNIT_TYPES = {"SELL", "SWITCH_OUT"}                  # decrease units held
EXTERNAL_IN_TYPES = {"BUY"}                                 # external money in (+invested)
EXTERNAL_OUT_TYPES = {"SELL"}                               # external money out (-invested)
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


def build_step_lookup(rows: list[tuple[date, float]]) -> Callable[[date], Optional[float]]:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/portfolio/services/benchmark_service.py app/domains/portfolio/tests/
git commit -m "feat(portfolio): benchmark service scaffold (dataclasses, helpers)"
```

---

## Task 2: Core series — full transaction handling + cumulative return lines

This implements `build_comparison_series` with all transaction types in one go (the
branch logic is small and clearer written once). Tests cover lump sum, SIP,
pre-first-buy exclusion, and a buy+sell.

**Files:**
- Modify: `app/domains/portfolio/services/benchmark_service.py`
- Modify: `app/domains/portfolio/tests/test_benchmark_service.py`

- [ ] **Step 1: Write the failing tests** — append to `app/domains/portfolio/tests/test_benchmark_service.py`:

```python
def _flat_nav(value: float):
    return lambda scheme, on: value


def _flat_tri(value: float):
    return lambda on: value


def test_lump_sum_customer_matches_nav_growth():
    # One BUY of 100 units @ NAV 10 (=1000) on day 0; NAV doubles to 20.
    txns = [bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1")]
    nav = lambda scheme, on: 10.0 if on == date(2024, 1, 1) else 20.0
    tri = lambda on: 100.0 if on == date(2024, 1, 1) else 200.0
    res = bs.build_comparison_series(txns, nav, tri, as_of=date(2024, 1, 2), horizon="MAX")
    assert res.dates == [date(2024, 1, 1), date(2024, 1, 2)]
    # day0: (1000-1000)/1000 = 0 ; day1: (2000-1000)/1000 = 1.0
    assert res.customer_pct == pytest.approx([0.0, 1.0])
    # Nifty clone: 1000/100 = 10 units; day1 value 10*200=2000 -> +1.0
    assert res.benchmark_pct == pytest.approx([0.0, 1.0])


def test_sip_invested_steps_up_and_series_length():
    txns = [
        bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1"),
        bs.TxnLite(date(2024, 1, 3), "BUY", 1000.0, 50.0, "S1"),
    ]
    res = bs.build_comparison_series(
        txns, _flat_nav(10.0), _flat_tri(100.0), as_of=date(2024, 1, 4), horizon="MAX"
    )
    # 4 days (Jan 1..4); flat NAV so value == invested each day -> 0% throughout
    assert len(res.dates) == 4
    assert res.customer_pct == pytest.approx([0.0, 0.0, 0.0, 0.0])
    assert res.summary["invested"] == pytest.approx(2000.0)


def test_days_before_first_buy_excluded():
    txns = [bs.TxnLite(date(2024, 1, 3), "BUY", 1000.0, 100.0, "S1")]
    res = bs.build_comparison_series(
        txns, _flat_nav(10.0), _flat_tri(100.0), as_of=date(2024, 1, 5), horizon="MAX"
    )
    assert res.dates[0] == date(2024, 1, 3)  # nothing before first purchase


def test_buy_then_partial_sell_reduces_units_both_sides():
    # Buy 100 units (1000), later sell 40 units (400) at unchanged prices.
    txns = [
        bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1"),
        bs.TxnLite(date(2024, 1, 3), "SELL", 400.0, 40.0, "S1"),
    ]
    res = bs.build_comparison_series(
        txns, _flat_nav(10.0), _flat_tri(100.0), as_of=date(2024, 1, 3), horizon="MAX"
    )
    # After sell: units 60 -> value 600; invested 1000-400=600 -> 0%
    assert res.customer_pct[-1] == pytest.approx(0.0)
    assert res.benchmark_pct[-1] == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -k "lump_sum or sip or before_first_buy or partial_sell" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'build_comparison_series'`.

- [ ] **Step 3: Implement the core** — add to `app/domains/portfolio/services/benchmark_service.py` (after `build_step_lookup`):

```python
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
    cust_cf: list[tuple[date, float]] = []
    bench_cf: list[tuple[date, float]] = []

    out = ComparisonResult()
    cust_val = bench_val = 0.0
    d = first
    one = timedelta(days=1)
    while d <= as_of:
        for t in by_date.get(d, []):
            if t.txn_type in ADD_UNIT_TYPES:
                units_per_scheme[t.scheme_code] += t.units
            elif t.txn_type in REMOVE_UNIT_TYPES:
                units_per_scheme[t.scheme_code] -= t.units

            tri_d = tri_lookup(d)
            if t.txn_type in EXTERNAL_IN_TYPES:
                invested += t.amount
                cust_cf.append((d, -t.amount))
                bench_cf.append((d, -t.amount))
                if tri_d:
                    nifty_units += t.amount / tri_d
            elif t.txn_type in EXTERNAL_OUT_TYPES:
                invested -= t.amount
                cust_cf.append((d, t.amount))
                bench_cf.append((d, t.amount))
                if tri_d:
                    nifty_units -= t.amount / tri_d

        if invested > 0:
            cust_val = sum(
                u * (nav_lookup(s, d) or 0.0) for s, u in units_per_scheme.items() if u > 0
            )
            tri_d = tri_lookup(d)
            bench_val = nifty_units * (tri_d or 0.0)
            if d >= start:
                out.dates.append(d)
                out.customer_pct.append((cust_val - invested) / invested)
                out.benchmark_pct.append((bench_val - invested) / invested)
        d += one

    out.summary = _build_summary(cust_cf, bench_cf, cust_val, bench_val, invested, as_of)
    return out


def _empty_summary(as_of: date) -> dict:
    return {
        "customer_xirr": None,
        "benchmark_xirr": None,
        "customer_value": 0.0,
        "benchmark_value": 0.0,
        "invested": 0.0,
        "as_of": as_of,
    }


def _build_summary(cust_cf, bench_cf, cust_val, bench_val, invested, as_of) -> dict:
    # Placeholder until Task 3 wires XIRR; values filled then.
    return {
        "customer_xirr": None,
        "benchmark_xirr": None,
        "customer_value": round(cust_val, 2),
        "benchmark_value": round(bench_val, 2),
        "invested": round(invested, 2),
        "as_of": as_of,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/portfolio/services/benchmark_service.py app/domains/portfolio/tests/test_benchmark_service.py
git commit -m "feat(portfolio): cumulative money-weighted comparison series"
```

---

## Task 3: XIRR headlines (customer + Nifty clone)

**Files:**
- Modify: `app/domains/portfolio/services/benchmark_service.py:_build_summary`
- Modify: `app/domains/portfolio/tests/test_benchmark_service.py`

- [ ] **Step 1: Write the failing tests** — append to `app/domains/portfolio/tests/test_benchmark_service.py`:

```python
def test_customer_beats_benchmark_xirr_and_line():
    # Customer NAV doubles; Nifty TRI flat. Customer must win on both line and XIRR.
    txns = [bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1")]
    nav = lambda scheme, on: 10.0 if on == date(2024, 1, 1) else 20.0
    res = bs.build_comparison_series(
        txns, nav, _flat_tri(100.0), as_of=date(2025, 1, 1), horizon="MAX"
    )
    assert res.customer_pct[-1] > res.benchmark_pct[-1]
    assert res.summary["customer_xirr"] > res.summary["benchmark_xirr"]


def test_customer_lags_benchmark_xirr():
    # Customer NAV flat; Nifty TRI doubles. Nifty must win.
    txns = [bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1")]
    tri = lambda on: 100.0 if on == date(2024, 1, 1) else 200.0
    res = bs.build_comparison_series(
        txns, _flat_nav(10.0), tri, as_of=date(2025, 1, 1), horizon="MAX"
    )
    assert res.summary["benchmark_xirr"] > res.summary["customer_xirr"]


def test_customer_xirr_matches_primitive_on_same_cashflows():
    from financial_primitives.xirr import xirr as xirr_primitive

    txns = [bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1")]
    nav = lambda scheme, on: 10.0 if on == date(2024, 1, 1) else 20.0
    as_of = date(2025, 1, 1)
    res = bs.build_comparison_series(txns, nav, _flat_tri(100.0), as_of=as_of, horizon="MAX")
    expected = xirr_primitive([(date(2024, 1, 1), -1000.0), (as_of, 2000.0)])
    assert res.summary["customer_xirr"] == pytest.approx(expected)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -k "beats or lags or matches_primitive" -v`
Expected: FAIL — `customer_xirr` is `None`, so comparisons raise `TypeError`.

- [ ] **Step 3: Wire XIRR into the summary** — replace the placeholder `_build_summary` in `app/domains/portfolio/services/benchmark_service.py` with:

```python
def _build_summary(cust_cf, bench_cf, cust_val, bench_val, invested, as_of) -> dict:
    cust_flows = list(cust_cf)
    bench_flows = list(bench_cf)
    if cust_val > 0:
        cust_flows.append((as_of, cust_val))
    if bench_val > 0:
        bench_flows.append((as_of, bench_val))
    return {
        "customer_xirr": xirr(cust_flows),
        "benchmark_xirr": xirr(bench_flows),
        "customer_value": round(cust_val, 2),
        "benchmark_value": round(bench_val, 2),
        "invested": round(invested, 2),
        "as_of": as_of,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/portfolio/services/benchmark_service.py app/domains/portfolio/tests/test_benchmark_service.py
git commit -m "feat(portfolio): XIRR headlines for customer + Nifty clone"
```

---

## Task 4: DB adapter — `compute_portfolio_vs_nifty`

Loads transactions + NAV + TRI from the DB, builds lookups, calls the pure core.
Row-loading is delegated to three small async helpers so the adapter is testable
by monkeypatching them (no live DB needed).

**Files:**
- Modify: `app/domains/portfolio/services/benchmark_service.py`
- Modify: `app/domains/portfolio/tests/test_benchmark_service.py`

- [ ] **Step 1: Write the failing test** — append to `app/domains/portfolio/tests/test_benchmark_service.py`:

```python
@pytest.mark.asyncio
async def test_compute_portfolio_vs_nifty_wires_loaders(monkeypatch):
    import uuid

    uid = uuid.uuid4()
    as_of = date(2024, 1, 2)

    async def fake_load_txns(db, user_id):
        assert user_id == uid
        return [bs.TxnLite(date(2024, 1, 1), "BUY", 1000.0, 100.0, "S1")]

    async def fake_load_nav_rows(db, codes):
        assert codes == {"S1"}
        return [("S1", date(2024, 1, 1), 10.0), ("S1", date(2024, 1, 2), 20.0)]

    async def fake_load_tri_rows(db):
        return [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 100.0)]

    monkeypatch.setattr(bs, "_load_txns", fake_load_txns)
    monkeypatch.setattr(bs, "_load_nav_rows", fake_load_nav_rows)
    monkeypatch.setattr(bs, "_load_tri_rows", fake_load_tri_rows)

    res = await bs.compute_portfolio_vs_nifty(db=None, user_id=uid, horizon="MAX", as_of=as_of)
    assert res.dates == [date(2024, 1, 1), date(2024, 1, 2)]
    assert res.customer_pct[-1] == pytest.approx(1.0)   # NAV doubled
    assert res.benchmark_pct[-1] == pytest.approx(0.0)  # TRI flat
    assert res.summary["invested"] == pytest.approx(1000.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py::test_compute_portfolio_vs_nifty_wires_loaders -v`
Expected: FAIL — `AttributeError: ... has no attribute '_load_txns'`.

- [ ] **Step 3: Implement the adapter + loaders** — add to `app/domains/portfolio/services/benchmark_service.py`:

Add these imports at the top (with the existing imports):

```python
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models import IndexTriHistory, MfNavHistory, MfTransaction
```

Then add at the end of the file:

```python
NIFTY_INDEX_NAME = "NIFTY 50"


async def _load_txns(db: AsyncSession, user_id: uuid.UUID) -> list[TxnLite]:
    rows = (
        await db.execute(
            select(MfTransaction)
            .where(MfTransaction.user_id == user_id)
            .order_by(MfTransaction.transaction_date.asc())
        )
    ).scalars().all()
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
            select(MfNavHistory.scheme_code, MfNavHistory.nav_date, MfNavHistory.nav)
            .where(MfNavHistory.scheme_code.in_(codes))
        )
    ).all()


async def _load_tri_rows(db: AsyncSession):
    return (
        await db.execute(
            select(IndexTriHistory.tri_date, IndexTriHistory.tri_value)
            .where(IndexTriHistory.index_name == NIFTY_INDEX_NAME)
        )
    ).all()


async def compute_portfolio_vs_nifty(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    horizon: str = "MAX",
    as_of: Optional[date] = None,
) -> ComparisonResult:
    """Load a user's transactions + NAV + TRI and compute the comparison."""
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

    return build_comparison_series(txns, nav_lookup, tri_lookup, as_of=as_of, horizon=horizon)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m pytest app/domains/portfolio/tests/test_benchmark_service.py -v`
Expected: PASS (12 tests).

- [ ] **Step 5: Commit**

```bash
git add app/domains/portfolio/services/benchmark_service.py app/domains/portfolio/tests/test_benchmark_service.py
git commit -m "feat(portfolio): DB adapter compute_portfolio_vs_nifty"
```

---

## Task 5: Prototype notebook

Create a notebook that runs the validated core against the real TRI history +
synthetic transactions, prints the summary, and plots the two lines — the
eyeball check the user asked for.

**Files:**
- Create: `notebooks/benchmark_prototype.ipynb`

- [ ] **Step 1: Create the notebook** — write `notebooks/benchmark_prototype.ipynb` with this JSON (4 cells):

```json
{
 "cells": [
  {"cell_type": "markdown", "metadata": {}, "source": ["# Portfolio vs Nifty 50 — prototype\n", "Runs the pure core on real TRI history + synthetic transactions."]},
  {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": [
    "import sys, csv\n",
    "from datetime import date\n",
    "sys.path.insert(0, '..')\n",
    "from app.domains.portfolio.services import benchmark_service as bs\n",
    "\n",
    "# Real TRI from the validated Part-1 CSV (nearest-on-or-before lookup).\n",
    "rows = []\n",
    "with open('../nifty50_tri_full.csv') as fh:\n",
    "    for r in csv.DictReader(fh):\n",
    "        rows.append((date.fromisoformat(r['tri_date']), float(r['tri_value'])))\n",
    "tri_lookup = bs.build_step_lookup(rows)\n",
    "print('TRI rows:', len(rows), '| 2024-01-31:', tri_lookup(date(2024,1,31)))"
  ]},
  {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": [
    "# Synthetic monthly SIP into one scheme; flat-ish NAV grown 12%/yr for illustration.\n",
    "txns = [bs.TxnLite(date(2023,1,1), 'BUY', 10000.0, 1000.0, 'S1'),\n",
    "        bs.TxnLite(date(2023,7,1), 'BUY', 10000.0, 950.0, 'S1'),\n",
    "        bs.TxnLite(date(2024,1,1), 'BUY', 10000.0, 900.0, 'S1')]\n",
    "def nav_lookup(scheme, on):\n",
    "    base = date(2023,1,1)\n",
    "    yrs = (on - base).days/365.0\n",
    "    return 10.0 * (1.12 ** yrs)\n",
    "res = bs.build_comparison_series(txns, nav_lookup, tri_lookup, as_of=date(2024,6,1), horizon='MAX')\n",
    "print('summary:', res.summary)\n",
    "print('points:', len(res.dates), '| last cust%:', round(res.customer_pct[-1]*100,2), '| last nifty%:', round(res.benchmark_pct[-1]*100,2))"
  ]},
  {"cell_type": "code", "execution_count": null, "metadata": {}, "outputs": [], "source": [
    "import matplotlib.pyplot as plt\n",
    "plt.figure(figsize=(9,4))\n",
    "plt.plot(res.dates, [p*100 for p in res.customer_pct], label='Customer')\n",
    "plt.plot(res.dates, [p*100 for p in res.benchmark_pct], '--', label='Nifty 50')\n",
    "plt.axhline(0, color='gray', lw=0.5); plt.ylabel('cumulative return %'); plt.legend(); plt.title('Portfolio vs Nifty 50')\n",
    "plt.show()"
  ]}
 ],
 "metadata": {"language_info": {"name": "python"}},
 "nbformat": 4,
 "nbformat_minor": 5
}
```

- [ ] **Step 2: Execute the notebook headlessly to verify it runs**

Run: `cd ailax/Prozpr_Backend && .venv-mac/bin/python -m jupyter nbconvert --to notebook --execute --inplace notebooks/benchmark_prototype.ipynb`
Expected: completes with exit 0 (no exceptions). If `jupyter`/`nbconvert` is missing, install into the venv: `.venv-mac/bin/python -m pip install nbconvert ipykernel matplotlib`, then re-run.
If `matplotlib` is unavailable and you don't want it as a dep, delete the 4th cell (plot) and re-run; the summary cell is the essential check.

- [ ] **Step 3: Sanity-check the output**

Open the executed notebook (or the nbconvert stdout). Expected: `summary` shows non-null `customer_xirr` and `benchmark_xirr`, `invested == 30000.0`, and `points` > 0. The two `%` numbers are finite.

- [ ] **Step 4: Commit**

```bash
git add notebooks/benchmark_prototype.ipynb
git commit -m "feat(portfolio): benchmark prototype notebook"
```

---

## Self-Review

**Spec coverage:**
- Money-weighted metric (cumulative return line) → Task 2. ✓
- XIRR headlines (customer + Nifty clone, same cashflows) → Task 3. ✓
- Per-point mechanics (units step fn, invested_to_date, nifty clone units, return%) → Task 2 core. ✓
- Switch/dividend rules (units move, excluded from external cashflows + clone) → Task 2 (`ADD_UNIT_TYPES`/`EXTERNAL_*`). ✓
- Edge cases: pre-first-buy excluded (Task 2 test), missing NAV → 0 contribution (`or 0.0`), missing TRI → skip clone unit (`if tri_d`). ✓
- Reuse `financial_primitives.xirr` → Task 3. ✓
- Pure core + DB adapter w/ bulk loaders + nearest-on-or-before lookups → Tasks 1–4. ✓
- Notebook-first prototype on real TRI → Task 5. ✓
- Horizon clips window (MAX = since first purchase) → `_window_start` (Task 1), used in Task 2. ✓
- Router/UI explicitly out of scope → no task. ✓

**Placeholder scan:** No TBD/TODO. `_build_summary` is introduced as an explicit placeholder in Task 2 and *replaced* with the real XIRR version in Task 3 (intentional TDD progression, not a leftover). All code steps show complete code.

**Type consistency:** `TxnLite(txn_date, txn_type, amount, units, scheme_code)`, `ComparisonResult(dates, customer_pct, benchmark_pct, summary)`, `build_step_lookup`, `build_comparison_series(txns, nav_lookup, tri_lookup, *, as_of, horizon)`, `compute_portfolio_vs_nifty(db, user_id, *, horizon, as_of)`, and `_load_txns/_load_nav_rows/_load_tri_rows` are referenced identically across tasks. `summary` keys (`customer_xirr, benchmark_xirr, customer_value, benchmark_value, invested, as_of`) consistent in Tasks 2–4. ✓

**Note on sells:** `invested_to_date` is *net* (BUY − SELL); realised gains from sells are not separately added to return% — consistent and fair because the Nifty clone applies the identical sell. Flagged for notebook review if a future requirement needs realised-gain accounting.
