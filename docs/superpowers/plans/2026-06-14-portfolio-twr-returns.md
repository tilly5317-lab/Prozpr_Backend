# Portfolio TWR Returns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the synthetic Returns tab of Portfolio Analysis with a real, mutual-funds-only Time-Weighted Return — portfolio TWR, Nifty 50 TWR, and a chart — computed in the backend and rendered by the existing chart.

**Architecture:** A pure backend function links daily portfolio values minus external cashflows into a growth-of-₹1 wealth index; a thin DB adapter feeds it from `UserPortfolioNavHistory` + `MfTransaction` and aligns the Nifty 50 TRI; a new `GET /portfolio/twr` returns the full daily series since inception; the frontend fetches it once and rebases per range client-side (`W_end / W_start − 1`).

**Tech Stack:** FastAPI + SQLAlchemy async (Python, `.venv-mac`), React + TypeScript + Recharts (Vite/vitest). Two git repos: `Prozpr_Backend`, `Prozpr_Frontend`.

**Spec:** `Prozpr_Backend/docs/superpowers/specs/2026-06-13-portfolio-twr-returns-design.md`

**Note on commits:** Commit steps below follow this repo's git workflow. Confirm with the maintainer before pushing; back-end and front-end commit separately (different repos).

**Implementation notes (2026-06-14) — two deviations made during execution:**
1. **Pure kernel moved to `financial_primitives`.** The pure TWR function lives in `AI_Agents/src/financial_primitives/twr.py` as `twr_wealth_index(...)` (exported from the package, tested in `Testing/test_twr.py`), sitting next to `xirr`. `app/domains/portfolio/services/twr_service.py` keeps only the DB adapter `compute_twr_series`, which does `from financial_primitives import twr_wealth_index` — mirroring how `benchmark_service` imports `xirr`. (Tasks 1 & 3 below describe the older single-file layout.)
2. **Adapter test needs `import app.all_models`.** Creating only the tables under test fails FK resolution (`NoReferencedTableError: ... 'users'`) because SQLAlchemy needs every referenced table's blueprint registered. The fixture imports `app.all_models` so all table objects are registered (FKs resolve), while still only physically creating the 3 tables under test.

---

## File structure

**Backend (`Prozpr_Backend/`)**
- Create `app/domains/portfolio/services/twr_service.py` — pure `compute_twr_wealth_index` + async `compute_twr_series` adapter.
- Create `app/domains/portfolio/services/tests/__init__.py` and `.../tests/test_twr_service.py` — unit + adapter tests.
- Modify `app/domains/portfolio/schemas/portfolio.py` — add `TwrPoint`, `TwrSeriesResponse`.
- Modify `app/domains/portfolio/routers/portfolio_router.py` — add `GET /twr`.

**Frontend (`Prozpr_Frontend/`)**
- Modify `src/lib/api.ts` — add `TwrPoint` / `TwrSeriesResponse` types + `getPortfolioTwr()`.
- Create `src/lib/twr.ts` — pure `windowStartIndex` + `rebaseTwr`.
- Create `src/lib/twr.test.ts` — vitest for the rebasing math.
- Modify `src/components/dashboard/PortfolioAnalysisModal.tsx` — fetch the series, rebase per range, scope label, loading/empty states; delete synthetic helpers.

---

## Task 1: Pure TWR core (`compute_twr_wealth_index`) — test-first

**Files:**
- Create: `app/domains/portfolio/services/twr_service.py`
- Create: `app/domains/portfolio/services/tests/__init__.py`
- Test: `app/domains/portfolio/services/tests/test_twr_service.py`

- [ ] **Step 1: Create the test package marker**

Create `app/domains/portfolio/services/tests/__init__.py` (empty file).

- [ ] **Step 2: Write the failing unit tests**

Create `app/domains/portfolio/services/tests/test_twr_service.py`:

```python
"""Unit tests for the pure TWR core."""

from __future__ import annotations

from datetime import date

from app.domains.portfolio.services.twr_service import compute_twr_wealth_index


def _w(series):
    """Extract just the wealth-index values (rounded) from (date, W) tuples."""
    return [round(w, 6) for _, w in series]


def test_no_cashflows_is_plain_value_growth():
    values = [(date(2024, 1, 1), 100.0), (date(2024, 1, 2), 110.0), (date(2024, 1, 3), 121.0)]
    assert _w(compute_twr_wealth_index(values, {})) == [1.0, 1.1, 1.21]


def test_mid_period_contribution_does_not_distort_twr():
    # Day 1 invest 100; +10% to 110; day 3 add 100 (value 210); +10% to 231.
    # TWR must be 21% regardless of the contribution (where MWR would differ).
    values = [
        (date(2024, 1, 1), 100.0),
        (date(2024, 1, 2), 110.0),
        (date(2024, 1, 3), 210.0),
        (date(2024, 1, 4), 231.0),
    ]
    cashflows = {date(2024, 1, 1): 100.0, date(2024, 1, 3): 100.0}
    assert _w(compute_twr_wealth_index(values, cashflows))[-1] == 1.21


def test_sell_does_not_distort_twr():
    # Sell half on day 3 (value drops to 55, proceeds 55 leave); +10% to 60.5.
    values = [
        (date(2024, 1, 1), 100.0),
        (date(2024, 1, 2), 110.0),
        (date(2024, 1, 3), 55.0),
        (date(2024, 1, 4), 60.5),
    ]
    cashflows = {date(2024, 1, 1): 100.0, date(2024, 1, 3): -55.0}
    assert _w(compute_twr_wealth_index(values, cashflows))[-1] == 1.21


def test_full_exit_then_reentry_carries_flat_and_resumes():
    # Full exit to 0, idle, re-buy 50, +10% to 55. No divide-by-zero; index resumes.
    values = [
        (date(2024, 1, 1), 100.0),
        (date(2024, 1, 2), 110.0),
        (date(2024, 1, 3), 0.0),
        (date(2024, 1, 4), 0.0),
        (date(2024, 1, 5), 50.0),
        (date(2024, 1, 6), 55.0),
    ]
    cashflows = {date(2024, 1, 1): 100.0, date(2024, 1, 3): -110.0, date(2024, 1, 5): 50.0}
    assert _w(compute_twr_wealth_index(values, cashflows)) == [1.0, 1.1, 1.1, 1.1, 1.1, 1.21]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/portfolio/services/tests/test_twr_service.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError: cannot import name 'compute_twr_wealth_index'`.

- [ ] **Step 4: Write the minimal pure implementation**

Create `app/domains/portfolio/services/twr_service.py`:

```python
"""Time-Weighted Return for the portfolio (mutual-funds only).

Pure core (``compute_twr_wealth_index``) links daily portfolio values minus
external cashflows into a growth-of-1 wealth index — fully unit-testable, no DB.
The async adapter (``compute_twr_series``) feeds it from UserPortfolioNavHistory
+ MfTransaction and aligns the Nifty 50 TRI. See the design spec for the math.
"""

from __future__ import annotations

from datetime import date

_EPS = 1e-9


def compute_twr_wealth_index(
    daily_values: list[tuple[date, float]],
    daily_cashflows: dict[date, float],
) -> list[tuple[date, float]]:
    """Daily-linked TWR wealth index, anchored W = 1.0 on the first day.

    ``daily_values``: (recorded_date, total_value) ascending, dense per day.
    ``daily_cashflows``: net external cashflow per date (+ money in, − money out).

    r_t = (V_t − C_t) / V_{t-1} − 1 ; W_t = W_{t-1} × (1 + r_t).
    When the prior value is ~0 (first day, or re-entry after a full exit) there is
    no base: r_t = 0 (carry W flat), and that day seeds a fresh base.
    """
    out: list[tuple[date, float]] = []
    w = 1.0
    prev_value: float | None = None
    for d, v in daily_values:
        c = daily_cashflows.get(d, 0.0)
        if prev_value is None or prev_value <= _EPS:
            r = 0.0
        else:
            r = (v - c) / prev_value - 1.0
        w *= 1.0 + r
        out.append((d, w))
        prev_value = v
    return out
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/portfolio/services/tests/test_twr_service.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
cd Prozpr_Backend
git add app/domains/portfolio/services/twr_service.py app/domains/portfolio/services/tests/
git commit -m "feat(portfolio): pure TWR wealth-index core + unit tests"
```

---

## Task 2: Response schemas (`TwrPoint`, `TwrSeriesResponse`)

**Files:**
- Modify: `app/domains/portfolio/schemas/portfolio.py`

- [ ] **Step 1: Add the schemas**

Append to `app/domains/portfolio/schemas/portfolio.py` (the file already imports `date` from `datetime` and `BaseModel` from `pydantic`; if either import is missing, add `from datetime import date` and `from pydantic import BaseModel` at the top alongside the existing imports):

```python
class TwrPoint(BaseModel):
    """One day of the TWR series. Both indices are growth-of-1 (1.0 at inception)."""

    date: date
    portfolio_index: float
    nifty_index: float | None  # Nifty 50 TRI normalized to inception; null if no baseline


class TwrSeriesResponse(BaseModel):
    """Full daily TWR series since inception. Frontend rebases per range."""

    has_data: bool  # True only when there are >= 2 valued days (renderable)
    points: list[TwrPoint]
```

- [ ] **Step 2: Verify it imports cleanly**

Run: `.venv-mac/bin/python -c "from app.domains.portfolio.schemas.portfolio import TwrPoint, TwrSeriesResponse; print('ok')"`
Expected: prints `ok` (no import error).

- [ ] **Step 3: Commit**

```bash
cd Prozpr_Backend
git add app/domains/portfolio/schemas/portfolio.py
git commit -m "feat(portfolio): TWR response schemas"
```

---

## Task 3: DB adapter (`compute_twr_series`) — test-first

**Files:**
- Modify: `app/domains/portfolio/services/twr_service.py`
- Test: `app/domains/portfolio/services/tests/test_twr_service.py`

- [ ] **Step 1: Write the failing adapter tests**

Append to `app/domains/portfolio/services/tests/test_twr_service.py`:

```python
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domains.mutual_funds.models import IndexTriHistory, MfTransaction
from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.portfolio.models.user_portfolio_nav_history import UserPortfolioNavHistory
from app.domains.portfolio.services.twr_service import compute_twr_series


@pytest_asyncio.fixture
async def db_session():
    # Per backend CLAUDE.md: create only the tables under test (Base.metadata.create_all
    # fails on sqlite because an unrelated model uses a Postgres ARRAY). sqlite does not
    # enforce FKs by default, so FK columns to un-created tables are fine for inserts.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(UserPortfolioNavHistory.__table__.create)
        await conn.run_sync(MfTransaction.__table__.create)
        await conn.run_sync(IndexTriHistory.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _nav(user_id, d, value):
    return UserPortfolioNavHistory(
        id=uuid.uuid4(), user_id=user_id, recorded_date=d,
        total_value=value, total_invested=value, gain_percentage=0.0,
    )


@pytest.mark.asyncio
async def test_compute_twr_series_real_data(db_session: AsyncSession):
    uid = uuid.uuid4()
    db_session.add_all([
        _nav(uid, date(2024, 1, 1), 100.0),
        _nav(uid, date(2024, 1, 2), 110.0),
        _nav(uid, date(2024, 1, 3), 121.0),
    ])
    # A SELL with a NEGATIVE stored amount must still reduce (abs + type sign).
    db_session.add(MfTransaction(
        id=uuid.uuid4(), user_id=uid, scheme_code="SCH1", folio_number="F1",
        transaction_type=MfTransactionType.SELL, transaction_date=date(2024, 1, 3),
        units=-1.0, nav=10.0, amount=-10.0,
    ))
    db_session.add_all([
        IndexTriHistory(index_name="NIFTY 50", tri_date=date(2024, 1, 1), tri_value=200.0),
        IndexTriHistory(index_name="NIFTY 50", tri_date=date(2024, 1, 3), tri_value=220.0),
    ])
    await db_session.flush()

    res = await compute_twr_series(db_session, uid)
    assert res.has_data is True
    assert len(res.points) == 3
    assert res.points[0].portfolio_index == pytest.approx(1.0)
    # Day 3: (121 − (−10)) / 110 = 1.19090..., linked from day 2's 1.1 → 1.30999...
    assert res.points[-1].portfolio_index == pytest.approx(1.31, abs=1e-2)
    # Nifty normalized to inception: 200→1.0, 220→1.1 (Jan 2 has no TRI → on-or-before 200).
    assert res.points[0].nifty_index == pytest.approx(1.0)
    assert res.points[1].nifty_index == pytest.approx(1.0)
    assert res.points[-1].nifty_index == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_compute_twr_series_no_history(db_session: AsyncSession):
    res = await compute_twr_series(db_session, uuid.uuid4())
    assert res.has_data is False
    assert res.points == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/portfolio/services/tests/test_twr_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_twr_series'`.

- [ ] **Step 3: Implement the adapter**

Append to `app/domains/portfolio/services/twr_service.py`:

```python
import uuid
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models import IndexTriHistory, MfTransaction
from app.domains.portfolio.models.user_portfolio_nav_history import UserPortfolioNavHistory
from app.domains.portfolio.schemas.portfolio import TwrPoint, TwrSeriesResponse
from app.domains.portfolio.services.benchmark_service import (
    EXTERNAL_IN_TYPES,
    EXTERNAL_OUT_TYPES,
    NIFTY_INDEX_NAME,
    build_step_lookup,
)


async def compute_twr_series(db: AsyncSession, user_id: uuid.UUID) -> TwrSeriesResponse:
    """Assemble the user's daily TWR series (portfolio + normalized Nifty 50 TRI)."""
    nav_rows = (
        await db.execute(
            select(
                UserPortfolioNavHistory.recorded_date,
                UserPortfolioNavHistory.total_value,
            )
            .where(UserPortfolioNavHistory.user_id == user_id)
            .order_by(UserPortfolioNavHistory.recorded_date.asc())
        )
    ).all()
    daily_values = [(d, float(v)) for d, v in nav_rows]
    if len(daily_values) < 2:
        return TwrSeriesResponse(has_data=False, points=[])

    txn_rows = (
        await db.execute(
            select(
                MfTransaction.transaction_date,
                MfTransaction.transaction_type,
                MfTransaction.amount,
            ).where(MfTransaction.user_id == user_id)
        )
    ).all()
    cashflows: dict[date, float] = defaultdict(float)
    for txn_date, txn_type, amount in txn_rows:
        type_value = txn_type.value if hasattr(txn_type, "value") else str(txn_type)
        magnitude = abs(float(amount))
        if type_value in EXTERNAL_IN_TYPES:
            cashflows[txn_date] += magnitude
        elif type_value in EXTERNAL_OUT_TYPES:
            cashflows[txn_date] -= magnitude

    tri_rows = (
        await db.execute(
            select(IndexTriHistory.tri_date, IndexTriHistory.tri_value).where(
                IndexTriHistory.index_name == NIFTY_INDEX_NAME
            )
        )
    ).all()
    tri_lookup = build_step_lookup([(d, float(v)) for d, v in tri_rows])
    baseline = tri_lookup(daily_values[0][0])

    wealth = compute_twr_wealth_index(daily_values, dict(cashflows))
    points: list[TwrPoint] = []
    for d, w in wealth:
        tri = tri_lookup(d)
        nifty_index = (tri / baseline) if (tri is not None and baseline) else None
        points.append(TwrPoint(date=d, portfolio_index=w, nifty_index=nifty_index))

    return TwrSeriesResponse(has_data=len(points) >= 2, points=points)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/portfolio/services/tests/test_twr_service.py -v`
Expected: PASS (6 passed total).

- [ ] **Step 5: Commit**

```bash
cd Prozpr_Backend
git add app/domains/portfolio/services/twr_service.py app/domains/portfolio/services/tests/test_twr_service.py
git commit -m "feat(portfolio): TWR DB adapter (nav + cashflows + Nifty TRI)"
```

---

## Task 4: Endpoint `GET /portfolio/twr`

**Files:**
- Modify: `app/domains/portfolio/routers/portfolio_router.py`

- [ ] **Step 1: Add the schema import**

In `app/domains/portfolio/routers/portfolio_router.py`, add `TwrSeriesResponse` to the existing import from `app.domains.portfolio.schemas.portfolio` (the multi-line block starting `from app.domains.portfolio.schemas.portfolio import (`). Insert it alphabetically, e.g. after `RecommendedPlanSnapshotResponse,`:

```python
    RecommendedPlanSnapshotResponse,
    TwrSeriesResponse,
)
```

- [ ] **Step 2: Add the service import**

Add a new import block near the other portfolio service imports:

```python
from app.domains.portfolio.services.twr_service import compute_twr_series
```

- [ ] **Step 3: Add the route**

Add this route immediately after the `get_nav_history` route (after its `return PortfolioNavHistoryResponse(...)` block, before `@router.post("/nav-history/refresh"...)`):

```python
@router.get("/twr", response_model=TwrSeriesResponse)
async def get_twr(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    """Real time-weighted return series — portfolio vs Nifty 50, mutual funds only.

    Returns the full daily growth-of-1 series since inception; the frontend
    rebases per selected range. ``has_data`` is false when there are < 2 days.
    """
    return await compute_twr_series(db, current_user.id)
```

- [ ] **Step 4: Verify the app imports and the route is registered**

Run: `.venv-mac/bin/python -c "from app.main import app; print([r.path for r in app.routes if 'twr' in r.path])"`
Expected: prints a list containing `'/api/v1/portfolio/twr'`.

- [ ] **Step 5: Commit**

```bash
cd Prozpr_Backend
git add app/domains/portfolio/routers/portfolio_router.py
git commit -m "feat(portfolio): GET /portfolio/twr endpoint"
```

---

## Task 5: Frontend API client (`getPortfolioTwr`)

**Files:**
- Modify: `Prozpr_Frontend/src/lib/api.ts`

- [ ] **Step 1: Add types + fetch function**

In `src/lib/api.ts`, add immediately after the `PortfolioDetail` interface (ends at the line with `holdings: { ... }[];` then `}`):

```typescript
export interface TwrPoint {
  date: string;
  portfolio_index: number;
  nifty_index: number | null;
}

export interface TwrSeriesResponse {
  has_data: boolean;
  points: TwrPoint[];
}

/** Real TWR series (portfolio vs Nifty 50, MF-only). Frontend rebases per range. */
export async function getPortfolioTwr(): Promise<TwrSeriesResponse> {
  return request<TwrSeriesResponse>("/portfolio/twr");
}
```

- [ ] **Step 2: Typecheck**

Run (in `Prozpr_Frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
cd Prozpr_Frontend
git add src/lib/api.ts
git commit -m "feat(api): getPortfolioTwr client + types"
```

---

## Task 6: Frontend rebasing core (`twr.ts`) — test-first

**Files:**
- Create: `Prozpr_Frontend/src/lib/twr.ts`
- Test: `Prozpr_Frontend/src/lib/twr.test.ts`

- [ ] **Step 1: Write the failing vitest**

Create `src/lib/twr.test.ts`:

```typescript
import { describe, it, expect } from "vitest";
import { rebaseTwr, windowStartIndex } from "./twr";
import type { TwrPoint } from "./api";

const pts: TwrPoint[] = [
  { date: "2024-01-01", portfolio_index: 1.0, nifty_index: 1.0 },
  { date: "2024-01-02", portfolio_index: 1.1, nifty_index: 1.05 },
  { date: "2024-01-03", portfolio_index: 1.21, nifty_index: 1.1 },
];

describe("rebaseTwr", () => {
  it("rebases to the window start", () => {
    const r = rebaseTwr(pts, 0);
    expect(r.twr).toBe(21);
    expect(r.niftyTwr).toBe(10);
    expect(r.series[0].twr).toBe(0);
    expect(r.series[2].twr).toBe(21);
  });

  it("rebases from a later start index", () => {
    const r = rebaseTwr(pts, 1);
    expect(r.twr).toBe(10);
    expect(r.series[0].twr).toBe(0);
  });

  it("returns null nifty + omits bench when nifty data is missing", () => {
    const noNifty = pts.map((p) => ({ ...p, nifty_index: null }));
    const r = rebaseTwr(noNifty, 0);
    expect(r.niftyTwr).toBeNull();
    expect(r.series[0].bench_nifty50).toBeUndefined();
  });
});

describe("windowStartIndex", () => {
  it("returns 0 for All", () => {
    expect(windowStartIndex(pts, "All", new Date("2024-01-03"))).toBe(0);
  });

  it("finds the first point within a trailing window", () => {
    // 1M window ending 2024-01-31 → cutoff 2024-01-01 → index 0 here.
    expect(windowStartIndex(pts, "1M", new Date("2024-01-31"))).toBe(0);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (in `Prozpr_Frontend/`): `npx vitest run src/lib/twr.test.ts`
Expected: FAIL — cannot find module `./twr` / exports undefined.

- [ ] **Step 3: Implement the pure helpers**

Create `src/lib/twr.ts`:

```typescript
import type { TwrPoint } from "./api";

export type AnalysisRange = "1M" | "3M" | "YTD" | "1Y" | "3Y" | "All";

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

/** Index of the first point at/after the window's start date (All → 0). */
export function windowStartIndex(points: TwrPoint[], range: AnalysisRange, today: Date): number {
  if (range === "All" || points.length === 0) return 0;
  const cutoff = new Date(today);
  if (range === "YTD") {
    cutoff.setMonth(0, 1);
    cutoff.setHours(0, 0, 0, 0);
  } else {
    const days = range === "1M" ? 30 : range === "3M" ? 90 : range === "1Y" ? 365 : 365 * 3;
    cutoff.setDate(cutoff.getDate() - days);
  }
  const cutoffStr = cutoff.toISOString().slice(0, 10);
  const idx = points.findIndex((p) => p.date >= cutoffStr);
  return idx < 0 ? Math.max(0, points.length - 1) : idx;
}

export interface RebasedPoint {
  i: number;
  date: string;
  twr: number;
  bench_nifty50?: number;
}

export interface RebasedTwr {
  twr: number;
  niftyTwr: number | null;
  series: RebasedPoint[];
}

/** Rebase a growth-of-1 series to the window start: value_t / value_start − 1 (as %). */
export function rebaseTwr(points: TwrPoint[], startIdx: number): RebasedTwr {
  const window = points.slice(startIdx);
  if (window.length === 0) return { twr: 0, niftyTwr: null, series: [] };
  const base = window[0];
  const niftyBase = base.nifty_index;
  const series: RebasedPoint[] = window.map((p, i) => {
    const pt: RebasedPoint = {
      i,
      date: p.date,
      twr: round1((p.portfolio_index / base.portfolio_index - 1) * 100),
    };
    if (niftyBase != null && p.nifty_index != null) {
      pt.bench_nifty50 = round1((p.nifty_index / niftyBase - 1) * 100);
    }
    return pt;
  });
  const last = window[window.length - 1];
  const twr = round1((last.portfolio_index / base.portfolio_index - 1) * 100);
  const niftyTwr =
    niftyBase != null && last.nifty_index != null
      ? round1((last.nifty_index / niftyBase - 1) * 100)
      : null;
  return { twr, niftyTwr, series };
}
```

- [ ] **Step 4: Run to verify pass**

Run (in `Prozpr_Frontend/`): `npx vitest run src/lib/twr.test.ts`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd Prozpr_Frontend
git add src/lib/twr.ts src/lib/twr.test.ts
git commit -m "feat(twr): pure range-rebasing helpers + tests"
```

---

## Task 7: Rewire the Returns tab in `PortfolioAnalysisModal.tsx`

**Files:**
- Modify: `Prozpr_Frontend/src/components/dashboard/PortfolioAnalysisModal.tsx`

This task replaces the synthetic Returns logic with the real series. Do the steps in order, then typecheck once at the end.

- [ ] **Step 1: Update imports**

Replace the existing api import line (`import type { PortfolioDetail } from "@/lib/api";`) with:

```typescript
import { getPortfolioTwr, type PortfolioDetail, type TwrSeriesResponse } from "@/lib/api";
import { rebaseTwr, windowStartIndex } from "@/lib/twr";
```

- [ ] **Step 2: Add a fixed Nifty display config**

Replace the entire `BENCHMARKS` array (the `const BENCHMARKS: BenchmarkOption[] = [ ... ];` block and the `type BenchmarkOption = { ... };` above it) with a single minimal config (the multiplier/seed machinery is gone):

```typescript
// Single fixed benchmark — Nifty 50. Value now comes from real Nifty TRI data.
const NIFTY = {
  fullName: "Benchmark: Nifty 50",
  shortName: "Nifty 50",
  color: "hsl(var(--muted-foreground))",
  dash: "2 3",
};
```

- [ ] **Step 3: Delete the synthetic-only helper functions**

Delete these now-unused functions entirely: `pointsForRange`, `rangeScaleFactor`, `synthCurve`, `rangeSpanDays`, and `dateForIndex`. Keep `formatDateTick` and `tickIndicesFor` (still used). Keep `fmtPct`, `fmtInr1`, `fmtInrCompact1`.

- [ ] **Step 4: Add fetch state + effect**

Inside the component, just after the existing `const [infoOpen, setInfoOpen] = useState<"twr" | null>(null);` line, add:

```typescript
  const [twrData, setTwrData] = useState<TwrSeriesResponse | null>(null);
  const [twrLoading, setTwrLoading] = useState(false);
  const [twrError, setTwrError] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setTwrLoading(true);
    setTwrError(false);
    getPortfolioTwr()
      .then((d) => { if (!cancelled) setTwrData(d); })
      .catch(() => { if (!cancelled) setTwrError(true); })
      .finally(() => { if (!cancelled) setTwrLoading(false); });
    return () => { cancelled = true; };
  }, [open]);
```

- [ ] **Step 5: Replace the synthetic returns computation**

Replace the whole synthetic block (from the comment `// Time-weighted return:` through the end of the `returnsSeries = useMemo(...)` block, i.e. the original lines computing `simpleGain`, `fullTwr`, `scaledTwr`, `primaryBenchmark`, `activeBenchmarks`, `benchPctById`, `primaryBench`, `today`, `seriesPoints`, `dateTicks`, `formatXTick`, and `returnsSeries`) with:

```typescript
  const today = useMemo(() => new Date(), []);

  const rebased = useMemo(() => {
    if (!twrData || !twrData.has_data) return null;
    const startIdx = windowStartIndex(twrData.points, range, today);
    return rebaseTwr(twrData.points, startIdx);
  }, [twrData, range, today]);

  const returnsSeries = rebased?.series ?? [];
  const seriesPoints = returnsSeries.length;
  const scaledTwr = rebased?.twr ?? 0;
  const primaryBench = rebased?.niftyTwr ?? null;
  const hasBench = returnsSeries.some((p) => p.bench_nifty50 !== undefined);
  const dateTicks = useMemo(() => tickIndicesFor(seriesPoints), [seriesPoints]);
  const formatXTick = (v: number) => {
    const p = returnsSeries[Number(v)];
    return p ? formatDateTick(range, new Date(p.date)) : "";
  };
```

- [ ] **Step 6: Update `handleExport` for the returns tab**

In `handleExport`, replace the `if (tab === "returns") { ... }` block with:

```typescript
    if (tab === "returns") {
      const rows: (string | number)[][] = [
        ["Metric (Mutual funds)", `${range}`],
        ["Portfolio TWR %", scaledTwr],
        ["Nifty 50 TWR %", primaryBench ?? ""],
      ];
      downloadFile(`portfolio-returns-${ts}.csv`, "text/csv", toCsv(rows));
      return;
    }
```

- [ ] **Step 7: Replace the Returns-tab JSX**

Replace the entire Returns-tab block (`{tab === "returns" && ( ... )}`) with the version below — adds loading/empty states, the "Mutual funds" scope label, real-date axis, and a single Nifty line:

```tsx
                    {/* — Returns tab — */}
                    {tab === "returns" && (
                      <div>
                        <div className="flex items-center gap-2 mb-2">
                          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
                            Returns
                          </p>
                          <span className="text-[9px] rounded-full px-1.5 py-0.5 bg-muted text-muted-foreground">
                            Mutual funds
                          </span>
                        </div>

                        {twrLoading && (
                          <p className="text-[12px] text-muted-foreground py-8 text-center">
                            Loading your returns…
                          </p>
                        )}

                        {!twrLoading && (twrError || !rebased) && (
                          <p className="text-[12px] text-muted-foreground py-8 text-center leading-relaxed">
                            Not enough history yet — import your transactions to see your returns.
                          </p>
                        )}

                        {!twrLoading && !twrError && rebased && (
                          <>
                            <div className="grid grid-cols-2 gap-2">
                              <div className="rounded-xl p-2.5" style={{ border: `1px solid ${HAIRLINE}` }}>
                                <div className="flex items-center gap-1 mb-0.5">
                                  <p className="text-[9px] uppercase tracking-wide text-muted-foreground">TWR</p>
                                  <button
                                    type="button"
                                    onClick={() => setInfoOpen((o) => (o === "twr" ? null : "twr"))}
                                    className="text-muted-foreground hover:text-foreground"
                                    aria-label="About TWR"
                                  >
                                    <Info className="h-3 w-3" />
                                  </button>
                                </div>
                                <p
                                  className="text-base font-semibold leading-tight"
                                  style={{
                                    color: scaledTwr >= 0 ? POSITIVE : NEGATIVE,
                                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                                  }}
                                >
                                  {fmtPct(scaledTwr)}
                                </p>
                                <p className="text-[9px] text-muted-foreground mt-0.5">{range}</p>
                              </div>
                              <div className="rounded-xl p-2.5" style={{ border: `1px solid ${HAIRLINE}` }}>
                                <p className="text-[9px] uppercase tracking-wide text-muted-foreground mb-0.5 leading-tight">
                                  {NIFTY.fullName}
                                </p>
                                <p
                                  className="text-base font-semibold leading-tight"
                                  style={{
                                    color: (primaryBench ?? 0) >= 0 ? POSITIVE : NEGATIVE,
                                    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
                                  }}
                                >
                                  {primaryBench == null ? "—" : fmtPct(primaryBench)}
                                </p>
                                <p className="text-[9px] text-muted-foreground mt-0.5">{range}</p>
                              </div>
                            </div>

                            {infoOpen && (
                              <div className="mt-2 rounded-lg px-3 py-2" style={{ backgroundColor: "hsl(var(--muted) / 0.6)" }}>
                                <p className="text-[11.5px] text-foreground leading-relaxed">
                                  <strong>TWR</strong> measures the portfolio's compounded performance
                                  over time, stripping out the effect of when and how much you
                                  contributed — so it isolates investment performance. (Mutual-fund
                                  holdings only.)
                                </p>
                              </div>
                            )}

                            <p className="text-[10px] uppercase tracking-wide text-muted-foreground mt-4 mb-1.5">
                              Portfolio vs Nifty 50 over {range}
                            </p>
                            <div className="h-[180px] w-full">
                              <ResponsiveContainer width="100%" height="100%">
                                <LineChart data={returnsSeries} margin={{ top: 8, right: 12, left: 12, bottom: 18 }}>
                                  <CartesianGrid stroke={HAIRLINE} vertical={false} />
                                  <XAxis
                                    dataKey="i"
                                    type="number"
                                    domain={[0, Math.max(0, seriesPoints - 1)]}
                                    ticks={dateTicks}
                                    tickFormatter={formatXTick}
                                    tick={{ fontSize: 9, fill: "hsl(var(--muted-foreground))" }}
                                    axisLine={false}
                                    tickLine={false}
                                    tickMargin={6}
                                    height={20}
                                    interval={0}
                                  />
                                  <YAxis
                                    orientation="right"
                                    width={36}
                                    tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
                                    tickFormatter={(v) => `${v}%`}
                                    axisLine={false}
                                    tickLine={false}
                                  />
                                  <ReferenceLine y={0} stroke={HAIRLINE} strokeDasharray="3 3" />
                                  <Tooltip
                                    contentStyle={{
                                      fontSize: 11,
                                      borderRadius: 8,
                                      border: `1px solid ${HAIRLINE}`,
                                      backgroundColor: "hsl(var(--card))",
                                      color: "hsl(var(--foreground))",
                                    }}
                                    labelStyle={{ color: "hsl(var(--foreground))", fontWeight: 600 }}
                                    formatter={(v: number, name: string) => [`${v}%`, name.toUpperCase()]}
                                    labelFormatter={(label) => {
                                      const p = returnsSeries[Number(label)];
                                      return p ? formatDateTick(range, new Date(p.date)) : "";
                                    }}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="twr"
                                    name="TWR"
                                    stroke={USER_LINE}
                                    strokeWidth={2}
                                    dot={false}
                                    isAnimationActive={false}
                                  />
                                  {hasBench && (
                                    <Line
                                      type="monotone"
                                      dataKey="bench_nifty50"
                                      name={NIFTY.shortName}
                                      stroke={NIFTY.color}
                                      strokeWidth={1.75}
                                      strokeDasharray={NIFTY.dash}
                                      dot={false}
                                      isAnimationActive={false}
                                    />
                                  )}
                                </LineChart>
                              </ResponsiveContainer>
                            </div>

                            <div className="flex flex-wrap items-center justify-center gap-x-3 gap-y-1 mt-2 text-[11px]">
                              <span className="inline-flex items-center gap-1.5">
                                <span className="inline-block h-0.5 w-4" style={{ backgroundColor: USER_LINE }} />
                                TWR
                              </span>
                              {hasBench && (
                                <span className="inline-flex items-center gap-1.5">
                                  <span className="inline-block h-0.5 w-4" style={{ backgroundColor: NIFTY.color }} />
                                  {NIFTY.shortName}
                                </span>
                              )}
                            </div>
                          </>
                        )}
                      </div>
                    )}
```

- [ ] **Step 8: Typecheck and lint**

Run (in `Prozpr_Frontend/`): `npx tsc --noEmit && npm run lint`
Expected: no type errors. Fix any "declared but never used" errors by removing the leftover symbol (e.g. a stray reference to a deleted helper). The waterfall tab and its helpers must be untouched.

- [ ] **Step 9: Manual verification**

Run (in `Prozpr_Frontend/`): `npm run dev`, open the app, go to Portfolio → Portfolio analysis → Returns.
Expected:
- A user with built MF history: real TWR + Nifty 50 TWR numbers, a two-line chart with real dates on the X-axis, the "Mutual funds" tag, and range buttons re-slice instantly.
- A user without history: the "Not enough history yet…" empty state.

- [ ] **Step 10: Commit**

```bash
cd Prozpr_Frontend
git add src/components/dashboard/PortfolioAnalysisModal.tsx
git commit -m "feat(portfolio): real TWR in the Returns tab (replaces synthetic data)"
```

---

## Final verification

- [ ] Backend: `.venv-mac/bin/python -m pytest app/domains/portfolio/services/tests/test_twr_service.py -v` → all pass.
- [ ] Frontend: `npx vitest run src/lib/twr.test.ts` → all pass; `npx tsc --noEmit` → clean.
- [ ] `grep -n "synthCurve\|rangeScaleFactor\|0.85" Prozpr_Frontend/src/components/dashboard/PortfolioAnalysisModal.tsx` → no matches.
- [ ] Manual: Returns tab shows real figures (with "Mutual funds" label) for a user with history; empty state otherwise.
