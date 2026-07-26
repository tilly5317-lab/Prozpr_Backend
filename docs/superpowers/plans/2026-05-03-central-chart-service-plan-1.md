# Central Chart Service — Implementation Plan 1 (Foundation + AA charts)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape `app/services/visualization_tools/` into a flat, per-chart registry; relocate the 3 existing AA chart builders; wire the AA chat branch through the central selector with the LLM call running parallel to the formatter; rebuild the AA chart frontend in the editorial-wealth visual language; ship a developer script that regenerates `docs/charts.md` from the registry.

**Architecture:** Registry stays at `app/services/visualization_tools/registry.py` and is the only entry point that `ai_bridge/chart_selector_service` imports. Each chart owns one folder (`<name>/{schema.py, builder.py, tests/}`). A thin per-intent dispatcher (`build_aa.py`) maps chart names → builder calls for the AA flow. `ChatBrain.run_turn`'s AA branch is rewritten to kick off `select_charts()` as `asyncio.create_task` parallel to `dispatch_chat()`, then build and attach payloads in `finalize()`. Frontend `ChartRenderer.tsx` switches on `payload.type` and dispatches to per-chart components, each rebuilt to use `wealth-*` Tailwind tokens and `Instrument Serif` italic titles.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy async / Pydantic v2 / Anthropic via `langchain-anthropic`; React + Vite + Tailwind / Recharts. No new dependencies.

**Project state caveat:** `ailax/` is not a git repository. Each task ends with a **manual checkpoint** (run command, confirm output) instead of `git commit`. To take a rollback snapshot before a task, run `cp -R Prozpr_Backend/app/services/visualization_tools Prozpr_Backend/app/services/visualization_tools.snap-<task-name>` (and similarly for the frontend folder when relevant). Remove on success.

**Spec:** `Prozpr_Backend/docs/superpowers/specs/2026-05-03-central-chart-service-design.md`

**Plan 2 (follows this one):** Migrate the 3 rebalancing charts from dict-shape `ChartSpec` to typed payloads + frontend rewrite, build the 3 net-new charts (`top_bottom_funds`, `profile_dial`, `buy_sell_ledger`), wire the rebalancing branch through the central selector, delete `ai_bridge/rebalancing/{charts,chart_picker}.py` and the on-critical-path picker code in `chat.py`, archive `sub_asset_treemap`. Plan 2 is written after this one ships.

---

## Phase 0 — Scaffolding

### Task 1: Add `_base.py` shared schema base

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/_base.py`

- [ ] **Step 1: Create `_base.py`**

```python
"""Shared base schema for chart payloads.

Every chart's Pydantic payload subclasses ``ChartBase`` so the discriminated
union in ``registry.py`` can rely on a stable shape (``schema_version``,
``title``, ``subtitle``) regardless of which chart it is.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class ChartBase(BaseModel):
    schema_version: Literal["v1"] = "v1"
    title: str
    subtitle: str | None = None
```

- [ ] **Step 2: Verify import works**

Run: `cd Prozpr_Backend && python -c "from app.services.visualization_tools._base import ChartBase, SCHEMA_VERSION; print(SCHEMA_VERSION)"`
Expected: `v1`

- [ ] **Step 3: Manual checkpoint**

Confirm `Prozpr_Backend/app/services/visualization_tools/_base.py` exists and the import command above prints `v1`. Move on.

---

### Task 2: Refactor `registry.py` to per-chart-folder imports (with old paths still working)

**Goal:** Make `registry.py` ready to import from the new flat per-chart locations *without* breaking anything yet. We keep the existing `asset_allocation/*.py` files in place; the new chart folders will be added in tasks 3-5 and 7-9 and re-imported in task 10.

**Files:**
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`
- Modify: `Prozpr_Backend/app/services/visualization_tools/schema.py` (no logic change yet — leave as-is until task 10)

- [ ] **Step 1: Confirm current registry behavior**

Run: `cd Prozpr_Backend && python -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"`
Expected: `['allocation.concentration_risk', 'allocation.current_donut', 'allocation.sub_asset_treemap', 'allocation.target_vs_actual']`

This is the baseline — capture it; any change here is a regression until Task 6.

- [ ] **Step 2: No file edits this task — just snapshot baseline**

Snapshot: `cp -R Prozpr_Backend/app/services/visualization_tools Prozpr_Backend/app/services/visualization_tools.snap-pre-relocate`

This is the rollback point if a later relocate task corrupts the registry.

- [ ] **Step 3: Manual checkpoint**

Confirm the snapshot folder exists: `ls -d Prozpr_Backend/app/services/visualization_tools.snap-pre-relocate`. Move on.

---

### Task 3a: Add shared test conftest for `visualization_tools/tests/`

**Discovery while executing:** the only existing conftest with `db_session`, `fixture_user`, and `fixture_user_with_dob` lives at `app/services/ai_bridge/rebalancing/tests/conftest.py`. Pytest does not auto-share fixtures across sibling directories. Tests in this plan live in two places (`visualization_tools/tests/test_build_aa.py` for the dispatcher, and `visualization_tools/<chart>/tests/test_builder.py` for each chart), so the conftest must live at the common ancestor: `visualization_tools/conftest.py`.

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/tests/__init__.py` (empty)
- Create: `Prozpr_Backend/app/services/visualization_tools/conftest.py`

- [ ] **Step 1: Create `tests/__init__.py` (empty file)**

```bash
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/tests/__init__.py
```

- [ ] **Step 2: Create `visualization_tools/conftest.py` with shared fixtures**

```python
"""Async DB fixtures for visualization_tools tests.

Mirrors the fixture set in ``app/services/ai_bridge/rebalancing/tests/conftest.py``
(in-memory SQLite, full ``Base.metadata`` schema, per-test isolation). Pytest
does not auto-share fixtures across sibling test directories, so we duplicate
the minimal subset needed by chart-builder tests rather than promote them to
a project-level conftest as part of this work.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect imports: register every model with Base.metadata so create_all
# materialises the entire schema (FK targets must exist before children).
import app.models  # noqa: F401
from app.database import Base
from app.models.user import User


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test in-memory SQLite session; engine disposed at teardown."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def fixture_user(db_session: AsyncSession) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"viz_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def fixture_user_with_dob(db_session: AsyncSession) -> User:
    """User with a date_of_birth (some downstream code expects it)."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"viz_dob_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return user
```

- [ ] **Step 3: Smoke-run the conftest collection**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/tests/ --collect-only 2>&1 | tail -10`
Expected: `no tests ran` (the conftest exists but no tests yet); no import errors.

- [ ] **Step 4: Manual checkpoint**

Conftest in place. Move on.

---

### Task 3: Add `build_aa.py` AA-side dispatcher (initially empty)

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/build_aa.py`
- Create: `Prozpr_Backend/app/services/visualization_tools/tests/test_build_aa.py`

- [ ] **Step 1: Write the failing test for empty-name handling**

Create `Prozpr_Backend/app/services/visualization_tools/tests/test_build_aa.py`:

```python
"""Smoke tests for build_aa.build_charts_for_aa."""
from __future__ import annotations

import uuid

import pytest

from app.services.visualization_tools.build_aa import build_charts_for_aa


@pytest.mark.asyncio
async def test_empty_names_returns_empty(db_session):
    user_id = uuid.uuid4()
    out = await build_charts_for_aa(db_session, user_id, [])
    assert out == []


@pytest.mark.asyncio
async def test_unknown_name_skipped(db_session):
    user_id = uuid.uuid4()
    out = await build_charts_for_aa(db_session, user_id, ["does_not_exist"])
    assert out == []
```

The `db_session` fixture is provided by `tests/conftest.py` created in Task 3a.

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/tests/test_build_aa.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_charts_for_aa'` (because the module does not exist yet).

- [ ] **Step 3: Write the dispatcher**

Create `Prozpr_Backend/app/services/visualization_tools/build_aa.py`:

```python
"""AA-side chart builder dispatcher.

Given the list of chart names returned by the selector, call each registered
builder with the AA-shape signature ``(db, user_id)`` and collect the
non-None payloads. Unknown names and builders that produce ``None`` (no data,
no portfolio yet, etc.) are skipped silently — the chat answer renders without
that chart rather than failing.

Rebalancing-shape builders that take a ``RebalancingComputeResponse`` are NOT
dispatched here; they live in ``build_rebalancing.py`` (Plan 2). Names that
belong to the rebalancing flow are quietly skipped here.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.visualization_tools.registry import CHART_TOOLS

logger = logging.getLogger(__name__)


async def build_charts_for_aa(
    db: AsyncSession, user_id: uuid.UUID, chart_names: list[str]
) -> list[Any]:
    """Build AA-flow chart payloads for the given names. Returns Pydantic
    payload instances; the caller dumps them via ``model_dump(mode='json')``.
    """
    out: list[Any] = []
    for name in chart_names:
        tool = CHART_TOOLS.get(name)
        if tool is None:
            logger.info("build_aa: unknown chart name %s skipped", name)
            continue
        try:
            payload = await tool.builder(db, user_id)
        except TypeError:
            # Wrong signature — this chart wants the rebalancing-shape input
            # (``response`` only). It belongs to ``build_rebalancing``;
            # silently skip in the AA dispatcher.
            logger.info("build_aa: %s requires rebalancing input; skipped", name)
            continue
        except Exception as exc:
            logger.warning("build_aa: builder %s failed (%s); skipping", name, exc)
            continue
        if payload is not None:
            out.append(payload)
    return out
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/tests/test_build_aa.py -v`
Expected: PASS — both `test_empty_names_returns_empty` and `test_unknown_name_skipped`.

- [ ] **Step 5: Manual checkpoint**

Confirm tests pass and `build_aa.py` is in place. Move on.

---

## Phase 1 — Relocate the 3 existing AA charts

Each of the next three tasks follows the same shape: create the new flat folder, split the chart's piece of `schema.py` into `<name>/schema.py`, move the builder unchanged into `<name>/builder.py`, write a smoke test for the builder, register the new path in `registry.py`, leave the old `asset_allocation/<file>.py` in place for now (deleted in Task 6).

### Task 4: Relocate `current_donut`

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/current_donut/__init__.py`
- Create: `Prozpr_Backend/app/services/visualization_tools/current_donut/schema.py`
- Create: `Prozpr_Backend/app/services/visualization_tools/current_donut/builder.py`
- Create: `Prozpr_Backend/app/services/visualization_tools/current_donut/tests/__init__.py`
- Create: `Prozpr_Backend/app/services/visualization_tools/current_donut/tests/test_builder.py`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py` (swap import + name)

- [ ] **Step 1: Write the failing builder test**

Create `current_donut/tests/test_builder.py`:

```python
"""Smoke test for the current_donut chart builder."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest


@pytest.mark.asyncio
async def test_returns_none_when_no_portfolio(db_session, fixture_user_with_dob):
    from app.services.visualization_tools.current_donut.builder import (
        build_current_donut,
    )

    out = await build_current_donut(db_session, fixture_user_with_dob.id)
    assert out is None


@pytest.mark.asyncio
async def test_donut_slices_match_allocations(
    db_session, fixture_user_with_portfolio_and_allocations
):
    from app.services.visualization_tools.current_donut.builder import (
        build_current_donut,
    )

    user = fixture_user_with_portfolio_and_allocations
    out = await build_current_donut(db_session, user.id)
    assert out is not None
    assert out.type == "current_donut"
    labels = {s.label for s in out.slices}
    assert labels == {"Equity", "Debt", "Cash"}
    pcts = {s.label: s.percentage for s in out.slices}
    assert pcts["Equity"] + pcts["Debt"] + pcts["Cash"] == pytest.approx(100.0, abs=0.5)
    assert out.total_value > 0
```

The fixture `fixture_user_with_portfolio_and_allocations` is created next.

- [ ] **Step 2: Append the portfolio fixture to the conftest from Task 3a**

Append to `Prozpr_Backend/app/services/visualization_tools/conftest.py` (created in Task 3a):

```python
# ── Portfolio fixtures (used by current_donut, target_vs_actual) ──
import uuid as _uuid
from decimal import Decimal

from app.models.portfolio import Portfolio, PortfolioAllocation


@pytest_asyncio.fixture
async def fixture_user_with_portfolio_and_allocations(
    db_session, fixture_user_with_dob,
):
    """Adds a Portfolio + 3 PortfolioAllocation rows (Equity/Debt/Cash) summing to 100%."""
    portfolio = Portfolio(
        id=_uuid.uuid4(),
        user_id=fixture_user_with_dob.id,
        is_primary=True,
    )
    db_session.add(portfolio)
    await db_session.flush()
    for cls, amount, pct in (
        ("Equity", Decimal("700000"), Decimal("70.00")),
        ("Debt", Decimal("250000"), Decimal("25.00")),
        ("Cash", Decimal("50000"), Decimal("5.00")),
    ):
        db_session.add(PortfolioAllocation(
            id=_uuid.uuid4(),
            portfolio_id=portfolio.id,
            asset_class=cls,
            amount=amount,
            allocation_percentage=pct,
        ))
    await db_session.flush()
    return fixture_user_with_dob
```

(The `_uuid` alias avoids shadowing the existing `uuid` import at the top of the conftest.)

- [ ] **Step 3: Run test to confirm it fails**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/current_donut/tests/test_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.visualization_tools.current_donut.builder'`.

- [ ] **Step 4: Write `current_donut/schema.py`**

```python
"""Pydantic payload — current_donut chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.visualization_tools._base import ChartBase


class DonutSlice(BaseModel):
    label: str
    value: float
    percentage: float = Field(..., ge=0, le=100)
    color_hint: str | None = None


class CurrentDonut(ChartBase):
    type: Literal["current_donut"] = "current_donut"
    total_value: float
    slices: list[DonutSlice]
```

- [ ] **Step 5: Write `current_donut/builder.py`** (logic unchanged from `asset_allocation/current_allocation.py`)

```python
"""Chart builder — current asset allocation donut.

Reads PortfolioAllocation rows for a user's primary portfolio and returns a
``CurrentDonut`` payload. Returns None when the user has no portfolio
or no allocation rows yet.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioAllocation
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.current_donut.schema import (
    CurrentDonut,
    DonutSlice,
)


async def build_current_donut(
    db: AsyncSession, user_id: uuid.UUID
) -> CurrentDonut | None:
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = (
        select(PortfolioAllocation)
        .where(PortfolioAllocation.portfolio_id == portfolio.id)
        .order_by(PortfolioAllocation.allocation_percentage.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None

    slices = [
        DonutSlice(
            label=row.asset_class,
            value=float(row.amount),
            percentage=float(row.allocation_percentage),
        )
        for row in rows
    ]
    total_value = sum(s.value for s in slices)

    return CurrentDonut(
        title="Your asset mix",
        subtitle="Allocation across asset classes",
        total_value=total_value,
        slices=slices,
    )
```

Title and subtitle copy match the editorial-wealth visual mockup approved in the brainstorm.

- [ ] **Step 6: Update `registry.py` to use the new path and the new flat name**

Open `Prozpr_Backend/app/services/visualization_tools/registry.py` and replace the `current_donut`-related lines:

```python
# OLD (delete these lines)
from app.services.visualization_tools.asset_allocation.current_allocation import (
    build_current_allocation_donut,
)
# ...
"allocation.current_donut": ChartTool(
    name="allocation.current_donut",
    description=(...),
    builder=build_current_allocation_donut,
),

# NEW
from app.services.visualization_tools.current_donut.builder import build_current_donut
# ...
"current_donut": ChartTool(
    name="current_donut",
    description=(
        "Donut chart of the user's current asset allocation, broken down by class "
        "(equity, debt, gold, liquid, cash, etc.) with the total portfolio value at "
        "the centre. Use whenever the user asks about their current portfolio "
        "composition, asset mix, allocation breakdown, holdings split, or wants to "
        "see what they own at a glance — including follow-ups like 'show me again' "
        "or 'what's my mix'."
    ),
    builder=build_current_donut,
),
```

Leave the other 3 entries (`allocation.concentration_risk`, `allocation.sub_asset_treemap`, `allocation.target_vs_actual`) as-is for now — they get migrated in tasks 5-6.

- [ ] **Step 7: Run the registry sanity check**

Run: `cd Prozpr_Backend && python -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"`
Expected: `['allocation.concentration_risk', 'allocation.sub_asset_treemap', 'allocation.target_vs_actual', 'current_donut']`

The mix of old and new names during migration is intentional; both shapes coexist for a couple of tasks.

- [ ] **Step 8: Run the new builder test**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/current_donut/tests/test_builder.py -v`
Expected: PASS — both tests.

- [ ] **Step 9: Manual checkpoint**

Confirm builder tests pass AND the registry shows the renamed entry. Move on.

---

### Task 5: Relocate `concentration_risk`

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/concentration_risk/{__init__.py, schema.py, builder.py}`
- Create: `Prozpr_Backend/app/services/visualization_tools/concentration_risk/tests/{__init__.py, test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Write the failing builder test**

Create `concentration_risk/tests/test_builder.py`:

```python
"""Smoke test for the concentration_risk chart builder."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.portfolio import PortfolioHolding


@pytest.mark.asyncio
async def test_returns_none_when_no_holdings(
    db_session, fixture_user_with_portfolio_and_allocations
):
    from app.services.visualization_tools.concentration_risk.builder import (
        build_concentration_risk,
    )
    user = fixture_user_with_portfolio_and_allocations
    out = await build_concentration_risk(db_session, user.id)
    assert out is None  # the fixture has allocations but no holdings


@pytest.mark.asyncio
async def test_top_n_severity(db_session, fixture_user_with_portfolio_and_allocations):
    from app.services.visualization_tools.concentration_risk.builder import (
        build_concentration_risk,
    )
    user = fixture_user_with_portfolio_and_allocations
    # Look up the portfolio created by the fixture
    from sqlalchemy import select
    from app.models.portfolio import Portfolio
    portfolio = (await db_session.execute(
        select(Portfolio).where(Portfolio.user_id == user.id)
    )).scalar_one()

    # 6 holdings: top-1 = 60%, others ~8%, so severity should be "watch" or "act"
    values = [Decimal(s) for s in ("600000", "80000", "80000", "80000", "80000", "80000")]
    for i, v in enumerate(values):
        db_session.add(PortfolioHolding(
            id=uuid.uuid4(),
            portfolio_id=portfolio.id,
            instrument_name=f"Fund {i+1}",
            current_value=v,
        ))
    await db_session.flush()

    out = await build_concentration_risk(db_session, user.id)
    assert out is not None
    assert out.type == "concentration_risk"
    assert out.top_n == 5
    assert out.severity in {"watch", "act"}  # depending on threshold
    assert out.top_holdings[0].label == "Fund 1"
    assert out.rest_count == 1
```

- [ ] **Step 2: Run test to confirm it fails**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/concentration_risk/tests/test_builder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `concentration_risk/schema.py`**

```python
"""Pydantic payload — concentration_risk chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.visualization_tools._base import ChartBase


class ConcentrationHolding(BaseModel):
    label: str
    value: float
    percentage: float = Field(..., ge=0, le=100)


class ConcentrationRisk(ChartBase):
    type: Literal["concentration_risk"] = "concentration_risk"
    headline: str
    severity: Literal["ok", "watch", "act"]
    top_n: int
    top_holdings: list[ConcentrationHolding]
    rest_percentage: float
    rest_count: int
```

- [ ] **Step 4: Write `concentration_risk/builder.py`** (logic unchanged from `asset_allocation/concentration_risk.py`)

```python
"""Chart builder — concentration risk (top-N holdings vs the rest)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioHolding
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.concentration_risk.schema import (
    ConcentrationHolding,
    ConcentrationRisk,
)

_TOP_N = 5
_OK_THRESHOLD = 50.0
_WATCH_THRESHOLD = 70.0


def _severity_for(top_pct: float) -> str:
    if top_pct < _OK_THRESHOLD:
        return "ok"
    if top_pct < _WATCH_THRESHOLD:
        return "watch"
    return "act"


async def build_concentration_risk(
    db: AsyncSession, user_id: uuid.UUID
) -> ConcentrationRisk | None:
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = (
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio.id)
        .order_by(PortfolioHolding.current_value.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None

    total = sum(float(r.current_value) for r in rows)
    if total <= 0:
        return None

    top_rows = rows[:_TOP_N]
    rest_rows = rows[_TOP_N:]

    top_holdings = [
        ConcentrationHolding(
            label=r.instrument_name,
            value=float(r.current_value),
            percentage=float(r.current_value) / total * 100.0,
        )
        for r in top_rows
    ]
    top_pct = sum(h.percentage for h in top_holdings)
    rest_pct = max(0.0, 100.0 - top_pct)
    rest_count = len(rest_rows)
    severity = _severity_for(top_pct)

    if rest_count == 0:
        headline = (
            f"Your portfolio holds only {len(top_holdings)} fund"
            f"{'s' if len(top_holdings) != 1 else ''} — highly concentrated"
        )
    else:
        headline = f"Top {_TOP_N} funds = {top_pct:.0f}% of portfolio"

    return ConcentrationRisk(
        title="Concentration in your top holdings",
        subtitle=f"{rest_count} other funds make up the rest",
        headline=headline,
        severity=severity,
        top_n=len(top_holdings),
        top_holdings=top_holdings,
        rest_percentage=rest_pct,
        rest_count=rest_count,
    )
```

- [ ] **Step 5: Update `registry.py`**

Replace the `allocation.concentration_risk` block with:

```python
# Add to imports near the top:
from app.services.visualization_tools.concentration_risk.builder import (
    build_concentration_risk,
)

# Replace the old entry in CHART_TOOLS:
"concentration_risk": ChartTool(
    name="concentration_risk",
    description=(
        "Horizontal bar chart of the user's top-5 holdings by value plus a 'rest' "
        "bar, with a severity badge (diversified / watch / concentrated). Use when "
        "the user asks about concentration, diversification, biggest holdings, "
        "single-fund risk, 'how spread out is my portfolio', or whether they're "
        "over-exposed to any one fund."
    ),
    builder=build_concentration_risk,
),
```

Delete the old `from app.services.visualization_tools.asset_allocation.concentration_risk import build_concentration_risk` import line.

- [ ] **Step 6: Run tests, verify pass**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/concentration_risk/tests/test_builder.py -v`
Expected: PASS — both tests.

Run: `cd Prozpr_Backend && python -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"`
Expected: `['allocation.sub_asset_treemap', 'allocation.target_vs_actual', 'concentration_risk', 'current_donut']`

- [ ] **Step 7: Manual checkpoint**

Tests pass. Move on.

---

### Task 6: Relocate `target_vs_actual` and retire `sub_asset_treemap`

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/target_vs_actual/{__init__.py, schema.py, builder.py}`
- Create: `Prozpr_Backend/app/services/visualization_tools/target_vs_actual/tests/{__init__.py, test_builder.py}`
- Move (don't delete): `Prozpr_Backend/app/services/visualization_tools/asset_allocation/` → `Prozpr_Backend/app/services/visualization_tools/archive/asset_allocation_pre_relocate/`
- Delete: `Prozpr_Backend/app/services/visualization_tools/schema.py` (after the move)
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py` (drop `sub_asset_treemap`, add new path for `target_vs_actual`)

- [ ] **Step 1: Write the failing builder test**

Create `target_vs_actual/tests/test_builder.py`:

```python
"""Smoke test for the target_vs_actual chart builder."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot


@pytest.mark.asyncio
async def test_returns_none_when_no_target_snapshot(
    db_session, fixture_user_with_portfolio_and_allocations
):
    from app.services.visualization_tools.target_vs_actual.builder import (
        build_target_vs_actual,
    )
    out = await build_target_vs_actual(
        db_session, fixture_user_with_portfolio_and_allocations.id
    )
    assert out is None


@pytest.mark.asyncio
async def test_pairs_target_and_actual(
    db_session, fixture_user_with_portfolio_and_allocations
):
    user = fixture_user_with_portfolio_and_allocations
    snap = PortfolioAllocationSnapshot(
        id=uuid.uuid4(),
        user_id=user.id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation={"rows": [
            {"asset_class": "Equity", "weight_pct": 60.0},
            {"asset_class": "Debt", "weight_pct": 35.0},
            {"asset_class": "Cash", "weight_pct": 5.0},
        ]},
    )
    db_session.add(snap)
    await db_session.flush()

    from app.services.visualization_tools.target_vs_actual.builder import (
        build_target_vs_actual,
    )
    out = await build_target_vs_actual(db_session, user.id)
    assert out is not None
    assert out.type == "target_vs_actual"
    by_cls = {b.asset_class: b for b in out.bars}
    assert by_cls["Equity"].target_pct == 60.0
    assert by_cls["Equity"].actual_pct == 70.0
    assert by_cls["Equity"].drift_pct == pytest.approx(10.0)
```

- [ ] **Step 2: Run test, confirm it fails**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/target_vs_actual/tests/test_builder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write `target_vs_actual/schema.py`**

```python
"""Pydantic payload — target_vs_actual chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class TargetVsActualBar(BaseModel):
    asset_class: str
    target_pct: float
    actual_pct: float
    drift_pct: float


class TargetVsActual(ChartBase):
    type: Literal["target_vs_actual"] = "target_vs_actual"
    bars: list[TargetVsActualBar]
```

- [ ] **Step 4: Write `target_vs_actual/builder.py`** (logic unchanged from old file)

```python
"""Chart builder — target vs actual allocation bars."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.models.portfolio import PortfolioAllocation
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.target_vs_actual.schema import (
    TargetVsActual,
    TargetVsActualBar,
)


def _extract_target_pcts(snapshot_allocation: dict) -> dict[str, float]:
    rows = snapshot_allocation.get("rows") or []
    out: dict[str, float] = {}
    for row in rows:
        cls = row.get("asset_class")
        pct = row.get("weight_pct")
        if isinstance(cls, str) and isinstance(pct, (int, float)):
            out[cls] = float(pct)
    return out


async def _latest_ideal_targets(
    db: AsyncSession, user_id: uuid.UUID
) -> dict[str, float] | None:
    stmt = (
        select(PortfolioAllocationSnapshot)
        .where(
            PortfolioAllocationSnapshot.user_id == user_id,
            PortfolioAllocationSnapshot.snapshot_kind == PortfolioSnapshotKind.IDEAL,
        )
        .order_by(PortfolioAllocationSnapshot.effective_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    targets = _extract_target_pcts(row.allocation or {})
    return targets or None


async def build_target_vs_actual(
    db: AsyncSession, user_id: uuid.UUID
) -> TargetVsActual | None:
    targets = await _latest_ideal_targets(db, user_id)
    if not targets:
        return None

    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    actual_stmt = select(PortfolioAllocation).where(
        PortfolioAllocation.portfolio_id == portfolio.id
    )
    actual_rows = (await db.execute(actual_stmt)).scalars().all()
    if not actual_rows:
        return None

    actual: dict[str, float] = {
        r.asset_class: float(r.allocation_percentage) for r in actual_rows
    }

    asset_classes = list(targets.keys())
    for cls in actual.keys():
        if cls not in asset_classes:
            asset_classes.append(cls)

    bars = []
    for cls in asset_classes:
        target_pct = targets.get(cls, 0.0)
        actual_pct = actual.get(cls, 0.0)
        bars.append(
            TargetVsActualBar(
                asset_class=cls,
                target_pct=target_pct,
                actual_pct=actual_pct,
                drift_pct=actual_pct - target_pct,
            )
        )
    bars.sort(key=lambda b: max(b.target_pct, b.actual_pct), reverse=True)

    return TargetVsActual(
        title="Target vs your actual mix",
        subtitle="Drift per asset class",
        bars=bars,
    )
```

- [ ] **Step 5: Update `registry.py` — drop `sub_asset_treemap`, swap `target_vs_actual` to new path**

After this step, the registry should contain only the 3 relocated AA charts:

```python
"""Central registry of chart tools."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.services.visualization_tools.concentration_risk.builder import (
    build_concentration_risk,
)
from app.services.visualization_tools.current_donut.builder import build_current_donut
from app.services.visualization_tools.target_vs_actual.builder import (
    build_target_vs_actual,
)


@dataclass(frozen=True)
class ChartTool:
    name: str
    description: str
    builder: Callable[..., Any]


CHART_TOOLS: dict[str, ChartTool] = {
    "current_donut": ChartTool(
        name="current_donut",
        description=(
            "Donut chart of the user's current asset allocation, broken down by class "
            "(equity, debt, gold, liquid, cash, etc.) with the total portfolio value at "
            "the centre. Use whenever the user asks about their current portfolio "
            "composition, asset mix, allocation breakdown, holdings split, or wants to "
            "see what they own at a glance — including follow-ups like 'show me again' "
            "or 'what's my mix'."
        ),
        builder=build_current_donut,
    ),
    "concentration_risk": ChartTool(
        name="concentration_risk",
        description=(
            "Horizontal bar chart of the user's top-5 holdings by value plus a 'rest' "
            "bar, with a severity badge (diversified / watch / concentrated). Use when "
            "the user asks about concentration, diversification, biggest holdings, "
            "single-fund risk, 'how spread out is my portfolio', or whether they're "
            "over-exposed to any one fund."
        ),
        builder=build_concentration_risk,
    ),
    "target_vs_actual": ChartTool(
        name="target_vs_actual",
        description=(
            "Paired bar chart comparing the user's target (ideal/recommended) "
            "allocation against their actual current allocation, per asset class, with "
            "drift labels. Use when the user asks about whether they're on-track vs "
            "their plan, drift from target, rebalancing needs, gap to ideal, or how "
            "their actual mix compares to what was recommended."
        ),
        builder=build_target_vs_actual,
    ),
}
```

- [ ] **Step 6: Move retired files into `archive/` instead of deleting**

Run:

```bash
mkdir -p Prozpr_Backend/app/services/visualization_tools/archive
mv Prozpr_Backend/app/services/visualization_tools/asset_allocation \
   Prozpr_Backend/app/services/visualization_tools/archive/asset_allocation_pre_relocate
mv Prozpr_Backend/app/services/visualization_tools/schema.py \
   Prozpr_Backend/app/services/visualization_tools/archive/schema_pre_relocate.py
```

This satisfies the "reversible deletes in non-git directories" rule. Files are out of the import path but recoverable.

- [ ] **Step 7: Run all visualization_tools tests + registry sanity**

Run: `cd Prozpr_Backend && pytest app/services/visualization_tools/ -v`
Expected: PASS — both `current_donut`, `concentration_risk`, `target_vs_actual`, and `tests/test_build_aa.py` suites green.

Run: `cd Prozpr_Backend && python -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"`
Expected: `['concentration_risk', 'current_donut', 'target_vs_actual']`

- [ ] **Step 8: Run the broader test suite to catch any other code that imported the old paths**

Run: `cd Prozpr_Backend && pytest app/ -v 2>&1 | tail -40`
Expected: PASS overall, or the only failures are tests we will repair as part of later tasks (note them but do not fix here unless trivial).

If anything imports `app.services.visualization_tools.asset_allocation.*` or `app.services.visualization_tools.schema`, change it to the new paths in this task. The `archive/selector_pre_aibridge.py` is already in `archive/` and unused — leave it alone.

- [ ] **Step 9: Manual checkpoint**

Confirm visualization_tools test suite green, registry shows only the 3 AA charts, and full backend test run shows no broken imports.

---

## Phase 2 — Wire selector parallel into AA branch of `brain.py`

### Task 7: Add the AA-branch chart-selector wiring

**Files:**
- Modify: `Prozpr_Backend/app/services/chat_core/brain.py:143-154` (the `asset_allocation` branch)
- Create: `Prozpr_Backend/app/services/chat_core/tests/test_brain_aa_charts.py`

- [ ] **Step 1: Write a failing brain integration test**

Create `Prozpr_Backend/app/services/chat_core/tests/test_brain_aa_charts.py`:

```python
"""Brain integration — AA branch produces chart_payloads."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.chat_core.brain import ChatBrain
from app.services.chat_core.types import ChatTurnInput


@pytest.mark.asyncio
async def test_aa_turn_attaches_chart_payloads(
    db_session, fixture_user_with_portfolio_and_allocations,
):
    user = fixture_user_with_portfolio_and_allocations
    turn = ChatTurnInput(
        db=db_session,
        effective_user_id=user.id,
        session_id=None,
        user_question="show me my asset mix",
        conversation_history=[],
        client_context=None,
        user_ctx=user,
    )

    # 1) Force intent → asset_allocation
    # 2) Make the selector return current_donut
    # 3) Stub dispatch_chat to return a benign result
    fake_classification = type(
        "C",
        (),
        {
            "intent": type("I", (), {"value": "asset_allocation"})(),
            "confidence": 0.99,
            "reasoning": "test",
            "out_of_scope_message": None,
        },
    )()
    with patch(
        "app.services.chat_core.brain.classify_user_message",
        return_value=fake_classification,
    ), patch(
        "app.services.chat_core.brain.select_charts",
        return_value=["current_donut"],
    ), patch(
        "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
        return_value=type(
            "R",
            (),
            {
                "text": "Your portfolio is 70% equity.",
                "snapshot_id": None,
                "rebalancing_recommendation_id": None,
            },
        )(),
    ):
        result = await ChatBrain().run_turn(turn)

    assert result.intent == "asset_allocation"
    assert result.chart_payloads is not None
    assert len(result.chart_payloads) == 1
    assert result.chart_payloads[0]["type"] == "current_donut"


@pytest.mark.asyncio
async def test_aa_turn_charts_empty_on_selector_failure(
    db_session, fixture_user_with_portfolio_and_allocations,
):
    """Selector returns []. The reply still ships, just without charts."""
    user = fixture_user_with_portfolio_and_allocations
    turn = ChatTurnInput(
        db=db_session,
        effective_user_id=user.id,
        session_id=None,
        user_question="hi",
        conversation_history=[],
        client_context=None,
        user_ctx=user,
    )
    fake_classification = type(
        "C",
        (),
        {
            "intent": type("I", (), {"value": "asset_allocation"})(),
            "confidence": 0.99,
            "reasoning": "test",
            "out_of_scope_message": None,
        },
    )()
    with patch(
        "app.services.chat_core.brain.classify_user_message",
        return_value=fake_classification,
    ), patch(
        "app.services.chat_core.brain.select_charts",
        return_value=[],
    ), patch(
        "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
        return_value=type(
            "R",
            (),
            {
                "text": "Hello.",
                "snapshot_id": None,
                "rebalancing_recommendation_id": None,
            },
        )(),
    ):
        result = await ChatBrain().run_turn(turn)

    assert result.chart_payloads is None
```

If `Prozpr_Backend/app/services/chat_core/tests/__init__.py` does not exist, create it empty.

- [ ] **Step 2: Run test, confirm it fails**

Run: `cd Prozpr_Backend && pytest app/services/chat_core/tests/test_brain_aa_charts.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_charts' from 'app.services.chat_core.brain'` (because we have not added the import yet).

- [ ] **Step 3: Modify `brain.py` — add imports + rewrite the AA branch**

Open `Prozpr_Backend/app/services/chat_core/brain.py`. Add at the top of the imports:

```python
from app.services.ai_bridge.chart_selector_service import select_charts
from app.services.visualization_tools.build_aa import build_charts_for_aa
```

Replace the AA branch (currently `if intent_value == "asset_allocation":` at line ~143-154) with:

```python
if intent_value == "asset_allocation":
    # Local import — chat handler self-registers via @register at import time.
    from app.services.ai_bridge.asset_allocation import chat as _aa_chat  # noqa: F401
    from app.services.ai_bridge.chat_dispatcher import dispatch_chat
    flow.append("dispatch_chat → asset_allocation_chat")
    trace_line("next module: chat_dispatcher → asset_allocation_chat")

    # Kick off chart selection in parallel with the formatter LLM.
    selector_task = asyncio.create_task(
        select_charts(turn.user_question, intent_value)
    )

    result = await dispatch_chat(intent_value, turn_context)

    # Wait for the selector with a soft 3s ceiling — if it's still running
    # because the formatter returned fast, cancel and ship without charts
    # rather than block the response.
    try:
        chart_names = await asyncio.wait_for(selector_task, timeout=3.0)
    except asyncio.TimeoutError:
        logger.warning("AA chart selector timed out; shipping without charts")
        selector_task.cancel()
        chart_names = []
    except Exception as exc:
        logger.warning("AA chart selector failed (%s); shipping without charts", exc)
        chart_names = []

    chart_payloads: list[dict[str, Any]] | None = None
    if chart_names and db is not None:
        try:
            payloads = await build_charts_for_aa(db, uid, chart_names)
            if payloads:
                chart_payloads = [p.model_dump(mode="json") for p in payloads]
        except Exception:
            logger.exception("AA chart builder failed; shipping without charts")

    return await finalize(
        result.text,
        ideal_allocation_snapshot_id=result.snapshot_id,
        ideal_allocation_rebalancing_id=result.rebalancing_recommendation_id,
        chart_payloads=chart_payloads,
    )
```

- [ ] **Step 4: Run AA-branch tests**

Run: `cd Prozpr_Backend && pytest app/services/chat_core/tests/test_brain_aa_charts.py -v`
Expected: PASS — both `test_aa_turn_attaches_chart_payloads` and `test_aa_turn_charts_empty_on_selector_failure`.

- [ ] **Step 5: Run full chat_core test suite**

Run: `cd Prozpr_Backend && pytest app/services/chat_core/ -v`
Expected: PASS — no regressions in existing brain tests.

- [ ] **Step 6: Manual checkpoint**

AA branch now ships with charts. Move on.

---

## Phase 3 — Frontend rebuild for the 3 AA charts

The current frontend's `ChartRenderer.tsx` switches on `payload.chart_type` and dispatches to 3 rebalancing components. After this phase it switches on `payload.type` and dispatches to the new AA components plus the still-old rebalancing ones (rebal stays on `chart_type` until Plan 2 rewrites them). We add a tolerance: if the payload has `type` use it; if it has `chart_type` use that as a fallback.

### Task 8: Add `_base.ts` and update the dispatcher

**Files:**
- Create: `Prozpr_Frontend/src/components/visualization_tools/_base.ts`
- Modify: `Prozpr_Frontend/src/components/visualization_tools/types.ts` (add the 3 AA payload types)
- Modify: `Prozpr_Frontend/src/components/visualization_tools/ChartRenderer.tsx`
- Modify: `Prozpr_Frontend/src/components/visualization_tools/index.ts`

- [ ] **Step 1: Create `_base.ts`**

```ts
// Shared base — every chart payload carries these.
export interface ChartBase {
  schema_version: "v1";
  title: string;
  subtitle?: string | null;
}
```

- [ ] **Step 2: Add the 3 AA payload types to `types.ts`**

Open `Prozpr_Frontend/src/components/visualization_tools/types.ts` and add (above the existing `ChartPayload` union):

```ts
import type { ChartBase } from "./_base";

// current_donut

export interface DonutSlice {
  label: string;
  value: number;
  percentage: number;
  color_hint?: string | null;
}

export interface CurrentDonut extends ChartBase {
  type: "current_donut";
  total_value: number;
  slices: DonutSlice[];
}

// concentration_risk

export interface ConcentrationHolding {
  label: string;
  value: number;
  percentage: number;
}

export interface ConcentrationRisk extends ChartBase {
  type: "concentration_risk";
  headline: string;
  severity: "ok" | "watch" | "act";
  top_n: number;
  top_holdings: ConcentrationHolding[];
  rest_percentage: number;
  rest_count: number;
}

// target_vs_actual

export interface TargetVsActualBar {
  asset_class: string;
  target_pct: number;
  actual_pct: number;
  drift_pct: number;
}

export interface TargetVsActual extends ChartBase {
  type: "target_vs_actual";
  bars: TargetVsActualBar[];
}
```

Then update the `ChartPayload` union at the bottom to include all 6 (the 3 old rebalancing dict-shape + the 3 new AA typed):

```ts
export type ChartPayload =
  | CurrentDonut
  | ConcentrationRisk
  | TargetVsActual
  | CategoryGapBar
  | PlannedDonut
  | TaxCostBar;
```

Leave `CategoryGapBar`/`PlannedDonut`/`TaxCostBar` exactly as they are; Plan 2 rewrites them.

- [ ] **Step 3: Update `ChartRenderer.tsx`**

Replace the file with:

```tsx
import type { ChartPayload } from "./types";
import { CurrentDonut } from "./CurrentDonut/Chart";
import { ConcentrationRisk } from "./ConcentrationRisk/Chart";
import { TargetVsActual } from "./TargetVsActual/Chart";
import { CategoryGapBar } from "./rebalancing/CategoryGapBar";
import { PlannedDonut } from "./rebalancing/PlannedDonut";
import { TaxCostBar } from "./rebalancing/TaxCostBar";

interface ChartRendererProps {
  payload: ChartPayload;
}

// Backwards-compatible discriminator: AA charts use `type`, rebalancing
// charts (until Plan 2) use `chart_type`. Read whichever is present.
function discriminator(payload: ChartPayload): string {
  return (payload as { type?: string }).type
    ?? (payload as { chart_type?: string }).chart_type
    ?? "";
}

export function ChartRenderer({ payload }: ChartRendererProps) {
  switch (discriminator(payload)) {
    case "current_donut":
      return <CurrentDonut payload={payload as Extract<ChartPayload, { type: "current_donut" }>} />;
    case "concentration_risk":
      return <ConcentrationRisk payload={payload as Extract<ChartPayload, { type: "concentration_risk" }>} />;
    case "target_vs_actual":
      return <TargetVsActual payload={payload as Extract<ChartPayload, { type: "target_vs_actual" }>} />;
    case "category_gap_bar":
      return <CategoryGapBar payload={payload as Extract<ChartPayload, { chart_type: "category_gap_bar" }>} />;
    case "planned_donut":
      return <PlannedDonut payload={payload as Extract<ChartPayload, { chart_type: "planned_donut" }>} />;
    case "tax_cost_bar":
      return <TaxCostBar payload={payload as Extract<ChartPayload, { chart_type: "tax_cost_bar" }>} />;
    default:
      return null;
  }
}
```

- [ ] **Step 4: Update `index.ts` exports**

```ts
export { ChartRenderer } from "./ChartRenderer";
export type {
  ChartPayload,
  // AA charts
  CurrentDonut,
  DonutSlice,
  ConcentrationRisk,
  ConcentrationHolding,
  TargetVsActual,
  TargetVsActualBar,
  // Rebalancing charts (legacy shape until Plan 2)
  CategoryGapBar,
  PlannedDonut,
  TaxCostBar,
  NamedSeries,
  PlannedDonutSlice,
} from "./types";
```

- [ ] **Step 5: Type-check the frontend**

Run: `cd Prozpr_Frontend && npx tsc --noEmit 2>&1 | head -30`
Expected: SUCCESS, or only the expected "module not found" errors for `./CurrentDonut/Chart`, `./ConcentrationRisk/Chart`, `./TargetVsActual/Chart` (those folders are created in the next 3 tasks). Note any unexpected errors and fix them now.

- [ ] **Step 6: Manual checkpoint**

Move on. The frontend will not build yet — the 3 missing components are added next.

---

### Task 9: Build the `CurrentDonut` chart component (editorial-wealth style)

**Files:**
- Create: `Prozpr_Frontend/src/components/visualization_tools/CurrentDonut/Chart.tsx`
- Create: `Prozpr_Frontend/src/components/visualization_tools/CurrentDonut/types.ts`

- [ ] **Step 1: Create `CurrentDonut/types.ts`**

```ts
export type { CurrentDonut as CurrentDonutPayload, DonutSlice } from "../types";
```

- [ ] **Step 2: Create `CurrentDonut/Chart.tsx`** (editorial-wealth style)

```tsx
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import type { CurrentDonutPayload } from "./types";
import { formatInrCompact } from "@/lib/utils";

// Asset-class palette mirrors the spec: Equity = wealth-blue, Debt = wealth-navy,
// Real Estate = wealth-green, Cash = wealth-amber. Falls back to a wealth-tinted
// rotation for other classes.
const ASSET_PALETTE: Record<string, string> = {
  Equity: "hsl(215 60% 48%)",
  Debt: "hsl(222 47% 14%)",
  "Real Estate": "hsl(160 50% 38%)",
  Cash: "hsl(38 80% 48%)",
  Gold: "hsl(38 80% 48%)",
  Liquid: "hsl(220 35% 28%)",
};

const FALLBACK = [
  "hsl(215 60% 48%)",
  "hsl(222 47% 14%)",
  "hsl(160 50% 38%)",
  "hsl(38 80% 48%)",
  "hsl(220 35% 28%)",
];

function colorFor(label: string, i: number): string {
  return ASSET_PALETTE[label] ?? FALLBACK[i % FALLBACK.length];
}

export function CurrentDonut({ payload }: { payload: CurrentDonutPayload }) {
  const data = payload.slices.map((s, i) => ({
    name: s.label,
    value: s.value,
    percentage: s.percentage,
    color: colorFor(s.label, i),
  }));

  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-wealth">
      <h3 className="font-display italic text-foreground text-xl leading-tight mb-1">
        {payload.title}
      </h3>
      {payload.subtitle ? (
        <p className="text-xs text-muted-foreground mb-4">{payload.subtitle}</p>
      ) : null}

      <div className="flex items-center gap-5">
        <div className="relative h-32 w-32 shrink-0">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                cx="50%"
                cy="50%"
                innerRadius={38}
                outerRadius={60}
                paddingAngle={3}
                dataKey="value"
                strokeWidth={0}
              >
                {data.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: number, _name, item) => [
                  `${formatInrCompact(value)} (${item.payload.percentage.toFixed(1)}%)`,
                  item.payload.name,
                ]}
                contentStyle={{ fontSize: "11px", borderRadius: "6px" }}
              />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
              Total
            </span>
            <span className="text-base font-bold text-foreground tabular-nums">
              {formatInrCompact(payload.total_value)}
            </span>
          </div>
        </div>

        <div className="flex-1 space-y-1">
          {data.map((item) => (
            <div
              key={item.name}
              className="flex items-center justify-between border-b border-dashed border-border/60 py-2 last:border-b-0"
            >
              <div className="flex items-center gap-2">
                <span
                  className="h-2.5 w-2.5 rounded"
                  style={{ backgroundColor: item.color }}
                />
                <span className="text-xs text-foreground font-medium">
                  {item.name}
                </span>
              </div>
              <span className="text-xs font-semibold text-foreground tabular-nums">
                {item.percentage.toFixed(1)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Type-check the frontend**

Run: `cd Prozpr_Frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: One fewer "module not found" error than before. The remaining ones are for `ConcentrationRisk` and `TargetVsActual`, fixed in tasks 10 and 11.

- [ ] **Step 4: Manual checkpoint**

`CurrentDonut/Chart.tsx` exists and the dispatcher resolves it without typescript errors specific to this file.

---

### Task 10: Build the `ConcentrationRisk` chart component

**Files:**
- Create: `Prozpr_Frontend/src/components/visualization_tools/ConcentrationRisk/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create `ConcentrationRisk/types.ts`**

```ts
export type {
  ConcentrationRisk as ConcentrationRiskPayload,
  ConcentrationHolding,
} from "../types";
```

- [ ] **Step 2: Create `ConcentrationRisk/Chart.tsx`**

```tsx
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from "recharts";
import type { ConcentrationRiskPayload } from "./types";
import { formatInrCompact } from "@/lib/utils";

const SEVERITY_PILL: Record<
  "ok" | "watch" | "act",
  { label: string; bg: string; fg: string }
> = {
  ok: {
    label: "Diversified",
    bg: "hsl(160 30% 93%)",
    fg: "hsl(160 50% 28%)",
  },
  watch: {
    label: "Watch",
    bg: "hsl(38 60% 93%)",
    fg: "hsl(38 80% 30%)",
  },
  act: {
    label: "Concentrated",
    bg: "hsl(0 86% 95%)",
    fg: "hsl(0 72% 38%)",
  },
};

export function ConcentrationRisk({ payload }: { payload: ConcentrationRiskPayload }) {
  const pill = SEVERITY_PILL[payload.severity];

  const rows = [
    ...payload.top_holdings.map((h) => ({
      name: h.label,
      value: h.value,
      percentage: h.percentage,
      isRest: false,
    })),
    ...(payload.rest_count > 0
      ? [
          {
            name: `Other ${payload.rest_count} fund${payload.rest_count === 1 ? "" : "s"}`,
            value: payload.rest_percentage,
            percentage: payload.rest_percentage,
            isRest: true,
          },
        ]
      : []),
  ];

  const chartHeight = Math.max(180, rows.length * 36);

  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-wealth">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="font-display italic text-foreground text-xl leading-tight">
          {payload.title}
        </h3>
        <span
          className="text-xs font-semibold rounded-full px-2.5 py-0.5 shrink-0"
          style={{ backgroundColor: pill.bg, color: pill.fg }}
        >
          {pill.label}
        </span>
      </div>
      <p className="text-xs text-muted-foreground mb-4">{payload.headline}</p>

      <div style={{ width: "100%", height: chartHeight }}>
        <ResponsiveContainer>
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 12, left: 4, bottom: 4 }}
          >
            <XAxis type="number" hide />
            <YAxis
              type="category"
              dataKey="name"
              width={120}
              tick={{ fontSize: 11, fill: "hsl(var(--foreground))" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.04)" }}
              formatter={(_value: number, _name, item) => [
                `${item.payload.percentage.toFixed(1)}%`,
                item.payload.name,
              ]}
              contentStyle={{ fontSize: "11px", borderRadius: "6px" }}
            />
            <Bar
              dataKey="percentage"
              radius={[0, 6, 6, 0]}
              barSize={14}
            >
              {rows.map((r) => (
                <Cell
                  key={r.name}
                  fill={r.isRest ? "hsl(220 13% 80%)" : "hsl(215 60% 48%)"}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {payload.rest_count > 0 ? (
        <p className="text-[11px] text-muted-foreground mt-2 tabular-nums">
          {`Top ${payload.top_n} = ${(100 - payload.rest_percentage).toFixed(0)}% · ${formatInrCompact(
            payload.top_holdings.reduce((sum, h) => sum + h.value, 0)
          )}`}
        </p>
      ) : null}
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run: `cd Prozpr_Frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: One fewer error vs Task 9. Only TargetVsActual missing now.

- [ ] **Step 4: Manual checkpoint**

Move on.

---

### Task 11: Build the `TargetVsActual` chart component

**Files:**
- Create: `Prozpr_Frontend/src/components/visualization_tools/TargetVsActual/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create `TargetVsActual/types.ts`**

```ts
export type {
  TargetVsActual as TargetVsActualPayload,
  TargetVsActualBar,
} from "../types";
```

- [ ] **Step 2: Create `TargetVsActual/Chart.tsx`**

```tsx
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Legend,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { TargetVsActualPayload } from "./types";

export function TargetVsActual({ payload }: { payload: TargetVsActualPayload }) {
  const data = payload.bars.map((b) => ({
    name: b.asset_class,
    Target: b.target_pct,
    Actual: b.actual_pct,
    drift: b.drift_pct,
  }));

  const chartHeight = Math.max(180, payload.bars.length * 44);

  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-wealth">
      <h3 className="font-display italic text-foreground text-xl leading-tight mb-1">
        {payload.title}
      </h3>
      {payload.subtitle ? (
        <p className="text-xs text-muted-foreground mb-4">{payload.subtitle}</p>
      ) : null}

      <div style={{ width: "100%", height: chartHeight }}>
        <ResponsiveContainer>
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 16, left: 4, bottom: 4 }}
            barGap={4}
          >
            <CartesianGrid horizontal={false} stroke="hsl(var(--border))" strokeOpacity={0.4} />
            <XAxis
              type="number"
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              tickFormatter={(v: number) => `${v.toFixed(0)}%`}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="name"
              width={100}
              tick={{ fontSize: 11, fill: "hsl(var(--foreground))" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.04)" }}
              formatter={(value: number, name) => [`${value.toFixed(1)}%`, name]}
              contentStyle={{ fontSize: "11px", borderRadius: "6px" }}
            />
            <Legend
              wrapperStyle={{ fontSize: "11px", paddingTop: "4px" }}
              iconSize={10}
            />
            <Bar dataKey="Target" fill="hsl(222 47% 14%)" radius={[0, 4, 4, 0]} barSize={10} />
            <Bar dataKey="Actual" fill="hsl(215 60% 48%)" radius={[0, 4, 4, 0]} barSize={10} />
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
        {payload.bars.map((b) => {
          const drift = b.drift_pct;
          const sign = drift >= 0 ? "+" : "";
          const tone =
            Math.abs(drift) < 2
              ? "text-muted-foreground"
              : drift > 0
                ? "text-[hsl(160_50%_28%)]"
                : "text-destructive";
          return (
            <div key={b.asset_class} className="flex items-center justify-between text-[11px]">
              <span className="text-muted-foreground">{b.asset_class}</span>
              <span className={`tabular-nums font-semibold ${tone}`}>
                {sign}
                {drift.toFixed(1)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Full frontend type-check**

Run: `cd Prozpr_Frontend && npx tsc --noEmit 2>&1 | head -20`
Expected: SUCCESS — no errors. If anything else surfaces, fix here before committing.

- [ ] **Step 4: Frontend build smoke**

Run: `cd Prozpr_Frontend && npm run build 2>&1 | tail -20`
Expected: build success. (Skip if vite is unavailable in this env; tsc already covered the contract.)

- [ ] **Step 5: Manual checkpoint**

Frontend builds with all 3 AA chart components.

---

## Phase 4 — Tooling: regen `docs/charts.md`

### Task 12: Add `scripts/regen_chart_docs.py` and generate the catalog reference

**Files:**
- Create: `Prozpr_Backend/scripts/regen_chart_docs.py`
- Create: `Prozpr_Backend/docs/charts.md` (generated)

- [ ] **Step 1: Write the script**

Create `Prozpr_Backend/scripts/regen_chart_docs.py`:

```python
"""Regenerate ``docs/charts.md`` from ``CHART_TOOLS``.

Run from ``Prozpr_Backend/``::

    python scripts/regen_chart_docs.py

Writes a Markdown reference of every registered chart — name, selector
description, and JSON-schema of the typed payload. Designed to be a manual
developer step (not a pre-commit hook); commit the regenerated file alongside
any chart changes so reviewers can read the catalogue.
"""
from __future__ import annotations

import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent  # Prozpr_Backend/
DOCS_FILE = REPO_ROOT / "docs" / "charts.md"

# Make ``app.*`` importable when run as a script.
sys.path.insert(0, str(REPO_ROOT))


def _payload_class_for(builder: Any) -> Any | None:
    """Inspect the builder's return annotation to find its ChartBase subclass."""
    sig = inspect.signature(builder)
    ret = sig.return_annotation
    if ret is inspect.Signature.empty:
        return None
    # Handle "X | None" annotations
    args = getattr(ret, "__args__", None)
    if args:
        for arg in args:
            if arg is type(None):
                continue
            return arg
    return ret


def _front_matter() -> str:
    return (
        "<!-- Generated by scripts/regen_chart_docs.py — do not edit by hand. -->\n"
        "<!-- To refresh: cd Prozpr_Backend && python scripts/regen_chart_docs.py -->\n\n"
    )


def _section_for(name: str, tool: Any) -> str:
    payload_cls = _payload_class_for(tool.builder)
    schema_block = ""
    if payload_cls is not None:
        try:
            schema = payload_cls.model_json_schema()
            schema_block = (
                "\n**Payload schema:**\n\n"
                f"```json\n{json.dumps(schema, indent=2)}\n```\n"
            )
        except Exception as exc:
            schema_block = f"\n**Payload schema:** _unavailable_ ({exc})\n"

    builder_path = (
        f"{tool.builder.__module__}.{tool.builder.__qualname__}"
    )
    return (
        f"## `{name}`\n\n"
        f"**Selector description (read by the LLM):**\n\n"
        f"> {tool.description}\n\n"
        f"**Builder:** `{builder_path}`\n"
        f"{schema_block}\n"
        "---\n\n"
    )


def main() -> None:
    from app.services.visualization_tools.registry import CHART_TOOLS

    body = ["# Chart Catalogue\n\n",
            _front_matter(),
            f"_{len(CHART_TOOLS)} charts registered._\n\n"]
    for name in sorted(CHART_TOOLS.keys()):
        body.append(_section_for(name, CHART_TOOLS[name]))

    DOCS_FILE.parent.mkdir(parents=True, exist_ok=True)
    DOCS_FILE.write_text("".join(body))
    print(f"wrote {DOCS_FILE.relative_to(REPO_ROOT)} ({len(CHART_TOOLS)} charts)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the script**

Run: `cd Prozpr_Backend && python scripts/regen_chart_docs.py`
Expected: prints `wrote docs/charts.md (3 charts)` (or whatever count `CHART_TOOLS` has after Phase 1).

- [ ] **Step 3: Inspect the generated file**

Run: `head -40 Prozpr_Backend/docs/charts.md`
Expected: starts with `# Chart Catalogue`, lists `concentration_risk`, `current_donut`, `target_vs_actual` in alphabetical order, includes their selector descriptions and JSON-schema blocks.

- [ ] **Step 4: Add a brief README pointer**

Open `Prozpr_Backend/README.md` and add (under whatever "Development" section exists, or near the top):

```markdown
### Regenerate chart catalogue docs

After adding or editing a chart in `app/services/visualization_tools/`,
regenerate the human-readable reference:

\`\`\`bash
cd Prozpr_Backend
python scripts/regen_chart_docs.py
\`\`\`

Commits the updated `docs/charts.md` alongside the code change.
```

(Skip this step if `Prozpr_Backend/README.md` does not exist; the script docstring is enough.)

- [ ] **Step 5: Manual checkpoint**

`docs/charts.md` exists, lists 3 charts, looks readable.

---

## Phase 5 — End-to-end smoke + cleanup

### Task 13: Run the full backend test suite

- [ ] **Step 1: Run all tests**

Run: `cd Prozpr_Backend && pytest app/ -v 2>&1 | tail -50`
Expected: PASS — all visualization_tools, chart_core, and ai_bridge tests green.

If anything fails because something imported the old `app.services.visualization_tools.asset_allocation.*` or `app.services.visualization_tools.schema` paths, fix that import to the new flat-folder location and re-run.

- [ ] **Step 2: Drop the pre-relocate snapshot if everything is green**

```bash
rm -rf Prozpr_Backend/app/services/visualization_tools.snap-pre-relocate
```

(Keep the snapshot if any test was unstable — it's the rollback point.)

- [ ] **Step 3: Manual checkpoint**

All tests green; AA chat ships with charts; docs catalogue regeneratable.

---

## Self-review against the spec

The plan covers:

- ✅ `_base.py` shared schema (Task 1)
- ✅ Per-chart flat folders for AA charts (Tasks 4, 5, 6)
- ✅ AA-side dispatcher `build_aa.py` (Task 3)
- ✅ Registry rewritten to import from new paths (Tasks 4-6)
- ✅ `sub_asset_treemap` retired into `archive/` (Task 6)
- ✅ Old `asset_allocation/` folder + old `schema.py` archived, not deleted (Task 6)
- ✅ AA branch wired through selector parallel to formatter LLM with 3s soft ceiling (Task 7)
- ✅ Frontend `_base.ts` + 3 AA chart components in editorial-wealth style (Tasks 8-11)
- ✅ Frontend dispatcher tolerant of both `type` (new) and `chart_type` (legacy) (Task 8)
- ✅ Auto-generated `docs/charts.md` via `scripts/regen_chart_docs.py` (Task 12)
- ✅ End-to-end test sweep (Task 13)

Deferred to Plan 2 (out of scope here, listed in the spec's non-goals or covered separately):
- Migrate the 3 rebalancing charts (`category_gap_bar`, `planned_donut`, `tax_cost_bar`) from dict-shape to typed payloads
- Wire the rebalancing branch through the central selector
- Delete `ai_bridge/rebalancing/{charts,chart_picker}.py`
- Build the 3 net-new charts (`top_bottom_funds`, `profile_dial`, `buy_sell_ledger`)
- Frontend rewrite of the 3 rebalancing components to editorial-wealth style
- Live-LLM boundary eval for the chart selector (depends on the upcoming shared eval harness)

These are explicitly deferred so Plan 1 stays scoped to "wire AA through the new architecture" without entangling the still-on-critical-path rebalancing picker.
