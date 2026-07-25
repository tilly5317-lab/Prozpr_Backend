# Central Chart Service — Implementation Plan 2 (Rebalancing migration + new charts + cleanup)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the 3 rebalancing charts (`category_gap_bar`, `planned_donut`, `tax_cost_bar`) from dict-shape `ChartSpec` to typed Pydantic payloads in flat per-chart folders; build the 3 net-new chart builders (`top_bottom_funds`, `profile_dial`, `buy_sell_ledger`); wire the rebalancing branch through the central selector parallel to the formatter LLM; delete the dead `ai_bridge/rebalancing/{chart_picker,charts}.py` files and the on-critical-path picker plumbing in `chat.py`; rebuild the matching frontend chart components in the editorial-wealth language; drop the legacy `chart_type` fallback in `ChartRenderer.tsx`; regenerate `docs/charts.md` (catalog grows from 3 to 9).

**Architecture:** Mirrors Plan 1's pattern. Each chart owns one folder under `visualization_tools/<name>/{schema.py, builder.py, tests/}`. AA-shape builders take `(db, user_id)` and dispatch through `build_aa.py`; rebal-shape builders take `(response: RebalancingComputeResponse)` and dispatch through a new `build_rebalancing.py`. `brain.py`'s rebalancing branch kicks off `select_charts()` as `asyncio.create_task` after the engine completes, runs parallel to the formatter, then dispatches build via `build_rebalancing.py`. Old `ai_bridge/rebalancing/charts.py` + `chart_picker.py` move to an archive folder (per the "reversible deletes" rule).

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy async / Pydantic v2 / Anthropic via `langchain-anthropic`; React + Vite + Tailwind / Recharts. No new dependencies.

**Project state caveat:** `ailax/` is treated as non-git. Each task ends with a **manual checkpoint** (run command, confirm output) instead of `git commit`. To take a rollback snapshot before a task, run `cp -R Prozpr_Backend/app/services/visualization_tools Prozpr_Backend/app/services/visualization_tools.snap-<task-name>` (similarly for other paths). Remove on success.

**Spec:** `Prozpr_Backend/docs/superpowers/specs/2026-05-03-central-chart-service-design.md`

**Plan 1 (already shipped):** Foundation + 3 AA charts relocated + AA chat wired + frontend `chat/visualization_tools/` + 3 AA chart components in editorial-wealth style + `scripts/regen_chart_docs.py`. Registry currently has 3 entries (`current_donut`, `concentration_risk`, `target_vs_actual`).

**Data sources discovered for the new charts:**
- `top_bottom_funds`: uses `PortfolioHolding.return_1y` (already on the model). No XIRR computation needed for v1.
- `profile_dial`: uses `EffectiveRiskAssessment.effective_risk_score` (Numeric(7,4) on `effective_risk_assessments` table; one row per user). Score is a number; the frontend renders the dial position from it.
- `buy_sell_ledger`: takes `RebalancingComputeResponse`; reads `subgroup.actions[]` rows (`FundRowAfterStep5`) for per-fund buy/sell/sub_category info.

---

## Phase 0 — Rebalancing-side dispatcher

### Task 1: Add `build_rebalancing.py` dispatcher

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/build_rebalancing.py`
- Create: `Prozpr_Backend/app/services/visualization_tools/tests/test_build_rebalancing.py`

- [ ] **Step 1: Write the failing test**

Create `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/tests/test_build_rebalancing.py`:

```python
"""Smoke tests for build_rebalancing.build_charts_for_rebalancing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.visualization_tools.build_rebalancing import (
    build_charts_for_rebalancing,
)


@pytest.mark.asyncio
async def test_empty_names_returns_empty():
    fake_response = MagicMock()
    out = await build_charts_for_rebalancing(fake_response, [])
    assert out == []


@pytest.mark.asyncio
async def test_unknown_name_skipped():
    fake_response = MagicMock()
    out = await build_charts_for_rebalancing(fake_response, ["does_not_exist"])
    assert out == []
```

- [ ] **Step 2: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/tests/test_build_rebalancing.py -v
```

Expected: ImportError for `build_charts_for_rebalancing`.

- [ ] **Step 3: Write the dispatcher**

Create `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/build_rebalancing.py`:

```python
"""Rebalancing-side chart builder dispatcher.

Mirrors ``build_aa.py`` but for rebal-shape builders that take a
``RebalancingComputeResponse`` instead of ``(db, user_id)``. The chat-side
caller (``brain.py``'s rebalancing branch) hands us the engine response and
the chart names returned by the selector; we look each name up in the central
registry, call the builder with the response, and collect non-None payloads.

AA-shape builders (those expecting ``(db, user_id)``) are silently skipped
here — they belong to ``build_aa.py``.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.visualization_tools.registry import CHART_TOOLS

logger = logging.getLogger(__name__)


async def build_charts_for_rebalancing(
    response: Any, chart_names: list[str]
) -> list[Any]:
    """Build rebalancing-flow chart payloads for the given names."""
    out: list[Any] = []
    for name in chart_names:
        tool = CHART_TOOLS.get(name)
        if tool is None:
            logger.info("build_rebalancing: unknown chart name %s skipped", name)
            continue
        try:
            payload = await tool.builder(response)
        except TypeError:
            # Wrong signature — this chart wants the AA-shape input
            # (``db, user_id``). Belongs to ``build_aa``; skip.
            logger.info("build_rebalancing: %s requires AA input; skipped", name)
            continue
        except Exception as exc:
            logger.warning("build_rebalancing: builder %s failed (%s); skipping", name, exc)
            continue
        if payload is not None:
            out.append(payload)
    return out
```

- [ ] **Step 4: Run tests, verify pass**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/tests/test_build_rebalancing.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Manual checkpoint**

Move on.

---

## Phase 1 — Migrate the 3 rebalancing charts to typed payloads

Each task follows the same shape: create the new flat folder, write a typed Pydantic schema (replacing the dict shape from `ai_bridge/rebalancing/charts.py`), port the computer logic to a builder that takes `RebalancingComputeResponse`, write a smoke test, and register in `CHART_TOOLS`. **Builder signature changes from sync `(response) -> ChartSpec | None` to async `(response) -> Payload | None`** — the dispatcher above expects awaitable. Keep the bucketing/aggregation logic identical.

### Task 2: Migrate `category_gap_bar`

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/category_gap_bar/{__init__.py, schema.py, builder.py, tests/__init__.py, tests/test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Create folder + empty inits**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/category_gap_bar/tests
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/category_gap_bar/__init__.py
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/category_gap_bar/tests/__init__.py
```

- [ ] **Step 2: Write the failing builder test**

Create `tests/test_builder.py`:

```python
"""Smoke test for the category_gap_bar chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _make_action(present_inr: float, buy: float = 0, sell: float = 0):
    a = MagicMock()
    a.present_allocation_inr = Decimal(str(present_inr))
    a.pass1_buy_amount = Decimal(str(buy)) if buy else None
    a.pass1_sell_amount = Decimal(str(sell)) if sell else None
    a.pass2_sell_amount = None
    a.pass1_realised_stcg = None
    a.pass1_realised_ltcg = None
    a.exit_load_amount = None
    a.sub_category = "Large Cap Fund"
    return a


def _make_response():
    """A minimal RebalancingComputeResponse-shaped MagicMock with one subgroup + one action."""
    response = MagicMock()
    subgroup = MagicMock()
    subgroup.asset_subgroup = "low_beta_equities"
    subgroup.goal_target_inr = Decimal("1100000")
    subgroup.actions = [_make_action(present_inr=1000000, buy=100000)]
    response.subgroups = [subgroup]
    return response


@pytest.mark.asyncio
async def test_returns_none_when_no_actions():
    from app.services.visualization_tools.category_gap_bar.builder import (
        build_category_gap_bar,
    )
    response = MagicMock()
    response.subgroups = []
    out = await build_category_gap_bar(response)
    assert out is None


@pytest.mark.asyncio
async def test_produces_one_category():
    from app.services.visualization_tools.category_gap_bar.builder import (
        build_category_gap_bar,
    )
    out = await build_category_gap_bar(_make_response())
    assert out is not None
    assert out.type == "category_gap_bar"
    assert out.categories == ["Large Cap Fund"]
    series_by_name = {s.name: s.values for s in out.series}
    assert "Current" in series_by_name
    assert "Target" in series_by_name
    assert "Plan" in series_by_name
    assert series_by_name["Current"][0] == 1000000.0
    assert series_by_name["Plan"][0] == 1100000.0  # current - sell + buy = 1000000 - 0 + 100000
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/category_gap_bar/tests/test_builder.py -v
```

Expected: ImportError.

- [ ] **Step 4: Write `category_gap_bar/schema.py`**

```python
"""Pydantic payload — category_gap_bar chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class NamedSeries(BaseModel):
    name: str
    values: list[float]


class CategoryGapBar(ChartBase):
    type: Literal["category_gap_bar"] = "category_gap_bar"
    categories: list[str]
    series: list[NamedSeries]
    caption: str | None = None
```

- [ ] **Step 5: Write `category_gap_bar/builder.py`**

The bucketing logic is lifted verbatim from `ai_bridge/rebalancing/charts.py` — but exposed as `async def` and producing the typed payload.

```python
"""Chart builder — Current / Target / Plan per SEBI sub_category.

Best for: 'how off am I?' / 'what's the gap?' rebalancing questions.
Bucketing logic mirrors the original ``ai_bridge/rebalancing/charts.py``
``compute_category_gap_bar`` (now archived).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.category_gap_bar.schema import (
    CategoryGapBar,
    NamedSeries,
)

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
    RebalancingComputeResponse,
)


@dataclass
class _Bucket:
    asset_subgroup: str
    sub_category: str
    actions: list["FundRowAfterStep5"]

    @property
    def current(self) -> Decimal:
        return sum((r.present_allocation_inr for r in self.actions), Decimal(0))

    @property
    def buy_total(self) -> Decimal:
        return sum(((r.pass1_buy_amount or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def sell_total(self) -> Decimal:
        return sum(
            (((r.pass1_sell_amount or Decimal(0)) + (r.pass2_sell_amount or Decimal(0)))
             for r in self.actions),
            Decimal(0),
        )

    @property
    def planned_final(self) -> Decimal:
        return self.current - self.sell_total + self.buy_total


def _bucketise(response: "RebalancingComputeResponse") -> list[_Bucket]:
    by_key: dict[tuple[str, str], _Bucket] = {}
    for s in response.subgroups:
        for row in s.actions:
            buy = row.pass1_buy_amount or Decimal(0)
            sell = (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))
            if row.present_allocation_inr <= 0 and buy <= 0 and sell <= 0:
                continue
            key = (s.asset_subgroup, row.sub_category)
            bucket = by_key.get(key)
            if bucket is None:
                bucket = _Bucket(
                    asset_subgroup=s.asset_subgroup,
                    sub_category=row.sub_category,
                    actions=[],
                )
                by_key[key] = bucket
            bucket.actions.append(row)
    return list(by_key.values())


def _bucket_target(
    bucket: _Bucket,
    all_buckets: list[_Bucket],
    response: "RebalancingComputeResponse",
) -> Decimal:
    parent = next(
        (s for s in response.subgroups if s.asset_subgroup == bucket.asset_subgroup),
        None,
    )
    if parent is None:
        return Decimal(0)
    siblings = [b for b in all_buckets if b.asset_subgroup == bucket.asset_subgroup]
    if len(siblings) <= 1:
        return parent.goal_target_inr
    total_planned = sum((b.planned_final for b in siblings), Decimal(0))
    if total_planned > 0:
        return parent.goal_target_inr * (bucket.planned_final / total_planned)
    return parent.goal_target_inr / len(siblings)


def _f(amount: Decimal) -> float:
    return float(amount)


async def build_category_gap_bar(response: Any) -> CategoryGapBar | None:
    """Build the Current/Target/Plan grouped-bar payload, or None if no actions."""
    buckets = _bucketise(response)
    if not buckets:
        return None

    rows = []
    for b in buckets:
        target = _bucket_target(b, buckets, response)
        gap = abs(target - b.current)
        rows.append((b, target, gap))
    rows.sort(key=lambda x: -x[2])

    return CategoryGapBar(
        title="Where you are vs. where you should be",
        subtitle="Current holdings, target allocation, and the post-rebalance plan",
        caption=None,
        categories=[b.sub_category for b, _, _ in rows],
        series=[
            NamedSeries(name="Current", values=[_f(b.current) for b, _, _ in rows]),
            NamedSeries(name="Target", values=[_f(t) for _, t, _ in rows]),
            NamedSeries(name="Plan", values=[_f(b.planned_final) for b, _, _ in rows]),
        ],
    )
```

- [ ] **Step 6: Update `registry.py` — add the new entry**

Open `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/registry.py`. Add to the imports:

```python
from app.services.visualization_tools.category_gap_bar.builder import build_category_gap_bar
from app.services.visualization_tools.category_gap_bar.schema import CategoryGapBar
```

Add to `CHART_TOOLS` after the AA entries (anywhere is fine; alphabetical preferred):

```python
    "category_gap_bar": ChartTool(
        name="category_gap_bar",
        description=(
            "Grouped horizontal bar chart showing Current / Target / Plan allocation "
            "(in ₹) per SEBI sub-category. Use when the user asks about gaps, drift, "
            "'how off am I', 'what should I be holding', or generic 'rebalance my "
            "portfolio' with no specific framing — this is the default chart for "
            "rebalancing questions."
        ),
        builder=build_category_gap_bar,
        payload_cls=CategoryGapBar,
    ),
```

- [ ] **Step 7: Run tests + registry sanity**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/category_gap_bar/tests/test_builder.py -v
python3 -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"
```

Expected: 2 passed; registry shows `['category_gap_bar', 'concentration_risk', 'current_donut', 'target_vs_actual']`.

- [ ] **Step 8: Manual checkpoint**

Move on.

---

### Task 3: Migrate `planned_donut`

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/planned_donut/{__init__.py, schema.py, builder.py, tests/__init__.py, tests/test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Create folder + inits**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/planned_donut/tests
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/planned_donut/__init__.py
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/planned_donut/tests/__init__.py
```

- [ ] **Step 2: Write the failing builder test**

Create `tests/test_builder.py`:

```python
"""Smoke test for the planned_donut chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _make_action(present_inr: float, buy: float = 0, sell: float = 0,
                 sub_category: str = "Large Cap Fund"):
    a = MagicMock()
    a.present_allocation_inr = Decimal(str(present_inr))
    a.pass1_buy_amount = Decimal(str(buy)) if buy else None
    a.pass1_sell_amount = Decimal(str(sell)) if sell else None
    a.pass2_sell_amount = None
    a.sub_category = sub_category
    return a


def _make_response_with_two_categories():
    response = MagicMock()
    subgroup = MagicMock()
    subgroup.asset_subgroup = "low_beta_equities"
    subgroup.actions = [
        _make_action(700000, buy=100000, sub_category="Large Cap Fund"),
        _make_action(300000, sell=50000, sub_category="Mid Cap Fund"),
    ]
    response.subgroups = [subgroup]
    return response


@pytest.mark.asyncio
async def test_returns_none_when_all_zero_planned():
    from app.services.visualization_tools.planned_donut.builder import (
        build_planned_donut,
    )
    response = MagicMock()
    response.subgroups = []
    out = await build_planned_donut(response)
    assert out is None


@pytest.mark.asyncio
async def test_slices_sorted_descending():
    from app.services.visualization_tools.planned_donut.builder import (
        build_planned_donut,
    )
    out = await build_planned_donut(_make_response_with_two_categories())
    assert out is not None
    assert out.type == "planned_donut"
    assert len(out.slices) == 2
    # Large Cap = 700k - 0 + 100k = 800k; Mid Cap = 300k - 50k + 0 = 250k
    assert out.slices[0].label == "Large Cap Fund"
    assert out.slices[0].value == 800000.0
    assert out.slices[1].value == 250000.0
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/planned_donut/tests/test_builder.py -v
```

- [ ] **Step 4: Write `planned_donut/schema.py`**

```python
"""Pydantic payload — planned_donut chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class PlannedDonutSlice(BaseModel):
    label: str
    value: float


class PlannedDonut(ChartBase):
    type: Literal["planned_donut"] = "planned_donut"
    slices: list[PlannedDonutSlice]
    caption: str | None = None
```

- [ ] **Step 5: Write `planned_donut/builder.py`**

```python
"""Chart builder — share of planned-final allocation by SEBI sub-category.

Best for: 'what does my portfolio look like after rebalancing?' questions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.planned_donut.schema import (
    PlannedDonut,
    PlannedDonutSlice,
)

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
)


@dataclass
class _Bucket:
    sub_category: str
    actions: list["FundRowAfterStep5"]

    @property
    def current(self) -> Decimal:
        return sum((r.present_allocation_inr for r in self.actions), Decimal(0))

    @property
    def buy_total(self) -> Decimal:
        return sum(((r.pass1_buy_amount or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def sell_total(self) -> Decimal:
        return sum(
            (((r.pass1_sell_amount or Decimal(0)) + (r.pass2_sell_amount or Decimal(0)))
             for r in self.actions),
            Decimal(0),
        )

    @property
    def planned_final(self) -> Decimal:
        return self.current - self.sell_total + self.buy_total


def _bucketise(response: Any) -> list[_Bucket]:
    by_key: dict[str, _Bucket] = {}
    for s in response.subgroups:
        for row in s.actions:
            buy = row.pass1_buy_amount or Decimal(0)
            sell = (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))
            if row.present_allocation_inr <= 0 and buy <= 0 and sell <= 0:
                continue
            bucket = by_key.get(row.sub_category)
            if bucket is None:
                bucket = _Bucket(sub_category=row.sub_category, actions=[])
                by_key[row.sub_category] = bucket
            bucket.actions.append(row)
    return list(by_key.values())


async def build_planned_donut(response: Any) -> PlannedDonut | None:
    """Build the post-rebalance allocation donut payload, or None if no actions."""
    buckets = _bucketise(response)
    slices = [
        PlannedDonutSlice(label=b.sub_category, value=float(b.planned_final))
        for b in buckets
        if b.planned_final > 0
    ]
    if not slices:
        return None
    slices.sort(key=lambda s: -s.value)

    return PlannedDonut(
        title="Your portfolio after rebalancing",
        subtitle="Share of corpus by category in the planned allocation",
        caption=None,
        slices=slices,
    )
```

- [ ] **Step 6: Register in `registry.py`**

Add the import:

```python
from app.services.visualization_tools.planned_donut.builder import build_planned_donut
from app.services.visualization_tools.planned_donut.schema import PlannedDonut
```

Add to `CHART_TOOLS`:

```python
    "planned_donut": ChartTool(
        name="planned_donut",
        description=(
            "Donut chart of the post-rebalance allocation share by SEBI sub-category. "
            "Use when the user asks about the resulting/final portfolio shape, 'what "
            "will it look like after I rebalance', or proportions of the planned mix."
        ),
        builder=build_planned_donut,
        payload_cls=PlannedDonut,
    ),
```

- [ ] **Step 7: Run tests, registry sanity**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/planned_donut/tests/test_builder.py -v
python3 -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"
```

Expected: 2 passed; registry shows 5 keys.

- [ ] **Step 8: Manual checkpoint**

Move on.

---

### Task 4: Migrate `tax_cost_bar`

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/tax_cost_bar/{__init__.py, schema.py, builder.py, tests/__init__.py, tests/test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Create folder + inits**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/tax_cost_bar/tests
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/tax_cost_bar/__init__.py
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/tax_cost_bar/tests/__init__.py
```

- [ ] **Step 2: Write the failing builder test**

Create `tests/test_builder.py`:

```python
"""Smoke test for the tax_cost_bar chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_returns_none_when_no_taxes():
    from app.services.visualization_tools.tax_cost_bar.builder import (
        build_tax_cost_bar,
    )
    response = MagicMock()
    totals = MagicMock()
    totals.total_tax_estimate_inr = Decimal(0)
    totals.total_exit_load_inr = Decimal(0)
    response.totals = totals
    response.subgroups = []
    out = await build_tax_cost_bar(response)
    assert out is None


@pytest.mark.asyncio
async def test_includes_one_category_with_taxes():
    from app.services.visualization_tools.tax_cost_bar.builder import (
        build_tax_cost_bar,
    )
    action = MagicMock()
    action.present_allocation_inr = Decimal("500000")
    action.pass1_buy_amount = None
    action.pass1_sell_amount = Decimal("100000")
    action.pass2_sell_amount = None
    action.pass1_realised_stcg = Decimal("5000")
    action.pass1_realised_ltcg = Decimal("2000")
    action.exit_load_amount = Decimal("500")
    action.sub_category = "Mid Cap Fund"
    subgroup = MagicMock()
    subgroup.asset_subgroup = "high_beta_equities"
    subgroup.actions = [action]
    response = MagicMock()
    response.subgroups = [subgroup]
    totals = MagicMock()
    totals.total_tax_estimate_inr = Decimal("7000")
    totals.total_exit_load_inr = Decimal("500")
    response.totals = totals

    out = await build_tax_cost_bar(response)
    assert out is not None
    assert out.type == "tax_cost_bar"
    assert out.categories == ["Mid Cap Fund"]
    assert out.totals.tax_estimate_inr == 7000.0
    assert out.totals.exit_load_inr == 500.0
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/tax_cost_bar/tests/test_builder.py -v
```

- [ ] **Step 4: Write `tax_cost_bar/schema.py`**

```python
"""Pydantic payload — tax_cost_bar chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class TaxCostNamedSeries(BaseModel):
    name: str
    values: list[float]


class TaxCostTotals(BaseModel):
    tax_estimate_inr: float
    exit_load_inr: float


class TaxCostBar(ChartBase):
    type: Literal["tax_cost_bar"] = "tax_cost_bar"
    categories: list[str]
    series: list[TaxCostNamedSeries]
    totals: TaxCostTotals
    caption: str | None = None
```

- [ ] **Step 5: Write `tax_cost_bar/builder.py`**

```python
"""Chart builder — exit-load + realised gains (ST/LT) per SEBI sub-category.

Best for: 'what does this rebalance cost me?' questions. Skipped when totals
are all zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.tax_cost_bar.schema import (
    TaxCostBar,
    TaxCostNamedSeries,
    TaxCostTotals,
)

ensure_ai_agents_path()

from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
    FundRowAfterStep5,
)


@dataclass
class _Bucket:
    sub_category: str
    actions: list["FundRowAfterStep5"]

    @property
    def realised_stcg(self) -> Decimal:
        return sum(((r.pass1_realised_stcg or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def realised_ltcg(self) -> Decimal:
        return sum(((r.pass1_realised_ltcg or Decimal(0)) for r in self.actions), Decimal(0))

    @property
    def exit_load_inr(self) -> Decimal:
        # exit_load_amount is the *potential* load if all in-period units sold.
        # Apportion by fraction actually sold from this row.
        total = Decimal(0)
        for r in self.actions:
            sold = (r.pass1_sell_amount or Decimal(0)) + (r.pass2_sell_amount or Decimal(0))
            present = r.present_allocation_inr
            potential = r.exit_load_amount or Decimal(0)
            if present > 0 and sold > 0:
                total += potential * (sold / present)
        return total


def _bucketise(response: Any) -> list[_Bucket]:
    by_key: dict[str, _Bucket] = {}
    for s in response.subgroups:
        for row in s.actions:
            buy = row.pass1_buy_amount or Decimal(0)
            sell = (row.pass1_sell_amount or Decimal(0)) + (row.pass2_sell_amount or Decimal(0))
            if row.present_allocation_inr <= 0 and buy <= 0 and sell <= 0:
                continue
            bucket = by_key.get(row.sub_category)
            if bucket is None:
                bucket = _Bucket(sub_category=row.sub_category, actions=[])
                by_key[row.sub_category] = bucket
            bucket.actions.append(row)
    return list(by_key.values())


def _f(amount: Decimal) -> float:
    return float(amount)


async def build_tax_cost_bar(response: Any) -> TaxCostBar | None:
    """Build the per-category cost stacked-bar payload, or None if no taxes."""
    totals = response.totals
    if (
        (totals.total_tax_estimate_inr or 0) <= 0
        and (totals.total_exit_load_inr or 0) <= 0
    ):
        return None

    buckets = _bucketise(response)
    rows = [
        b for b in buckets
        if b.exit_load_inr > 0 or b.realised_stcg > 0 or b.realised_ltcg > 0
    ]
    if not rows:
        return None
    rows.sort(key=lambda b: -(b.exit_load_inr + b.realised_stcg + b.realised_ltcg))

    return TaxCostBar(
        title="Cost of rebalancing per category",
        subtitle="Realised short-term and long-term gains plus exit loads",
        caption=None,
        categories=[b.sub_category for b in rows],
        series=[
            TaxCostNamedSeries(name="Short-term gains", values=[_f(b.realised_stcg) for b in rows]),
            TaxCostNamedSeries(name="Long-term gains", values=[_f(b.realised_ltcg) for b in rows]),
            TaxCostNamedSeries(name="Exit load", values=[_f(b.exit_load_inr) for b in rows]),
        ],
        totals=TaxCostTotals(
            tax_estimate_inr=_f(totals.total_tax_estimate_inr or Decimal(0)),
            exit_load_inr=_f(totals.total_exit_load_inr or Decimal(0)),
        ),
    )
```

- [ ] **Step 6: Register in `registry.py`**

Add imports:

```python
from app.services.visualization_tools.tax_cost_bar.builder import build_tax_cost_bar
from app.services.visualization_tools.tax_cost_bar.schema import TaxCostBar
```

Add entry:

```python
    "tax_cost_bar": ChartTool(
        name="tax_cost_bar",
        description=(
            "Stacked horizontal bar chart of realised short-term + long-term gains "
            "and exit loads per SEBI sub-category, plus headline totals. Use when "
            "the user asks about cost, taxes, exit loads, 'is rebalancing worth it', "
            "or trade-offs of the rebalance."
        ),
        builder=build_tax_cost_bar,
        payload_cls=TaxCostBar,
    ),
```

- [ ] **Step 7: Run tests, registry sanity**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/tax_cost_bar/tests/test_builder.py -v
python3 -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"
```

Expected: 2 passed; registry shows 6 keys.

- [ ] **Step 8: Manual checkpoint**

Move on.

---

## Phase 2 — Build the 3 net-new charts

### Task 5: Build `top_bottom_funds`

Performance domain. AA-shape builder reads `PortfolioHolding.return_1y` for the user's primary portfolio.

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/top_bottom_funds/{__init__.py, schema.py, builder.py, tests/__init__.py, tests/test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Create folder + inits**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/top_bottom_funds/tests
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/top_bottom_funds/__init__.py
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/top_bottom_funds/tests/__init__.py
```

- [ ] **Step 2: Write the failing builder test**

Create `tests/test_builder.py`:

```python
"""Smoke test for the top_bottom_funds chart builder."""
from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.models.portfolio import Portfolio, PortfolioHolding


@pytest.mark.asyncio
async def test_returns_none_when_no_holdings(
    db_session, fixture_user_with_dob,
):
    from app.services.visualization_tools.top_bottom_funds.builder import (
        build_top_bottom_funds,
    )
    out = await build_top_bottom_funds(db_session, fixture_user_with_dob.id)
    assert out is None


@pytest.mark.asyncio
async def test_returns_top_3_and_bottom_3_by_return_1y(
    db_session, fixture_user_with_portfolio_and_allocations,
):
    from sqlalchemy import select
    user = fixture_user_with_portfolio_and_allocations
    portfolio = (await db_session.execute(
        select(Portfolio).where(Portfolio.user_id == user.id)
    )).scalar_one()

    # 8 holdings with returns from -10% to 25%
    returns = [25.0, 18.0, 14.0, 10.0, 5.0, 0.0, -3.0, -10.0]
    for i, r in enumerate(returns):
        db_session.add(PortfolioHolding(
            id=uuid.uuid4(),
            portfolio_id=portfolio.id,
            instrument_name=f"Fund {i+1}",
            instrument_type="mutual_fund",
            current_value=Decimal("100000"),
            return_1y=Decimal(str(r)),
        ))
    await db_session.flush()

    out = await build_top_bottom_funds(db_session, user.id)
    assert out is not None
    assert out.type == "top_bottom_funds"
    assert len(out.top) == 3
    assert len(out.bottom) == 3
    assert out.top[0].name == "Fund 1"  # 25%
    assert out.top[0].return_pct == 25.0
    assert out.bottom[-1].name == "Fund 8"  # -10%
    # average is over all funds with return_1y set
    assert out.portfolio_average_pct == pytest.approx(sum(returns) / 8, abs=0.5)
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/top_bottom_funds/tests/test_builder.py -v
```

- [ ] **Step 4: Write `top_bottom_funds/schema.py`**

```python
"""Pydantic payload — top_bottom_funds chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class FundReturnRow(BaseModel):
    name: str
    return_pct: float
    current_value: float


class TopBottomFunds(ChartBase):
    type: Literal["top_bottom_funds"] = "top_bottom_funds"
    top: list[FundReturnRow]
    bottom: list[FundReturnRow]
    portfolio_average_pct: float
```

- [ ] **Step 5: Write `top_bottom_funds/builder.py`**

```python
"""Chart builder — top-3 + bottom-3 funds by 1Y return.

Reads ``PortfolioHolding.return_1y`` from the user's primary portfolio. Skips
holdings without a 1Y return value (they get excluded from the average too).
Returns None if the user has no portfolio or fewer than 2 valued holdings.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import PortfolioHolding
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.top_bottom_funds.schema import (
    FundReturnRow,
    TopBottomFunds,
)

_TOP_N = 3
_BOTTOM_N = 3


async def build_top_bottom_funds(
    db: AsyncSession, user_id: uuid.UUID
) -> TopBottomFunds | None:
    """Build the top/bottom-funds payload, or None if data missing."""
    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    stmt = (
        select(PortfolioHolding)
        .where(PortfolioHolding.portfolio_id == portfolio.id)
        .where(PortfolioHolding.return_1y.isnot(None))
        .order_by(PortfolioHolding.return_1y.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    if len(rows) < 2:
        return None

    avg = sum(float(r.return_1y) for r in rows) / len(rows)

    top_rows = rows[:_TOP_N]
    bottom_rows = rows[-_BOTTOM_N:] if len(rows) > _TOP_N else []
    # If top + bottom would overlap, trim the overlap from bottom.
    top_set = {r.id for r in top_rows}
    bottom_rows = [r for r in bottom_rows if r.id not in top_set]

    def _row(h: PortfolioHolding) -> FundReturnRow:
        return FundReturnRow(
            name=h.instrument_name,
            return_pct=float(h.return_1y),
            current_value=float(h.current_value),
        )

    return TopBottomFunds(
        title="Best and worst performers",
        subtitle="1-year return per fund",
        top=[_row(r) for r in top_rows],
        bottom=[_row(r) for r in reversed(bottom_rows)],  # worst-first for the chart
        portfolio_average_pct=avg,
    )
```

- [ ] **Step 6: Register in `registry.py`**

```python
from app.services.visualization_tools.top_bottom_funds.builder import build_top_bottom_funds
from app.services.visualization_tools.top_bottom_funds.schema import TopBottomFunds

# In CHART_TOOLS:
    "top_bottom_funds": ChartTool(
        name="top_bottom_funds",
        description=(
            "Bar chart of the top-3 and bottom-3 funds in the user's portfolio by "
            "1-year return, with a portfolio-average reference line. Use when the "
            "user asks about which funds are performing well or poorly, 'best and "
            "worst', 'which funds are dragging', or fund-level performance comparison."
        ),
        builder=build_top_bottom_funds,
        payload_cls=TopBottomFunds,
    ),
```

- [ ] **Step 7: Run tests, registry sanity**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/top_bottom_funds/tests/test_builder.py -v
python3 -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"
```

Expected: 2 passed; registry shows 7 keys.

- [ ] **Step 8: Manual checkpoint**

Move on.

---

### Task 6: Build `profile_dial`

Risk domain. AA-shape builder reads `EffectiveRiskAssessment.effective_risk_score` (Numeric(7,4); range 0-100 conventionally).

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/profile_dial/{__init__.py, schema.py, builder.py, tests/__init__.py, tests/test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Create folder + inits**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/profile_dial/tests
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/profile_dial/__init__.py
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/profile_dial/tests/__init__.py
```

- [ ] **Step 2: Write the failing builder test**

Create `tests/test_builder.py`:

```python
"""Smoke test for the profile_dial chart builder."""
from __future__ import annotations

import uuid

import pytest

from app.models.profile.effective_risk_assessment import EffectiveRiskAssessment


@pytest.mark.asyncio
async def test_returns_none_when_no_assessment(db_session, fixture_user_with_dob):
    from app.services.visualization_tools.profile_dial.builder import (
        build_profile_dial,
    )
    out = await build_profile_dial(db_session, fixture_user_with_dob.id)
    assert out is None


@pytest.mark.asyncio
async def test_returns_dial_with_band(db_session, fixture_user_with_dob):
    user = fixture_user_with_dob
    db_session.add(EffectiveRiskAssessment(
        id=uuid.uuid4(),
        user_id=user.id,
        step_name="risk_profile",
        payload={},
        calculations={},
        output={},
        effective_risk_score=72.0,
    ))
    await db_session.flush()

    from app.services.visualization_tools.profile_dial.builder import (
        build_profile_dial,
    )
    out = await build_profile_dial(db_session, user.id)
    assert out is not None
    assert out.type == "profile_dial"
    assert out.score == 72.0
    assert out.band in {"Conservative", "Moderate-Conservative", "Balanced",
                        "Moderate-Aggressive", "Aggressive"}
    # 72 sits in the Moderate-Aggressive band (60-80)
    assert out.band == "Moderate-Aggressive"
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/profile_dial/tests/test_builder.py -v
```

- [ ] **Step 4: Write `profile_dial/schema.py`**

```python
"""Pydantic payload — profile_dial chart."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.services.visualization_tools._base import ChartBase


class ProfileDial(ChartBase):
    type: Literal["profile_dial"] = "profile_dial"
    score: float = Field(..., ge=0, le=100)
    band: Literal[
        "Conservative",
        "Moderate-Conservative",
        "Balanced",
        "Moderate-Aggressive",
        "Aggressive",
    ]
    headline: str
```

- [ ] **Step 5: Write `profile_dial/builder.py`**

```python
"""Chart builder — risk profile dial.

Reads the user's latest ``EffectiveRiskAssessment.effective_risk_score`` and
returns a ProfileDial payload with the score (0-100), the named band, and a
short headline. Returns None when no assessment exists yet.

5-band mapping (matches the existing risk-profiling vocabulary):
  0-20:  Conservative
  20-40: Moderate-Conservative
  40-60: Balanced
  60-80: Moderate-Aggressive
  80-100: Aggressive
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.profile.effective_risk_assessment import EffectiveRiskAssessment
from app.services.visualization_tools.profile_dial.schema import ProfileDial


_BANDS: list[tuple[float, str]] = [
    (20.0, "Conservative"),
    (40.0, "Moderate-Conservative"),
    (60.0, "Balanced"),
    (80.0, "Moderate-Aggressive"),
    (100.01, "Aggressive"),
]


def _band_for(score: float) -> str:
    for upper, label in _BANDS:
        if score < upper:
            return label
    return "Aggressive"


async def build_profile_dial(
    db: AsyncSession, user_id: uuid.UUID
) -> ProfileDial | None:
    """Build the risk-profile dial payload, or None if no assessment exists."""
    stmt = (
        select(EffectiveRiskAssessment)
        .where(EffectiveRiskAssessment.user_id == user_id)
        .where(EffectiveRiskAssessment.effective_risk_score.isnot(None))
        .order_by(EffectiveRiskAssessment.computed_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None or row.effective_risk_score is None:
        return None

    score = float(row.effective_risk_score)
    score = max(0.0, min(100.0, score))
    band = _band_for(score)

    return ProfileDial(
        title="Your risk profile",
        subtitle="Based on your latest assessment",
        score=score,
        band=band,
        headline=f"You're in the {band} band ({score:.0f} / 100)",
    )
```

- [ ] **Step 6: Register in `registry.py`**

```python
from app.services.visualization_tools.profile_dial.builder import build_profile_dial
from app.services.visualization_tools.profile_dial.schema import ProfileDial

# In CHART_TOOLS:
    "profile_dial": ChartTool(
        name="profile_dial",
        description=(
            "Gauge / dial showing the user's risk profile from Conservative to "
            "Aggressive (5 bands). Use when the user asks about their risk profile, "
            "risk score, 'how aggressive is my profile', risk capacity, or how their "
            "profile compares to the spectrum."
        ),
        builder=build_profile_dial,
        payload_cls=ProfileDial,
    ),
```

- [ ] **Step 7: Run tests, registry sanity**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/profile_dial/tests/test_builder.py -v
python3 -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"
```

Expected: 2 passed; registry shows 8 keys.

- [ ] **Step 8: Manual checkpoint**

Move on.

---

### Task 7: Build `buy_sell_ledger`

Rebalancing domain. Rebal-shape builder takes `RebalancingComputeResponse` and produces a per-fund buy/sell list.

**Files:**
- Create: `Prozpr_Backend/app/services/visualization_tools/buy_sell_ledger/{__init__.py, schema.py, builder.py, tests/__init__.py, tests/test_builder.py}`
- Modify: `Prozpr_Backend/app/services/visualization_tools/registry.py`

- [ ] **Step 1: Create folder + inits**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/buy_sell_ledger/tests
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/buy_sell_ledger/__init__.py
touch /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/visualization_tools/buy_sell_ledger/tests/__init__.py
```

- [ ] **Step 2: Write the failing builder test**

Create `tests/test_builder.py`:

```python
"""Smoke test for the buy_sell_ledger chart builder."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest


def _make_action(name: str, sub_cat: str, buy: float = 0, sell: float = 0):
    a = MagicMock()
    a.fund_name = name
    a.sub_category = sub_cat
    a.pass1_buy_amount = Decimal(str(buy)) if buy else None
    a.pass1_sell_amount = Decimal(str(sell)) if sell else None
    a.pass2_sell_amount = None
    a.present_allocation_inr = Decimal("100000")
    return a


@pytest.mark.asyncio
async def test_returns_none_when_no_trades():
    from app.services.visualization_tools.buy_sell_ledger.builder import (
        build_buy_sell_ledger,
    )
    response = MagicMock()
    response.subgroups = []
    out = await build_buy_sell_ledger(response)
    assert out is None


@pytest.mark.asyncio
async def test_returns_rows_sorted_by_absolute_trade():
    from app.services.visualization_tools.buy_sell_ledger.builder import (
        build_buy_sell_ledger,
    )
    subgroup = MagicMock()
    subgroup.actions = [
        _make_action("Fund A", "Large Cap Fund", buy=200000),
        _make_action("Fund B", "Mid Cap Fund", sell=80000),
        _make_action("Fund C", "Large Cap Fund", buy=10000),
    ]
    response = MagicMock()
    response.subgroups = [subgroup]
    out = await build_buy_sell_ledger(response)
    assert out is not None
    assert out.type == "buy_sell_ledger"
    assert len(out.rows) == 3
    # Sorted by abs(buy + sell): Fund A 200k, Fund B 80k, Fund C 10k
    assert out.rows[0].name == "Fund A"
    assert out.rows[0].buy_inr == 200000.0
    assert out.rows[1].name == "Fund B"
    assert out.rows[1].sell_inr == 80000.0
```

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/buy_sell_ledger/tests/test_builder.py -v
```

Note: if `FundRowAfterStep5` does not have `fund_name` (it might be `name`, `instrument_name`, or `fund` — the engine model is the source of truth), READ `AI_Agents/src/Rebalancing/models.py` lines around 99-150 to find the actual field name and update both the test and builder to match. Report the adaptation in your concerns.

- [ ] **Step 4: Write `buy_sell_ledger/schema.py`**

```python
"""Pydantic payload — buy_sell_ledger chart."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.services.visualization_tools._base import ChartBase


class BuySellRow(BaseModel):
    name: str
    sub_category: str
    buy_inr: float
    sell_inr: float


class BuySellLedger(ChartBase):
    type: Literal["buy_sell_ledger"] = "buy_sell_ledger"
    rows: list[BuySellRow]
```

- [ ] **Step 5: Write `buy_sell_ledger/builder.py`**

```python
"""Chart builder — per-fund buy/sell ledger from a rebalancing trade plan.

Reads the engine response's subgroups → actions and emits one row per fund
with its sub-category, buy ₹, and sell ₹. Sorted by absolute trade size
(largest first) so the most consequential trades lead.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.buy_sell_ledger.schema import (
    BuySellLedger,
    BuySellRow,
)

ensure_ai_agents_path()

from Rebalancing.models import FundRowAfterStep5  # type: ignore[import-not-found]  # noqa: E402, F401


async def build_buy_sell_ledger(response: Any) -> BuySellLedger | None:
    """Build the per-fund buy/sell ledger, or None if no trades."""
    rows: list[BuySellRow] = []
    for subgroup in response.subgroups:
        for action in subgroup.actions:
            buy = float(action.pass1_buy_amount or Decimal(0))
            sell_p1 = float(action.pass1_sell_amount or Decimal(0))
            sell_p2 = float(action.pass2_sell_amount or Decimal(0))
            sell = sell_p1 + sell_p2
            if buy <= 0 and sell <= 0:
                continue
            # The engine's row may use ``fund_name``, ``name``, or ``instrument_name``;
            # look the right one up at runtime.
            name = (
                getattr(action, "fund_name", None)
                or getattr(action, "name", None)
                or getattr(action, "instrument_name", None)
                or "Unknown fund"
            )
            rows.append(BuySellRow(
                name=str(name),
                sub_category=str(getattr(action, "sub_category", "") or ""),
                buy_inr=buy,
                sell_inr=sell,
            ))
    if not rows:
        return None

    rows.sort(key=lambda r: -(r.buy_inr + r.sell_inr))

    return BuySellLedger(
        title="Trades to execute",
        subtitle="Buy and sell amounts per fund",
        rows=rows,
    )
```

- [ ] **Step 6: Register in `registry.py`**

```python
from app.services.visualization_tools.buy_sell_ledger.builder import build_buy_sell_ledger
from app.services.visualization_tools.buy_sell_ledger.schema import BuySellLedger

# In CHART_TOOLS:
    "buy_sell_ledger": ChartTool(
        name="buy_sell_ledger",
        description=(
            "Per-fund table of buy and sell amounts (₹) from a rebalancing trade "
            "plan, sorted by absolute trade size. Use when the user asks 'what trades "
            "should I do', 'just show me the trades', the actual buys and sells, or "
            "wants the executable steps from a rebalance."
        ),
        builder=build_buy_sell_ledger,
        payload_cls=BuySellLedger,
    ),
```

- [ ] **Step 7: Run tests, registry sanity, full backend smoke**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/visualization_tools/buy_sell_ledger/tests/test_builder.py -v
python3 -c "from app.services.visualization_tools.registry import CHART_TOOLS; print(sorted(CHART_TOOLS.keys()))"
python3 -m pytest app/ 2>&1 | tail -5
```

Expected: 2 passed; registry shows 9 keys; full backend tests still all pass.

- [ ] **Step 8: Manual checkpoint**

Move on.

---

## Phase 3 — Wire the rebalancing branch through the central selector

### Task 8: Rewrite the rebalancing branch in `brain.py` + remove chart plumbing from `ai_bridge/rebalancing/chat.py`

This task removes the on-critical-path picker LLM call and routes rebalancing chat through the central selector + builder pattern, mirroring what Plan 1 did for AA.

**Files:**
- Modify: `Prozpr_Backend/app/services/chat_core/brain.py` — rebalancing branch (around lines 168-180)
- Modify: `Prozpr_Backend/app/services/ai_bridge/rebalancing/chat.py` — strip chart_picker calls and `outcome.chart` plumbing
- Create: `Prozpr_Backend/app/services/chat_core/tests/test_brain_rebalancing_charts.py`

- [ ] **Step 1: Read the current rebalancing branch + rebalancing chat.py**

Read `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/chat_core/brain.py` lines 165-190 and `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/chat.py` (focus on places that touch `chart=`, `outcome.chart`, `chart_picker`, `pick_chart`).

Inventory the changes needed:
- `chat.py` returns a chart in its `RebalancingRunOutcome`/result (~10 sites). After this task it should NOT.
- `service.py` (the engine adapter) calls `pick_chart(...)` from `chart_picker.py`. After this task it should NOT.
- `brain.py` reads `result.chart` and passes it to `finalize(chart_payloads=[result.chart] if result.chart else None)`. After this task it should fetch chart names via the central selector and dispatch via `build_rebalancing.py`.

- [ ] **Step 2: Write the failing brain integration test**

Create `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/chat_core/tests/test_brain_rebalancing_charts.py`:

```python
"""Brain integration — rebalancing branch produces chart_payloads via central selector."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.database import Base
from app.models.user import User
from app.services.chat_core.brain import ChatBrain
from app.services.chat_core.types import ChatTurnInput


@pytest_asyncio.fixture
async def db_session():
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
async def fixture_user(db_session):
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_brain_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _classification(intent: str = "rebalancing"):
    return type("C", (), {
        "intent": type("I", (), {"value": intent})(),
        "confidence": 0.95,
        "reasoning": "test",
        "out_of_scope_message": None,
    })()


def _engine_response_dispatch_result():
    """Simulate dispatch_chat returning a result that carries the engine response."""
    response = MagicMock()
    response.subgroups = []  # empty → category_gap_bar returns None → no chart payload
    response.totals = MagicMock(total_tax_estimate_inr=Decimal(0), total_exit_load_inr=Decimal(0))
    return type("R", (), {
        "text": "Here's your rebalance plan.",
        "snapshot_id": None,
        "rebalancing_recommendation_id": None,
        "rebalancing_response": response,
    })()


@pytest.mark.asyncio
async def test_rebal_turn_attaches_chart_payloads(db_session, fixture_user):
    """When the selector returns names AND the engine response has trades, payloads ship."""
    from Rebalancing.models import RebalancingComputeResponse  # noqa: F401  -- ensure path injection

    # Build a richer response with one buy
    action = MagicMock()
    action.fund_name = "Fund A"
    action.sub_category = "Large Cap Fund"
    action.pass1_buy_amount = Decimal("100000")
    action.pass1_sell_amount = None
    action.pass2_sell_amount = None
    action.present_allocation_inr = Decimal("500000")
    subgroup = MagicMock()
    subgroup.asset_subgroup = "low_beta_equities"
    subgroup.goal_target_inr = Decimal("600000")
    subgroup.actions = [action]
    response = MagicMock()
    response.subgroups = [subgroup]
    response.totals = MagicMock(total_tax_estimate_inr=Decimal(0), total_exit_load_inr=Decimal(0))

    dispatch_result = type("R", (), {
        "text": "Here's your rebalance plan.",
        "snapshot_id": None,
        "rebalancing_recommendation_id": uuid.uuid4(),
        "rebalancing_response": response,
    })()

    turn = ChatTurnInput(
        db=db_session,
        user_id=fixture_user.id,
        session_id=uuid.uuid4(),
        user_question="rebalance my portfolio",
        conversation_history=[],
        client_context=None,
        user_ctx=fixture_user,
    )

    with patch(
        "app.services.chat_core.brain.classify_user_message",
        new=AsyncMock(return_value=_classification("rebalancing")),
    ), patch(
        "app.services.chat_core.brain.select_charts",
        new=AsyncMock(return_value=["category_gap_bar", "buy_sell_ledger"]),
    ), patch(
        "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
        new=AsyncMock(return_value=dispatch_result),
    ):
        result = await ChatBrain().run_turn(turn)

    assert result.intent == "rebalancing"
    assert result.chart_payloads is not None
    types_returned = {p["type"] for p in result.chart_payloads}
    assert "category_gap_bar" in types_returned
    assert "buy_sell_ledger" in types_returned
```

NOTE on signature: `ChatTurnInput` may use `effective_user_id` as a property (per Plan 1's discovery). Adapt if needed; `session_id` is non-nullable so use `uuid.uuid4()`. The dispatch result must carry `rebalancing_response` — this is a NEW field on the result that we're adding in Step 4 below; if it doesn't fit the existing dataclass, you may need to add it (see Step 4).

- [ ] **Step 3: Run test, confirm it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/chat_core/tests/test_brain_rebalancing_charts.py -v
```

Expected: FAIL — likely `AttributeError: ... has no attribute 'rebalancing_response'` (the result type doesn't yet carry the engine response) or an import error if your changes aren't yet in place.

- [ ] **Step 4: Strip chart code from `ai_bridge/rebalancing/chat.py` AND ensure the engine response is carried on the result**

Open `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/chat.py`. The file is large (~682 lines). Apply these focused edits:

(a) **Find the result/outcome dataclass**. There's a `RebalancingRunOutcome` (or similarly named result type) that carries `text`, `snapshot_id`, `rebalancing_recommendation_id`, and currently `chart`. The dispatch_chat call returns one of these. **Add a new field** `rebalancing_response: Any` to carry the in-memory engine response so `brain.py` can pass it to the chart builders. Keep `chart` for now (we'll remove it in step 6).

If you can't find the dataclass directly in `chat.py`, it likely lives in `Prozpr_Backend/app/services/ai_bridge/chat_dispatcher.py` or a `types.py` shared by the dispatcher. READ `chat_dispatcher.py` to find the result schema. Add the new optional field there.

(b) **In `chat.py`**, find every site that constructs the result with `chart=...`. Wherever the engine response is in scope at construction time, also pass `rebalancing_response=outcome.response` (or the local variable name for the engine response). For sites that produce a no-engine-response result (early returns / errors), pass `rebalancing_response=None`.

(c) **In `chat.py`**, REMOVE all calls to chart computation: `await pick_chart(...)`, `available_charts(...)`, `outcome.chart = ...`, anything that builds chart specs. The engine response itself is what we propagate; building chart payloads happens later in `brain.py` via `build_charts_for_rebalancing`.

(d) **In `Prozpr_Backend/app/services/ai_bridge/rebalancing/service.py`** (the engine adapter), if it currently calls `pick_chart` or `available_charts`, REMOVE those imports and call sites. Service should produce the engine response only; chart selection is no longer its job.

After this step, `grep -n "pick_chart\|available_charts\|chart=" Prozpr_Backend/app/services/ai_bridge/rebalancing/chat.py Prozpr_Backend/app/services/ai_bridge/rebalancing/service.py` should return zero results except for the new `rebalancing_response=` lines.

- [ ] **Step 5: Modify `brain.py` — rewrite the rebalancing branch**

Open `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/chat_core/brain.py`. The required imports were already added in Plan 1's Task 7:

```python
from app.services.ai_bridge.chart_selector_service import select_charts
from app.services.visualization_tools.build_aa import build_charts_for_aa
```

ADD one more import:

```python
from app.services.visualization_tools.build_rebalancing import build_charts_for_rebalancing
```

REPLACE the rebalancing branch (currently around lines 168-186 — locate by `if intent_value == "rebalancing":`):

```python
            if intent_value == "rebalancing":
                # Local import — chat handler self-registers via @register at import time.
                from app.services.ai_bridge.rebalancing import chat as _rb_chat  # noqa: F401
                from app.services.ai_bridge.chat_dispatcher import dispatch_chat
                flow.append("dispatch_chat → rebalancing_chat")
                trace_line("next module: chat_dispatcher → rebalancing_chat")

                # Dispatch the rebalancing chat handler (runs the engine + formatter).
                # The selector LLM cannot start until we have an engine response on
                # which to base the rebalancing chart builders, so we kick it off
                # AFTER dispatch_chat returns and overlap it with the build step
                # only.  Net latency change vs Plan 1's pre-state: still +1 LLM
                # call but no longer on the critical path of the formatter (which
                # runs inside dispatch_chat).
                result = await dispatch_chat(intent_value, turn_context)

                response = getattr(result, "rebalancing_response", None)
                chart_payloads: list[dict[str, Any]] | None = None
                if response is not None:
                    selector_task = asyncio.create_task(
                        select_charts(turn.user_question, intent_value)
                    )
                    try:
                        chart_names = await asyncio.wait_for(selector_task, timeout=3.0)
                    except asyncio.TimeoutError:
                        logger.warning("Rebal chart selector timed out; shipping without charts")
                        selector_task.cancel()
                        chart_names = []
                    except Exception as exc:
                        logger.warning("Rebal chart selector failed (%s); shipping without charts", exc)
                        chart_names = []

                    if chart_names:
                        try:
                            payloads = await build_charts_for_rebalancing(response, chart_names)
                            if payloads:
                                chart_payloads = [p.model_dump(mode="json") for p in payloads]
                        except Exception:
                            logger.exception("Rebal chart builder failed; shipping without charts")

                return await finalize(
                    result.text,
                    ideal_allocation_snapshot_id=result.snapshot_id,
                    ideal_allocation_rebalancing_id=result.rebalancing_recommendation_id,
                    chart_payloads=chart_payloads,
                )
```

Note: this is slightly different from the spec's "selector parallel with formatter" design because the formatter LLM lives inside `dispatch_chat`. To get true parallelism we'd need to refactor `dispatch_chat` to expose the formatter's task; that's deferred to a follow-up. For now we eliminate the on-critical-path picker LLM (the bigger win) and accept selector-then-build sequencing.

- [ ] **Step 6: Run the rebalancing-chart brain test + AA test (regression check)**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/chat_core/tests/ -v
```

Expected: all chat_core tests pass — both the AA chart test (from Plan 1) and the new rebalancing chart test.

- [ ] **Step 7: Run full backend test suite**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/ 2>&1 | tail -10
```

Expected: total tests ≥ 152 (Plan 1's 151 + at least 1 new), zero failures. If anything in `ai_bridge/rebalancing/tests/` fails because the chart removal in Step 4 broke an assertion, fix the test (the assertion was about the old chart_picker path, which is gone now).

- [ ] **Step 8: Manual checkpoint**

Move on.

---

## Phase 4 — Backend cleanup (archive dead code)

### Task 9: Archive `chart_picker.py` + `charts.py` + their tests

After Task 8, `ai_bridge/rebalancing/chart_picker.py` and `charts.py` have no callers. Move them to an archive folder per the "reversible deletes" rule.

**Files:**
- Move: `ai_bridge/rebalancing/chart_picker.py` → `ai_bridge/rebalancing/archive/chart_picker_pre_central_registry.py`
- Move: `ai_bridge/rebalancing/charts.py` → `ai_bridge/rebalancing/archive/charts_pre_central_registry.py`
- Move (if exists): `ai_bridge/rebalancing/tests/test_chart_picker.py` → archive
- Verify: nothing imports the moved modules

- [ ] **Step 1: Confirm no active code imports them**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && grep -rln "from app.services.ai_bridge.rebalancing.chart_picker\|from app.services.ai_bridge.rebalancing.charts\|from app.services.ai_bridge.rebalancing import.*chart_picker" app/ --include="*.py" 2>/dev/null
```

Expected: no output (no callers). If anything shows up, fix the import in that file (point to the new central registry / build_rebalancing) before moving.

- [ ] **Step 2: List the chart-picker tests that exist**

```bash
ls /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/tests/ | grep -i "chart"
```

Note the names that appear; you'll move these too.

- [ ] **Step 3: Move the files**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/archive
mv /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/chart_picker.py \
   /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/archive/chart_picker_pre_central_registry.py
mv /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/charts.py \
   /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/archive/charts_pre_central_registry.py
```

For each chart-related test file from Step 2 (likely `test_chart_picker.py` and `test_charts.py`), move it to `archive/`:

```bash
mv /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/tests/test_chart_picker.py \
   /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/services/ai_bridge/rebalancing/archive/test_chart_picker_pre_central_registry.py 2>/dev/null || true
```

Repeat for any other matching test files. The archive folder lacks an `__init__.py` so pytest won't auto-discover its contents.

- [ ] **Step 4: Re-run the rebalancing test suite to confirm nothing broke**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/services/ai_bridge/rebalancing/ -v 2>&1 | tail -15
```

Expected: pass. If any conftest fixture (like the autouse `_no_llm_chart_picker`) referenced the moved module, edit the conftest to remove the now-dead fixture/import. The autouse `_no_llm_chart_picker` patches `service.pick_chart` — that import path no longer exists if Task 8 step 4(d) removed `pick_chart` from `service.py`. Delete the autouse fixture from `app/services/ai_bridge/rebalancing/tests/conftest.py` if pytest reports an attribute error.

- [ ] **Step 5: Full backend test suite**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/ 2>&1 | tail -5
```

Expected: pass.

- [ ] **Step 6: Manual checkpoint**

Backend cleanup complete. Move on to frontend.

---

## Phase 5 — Frontend rewrite

### Task 10: Update frontend types — drop legacy rebal payloads, add typed shapes for all 6 (3 rebal + 3 new) charts

**Files:**
- Modify: `Prozpr_Frontend/src/components/chat/visualization_tools/types.ts`
- Modify: `Prozpr_Frontend/src/components/chat/visualization_tools/index.ts`

- [ ] **Step 1: Read current `types.ts`**

Read `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/types.ts` to see the current state (3 AA typed payloads + 3 legacy rebal payloads using `chart_type`).

- [ ] **Step 2: Replace `types.ts` with the unified 9-chart version**

Replace contents:

```ts
import type { ChartBase } from "./_base";

// ─── AA charts (typed) ───

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

// ─── Performance ───

export interface FundReturnRow {
  name: string;
  return_pct: number;
  current_value: number;
}

export interface TopBottomFunds extends ChartBase {
  type: "top_bottom_funds";
  top: FundReturnRow[];
  bottom: FundReturnRow[];
  portfolio_average_pct: number;
}

// ─── Risk ───

export interface ProfileDial extends ChartBase {
  type: "profile_dial";
  score: number;
  band:
    | "Conservative"
    | "Moderate-Conservative"
    | "Balanced"
    | "Moderate-Aggressive"
    | "Aggressive";
  headline: string;
}

// ─── Rebalancing (now typed) ───

export interface NamedSeries {
  name: string;
  values: number[];
}

export interface CategoryGapBar extends ChartBase {
  type: "category_gap_bar";
  categories: string[];
  series: NamedSeries[];
  caption?: string | null;
}

export interface PlannedDonutSlice {
  label: string;
  value: number;
}

export interface PlannedDonut extends ChartBase {
  type: "planned_donut";
  slices: PlannedDonutSlice[];
  caption?: string | null;
}

export interface TaxCostNamedSeries {
  name: string;
  values: number[];
}

export interface TaxCostTotals {
  tax_estimate_inr: number;
  exit_load_inr: number;
}

export interface TaxCostBar extends ChartBase {
  type: "tax_cost_bar";
  categories: string[];
  series: TaxCostNamedSeries[];
  totals: TaxCostTotals;
  caption?: string | null;
}

export interface BuySellRow {
  name: string;
  sub_category: string;
  buy_inr: number;
  sell_inr: number;
}

export interface BuySellLedger extends ChartBase {
  type: "buy_sell_ledger";
  rows: BuySellRow[];
}

// ─── Unified union ───

export type ChartPayload =
  | CurrentDonut
  | ConcentrationRisk
  | TargetVsActual
  | TopBottomFunds
  | ProfileDial
  | CategoryGapBar
  | PlannedDonut
  | TaxCostBar
  | BuySellLedger;
```

- [ ] **Step 3: Update `index.ts`**

Replace contents:

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
  // Performance
  TopBottomFunds,
  FundReturnRow,
  // Risk
  ProfileDial,
  // Rebalancing (typed)
  CategoryGapBar,
  NamedSeries,
  PlannedDonut,
  PlannedDonutSlice,
  TaxCostBar,
  TaxCostNamedSeries,
  TaxCostTotals,
  BuySellLedger,
  BuySellRow,
} from "./types";
```

- [ ] **Step 4: Type-check (with `-p tsconfig.app.json`!)**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend && npx tsc --noEmit -p tsconfig.app.json 2>&1 | head -30
```

Expected: errors for the existing rebalancing components (`./rebalancing/CategoryGapBar`, `./rebalancing/PlannedDonut`, `./rebalancing/TaxCostBar`) because they still expect the old `chart_type` shape — those are rewritten in tasks 11-13. Also expect missing-module errors for the 3 new chart components — created in tasks 14-16.

If any error mentions an AA component, fix it before continuing.

- [ ] **Step 5: Manual checkpoint**

Move on.

---

### Task 11: Frontend rewrite of `CategoryGapBar` in editorial-wealth style

**Files:**
- Create: `Prozpr_Frontend/src/components/chat/visualization_tools/CategoryGapBar/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create the directory**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/CategoryGapBar
```

- [ ] **Step 2: Create `CategoryGapBar/types.ts`**

```ts
export type { CategoryGapBar as CategoryGapBarPayload, NamedSeries } from "../types";
```

- [ ] **Step 3: Create `CategoryGapBar/Chart.tsx`**

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
import type { CategoryGapBarPayload } from "./types";
import { formatInrCompact } from "@/lib/utils";

// Series colors: Current = muted (where you are), Target = wealth-navy (where
// you should be), Plan = wealth-blue (the recommendation, the action).
const SERIES_COLOR: Record<string, string> = {
  Current: "hsl(220 13% 64%)",
  Target: "hsl(222 47% 14%)",
  Plan: "hsl(215 60% 48%)",
};

const FALLBACK = ["hsl(215 60% 48%)", "hsl(222 47% 14%)", "hsl(220 13% 64%)"];

function colorFor(name: string, i: number): string {
  return SERIES_COLOR[name] ?? FALLBACK[i % FALLBACK.length];
}

export function CategoryGapBar({ payload }: { payload: CategoryGapBarPayload }) {
  const { categories, series } = payload;
  const data = categories.map((category, idx) => {
    const row: Record<string, string | number> = { category };
    for (const s of series) {
      row[s.name] = s.values[idx] ?? 0;
    }
    return row;
  });
  const chartHeight = Math.max(180, categories.length * 40);

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
            margin={{ top: 4, right: 12, left: 4, bottom: 4 }}
            barGap={2}
          >
            <CartesianGrid horizontal={false} stroke="hsl(var(--border))" strokeOpacity={0.4} />
            <XAxis
              type="number"
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              tickFormatter={(v: number) => formatInrCompact(v)}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="category"
              width={110}
              tick={{ fontSize: 11, fill: "hsl(var(--foreground))" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.04)" }}
              formatter={(value: number, name) => [formatInrCompact(value), name]}
              contentStyle={{ fontSize: "11px", borderRadius: "6px" }}
            />
            <Legend
              wrapperStyle={{ fontSize: "11px", paddingTop: "4px" }}
              iconSize={10}
            />
            {series.map((s, i) => (
              <Bar
                key={s.name}
                dataKey={s.name}
                fill={colorFor(s.name, i)}
                radius={[0, 4, 4, 0]}
                barSize={9}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Manual checkpoint** — Tasks 12 and 13 follow the same shape; come back for the type-check after all three are in place.

---

### Task 12: Frontend rewrite of `PlannedDonut`

**Files:**
- Create: `Prozpr_Frontend/src/components/chat/visualization_tools/PlannedDonut/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create directory**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/PlannedDonut
```

- [ ] **Step 2: Create `PlannedDonut/types.ts`**

```ts
export type { PlannedDonut as PlannedDonutPayload, PlannedDonutSlice } from "../types";
```

- [ ] **Step 3: Create `PlannedDonut/Chart.tsx`**

```tsx
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import type { PlannedDonutPayload } from "./types";
import { formatInrCompact } from "@/lib/utils";

// Tinted-blue rotation for sub-categories — keeps the editorial-wealth feel
// while offering enough distinct hues for ~6-8 categories.
const PALETTE = [
  "hsl(215 60% 48%)",
  "hsl(222 47% 14%)",
  "hsl(160 50% 38%)",
  "hsl(38 80% 48%)",
  "hsl(220 35% 28%)",
  "hsl(215 50% 65%)",
  "hsl(160 30% 55%)",
  "hsl(38 60% 65%)",
];

export function PlannedDonut({ payload }: { payload: PlannedDonutPayload }) {
  const slices = payload.slices ?? [];
  const total = slices.reduce((sum, s) => sum + s.value, 0);
  const data = slices.map((s, i) => ({
    name: s.label,
    value: s.value,
    percentage: total > 0 ? (s.value / total) * 100 : 0,
    color: PALETTE[i % PALETTE.length],
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
              {formatInrCompact(total)}
            </span>
          </div>
        </div>

        <div className="flex-1 space-y-1">
          {data.map((item) => (
            <div
              key={item.name}
              className="flex items-center justify-between border-b border-dashed border-border/60 py-2 last:border-b-0"
            >
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="h-2.5 w-2.5 rounded shrink-0"
                  style={{ backgroundColor: item.color }}
                />
                <span className="text-xs text-foreground font-medium truncate">
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

- [ ] **Step 4: Manual checkpoint**

---

### Task 13: Frontend rewrite of `TaxCostBar`

**Files:**
- Create: `Prozpr_Frontend/src/components/chat/visualization_tools/TaxCostBar/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create directory**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/TaxCostBar
```

- [ ] **Step 2: Create `TaxCostBar/types.ts`**

```ts
export type {
  TaxCostBar as TaxCostBarPayload,
  TaxCostNamedSeries,
  TaxCostTotals,
} from "../types";
```

- [ ] **Step 3: Create `TaxCostBar/Chart.tsx`**

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
import type { TaxCostBarPayload } from "./types";
import { formatInrCompact } from "@/lib/utils";

const SERIES_COLOR: Record<string, string> = {
  "Short-term gains": "hsl(0 72% 51%)",        // destructive — STCG hurts most
  "Long-term gains": "hsl(38 80% 48%)",        // wealth-amber — LTCG is taxed less
  "Exit load": "hsl(220 13% 64%)",             // muted — admin friction
};

const FALLBACK = ["hsl(0 72% 51%)", "hsl(38 80% 48%)", "hsl(220 13% 64%)"];

function colorFor(name: string, i: number): string {
  return SERIES_COLOR[name] ?? FALLBACK[i % FALLBACK.length];
}

export function TaxCostBar({ payload }: { payload: TaxCostBarPayload }) {
  const { categories, series, totals } = payload;
  const data = categories.map((category, idx) => {
    const row: Record<string, string | number> = { category };
    for (const s of series) {
      row[s.name] = s.values[idx] ?? 0;
    }
    return row;
  });
  const chartHeight = Math.max(180, categories.length * 40);

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
            margin={{ top: 4, right: 12, left: 4, bottom: 4 }}
          >
            <CartesianGrid horizontal={false} stroke="hsl(var(--border))" strokeOpacity={0.4} />
            <XAxis
              type="number"
              tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }}
              tickFormatter={(v: number) => formatInrCompact(v)}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              type="category"
              dataKey="category"
              width={110}
              tick={{ fontSize: 11, fill: "hsl(var(--foreground))" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              cursor={{ fill: "rgba(0,0,0,0.04)" }}
              formatter={(value: number, name) => [formatInrCompact(value), name]}
              contentStyle={{ fontSize: "11px", borderRadius: "6px" }}
            />
            <Legend wrapperStyle={{ fontSize: "11px", paddingTop: "4px" }} iconSize={10} />
            {series.map((s, i) => (
              <Bar
                key={s.name}
                dataKey={s.name}
                stackId="cost"
                fill={colorFor(s.name, i)}
                radius={[0, 4, 4, 0]}
                barSize={11}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2">
        <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
          <span className="text-[11px] text-muted-foreground">Tax estimate</span>
          <span className="text-xs font-semibold text-foreground tabular-nums">
            {formatInrCompact(totals.tax_estimate_inr)}
          </span>
        </div>
        <div className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
          <span className="text-[11px] text-muted-foreground">Exit load</span>
          <span className="text-xs font-semibold text-foreground tabular-nums">
            {formatInrCompact(totals.exit_load_inr)}
          </span>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Manual checkpoint**

---

### Task 14: Frontend `TopBottomFunds` chart component

**Files:**
- Create: `Prozpr_Frontend/src/components/chat/visualization_tools/TopBottomFunds/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create directory + types**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/TopBottomFunds
```

`types.ts`:

```ts
export type { TopBottomFunds as TopBottomFundsPayload, FundReturnRow } from "../types";
```

- [ ] **Step 2: Create `Chart.tsx`**

```tsx
import type { TopBottomFundsPayload, FundReturnRow } from "./types";

function Row({ row, tone }: { row: FundReturnRow; tone: "up" | "down" }) {
  const sign = row.return_pct >= 0 ? "+" : "";
  const colorClass = tone === "up" ? "text-[hsl(160_50%_28%)]" : "text-destructive";
  const barColor = tone === "up" ? "hsl(160 50% 38%)" : "hsl(0 72% 51%)";
  // Bar width is proportional to absolute return, capped at 100% for visual scaling.
  const widthPct = Math.min(100, Math.abs(row.return_pct) * 3);
  return (
    <div className="flex items-center gap-3 py-1.5">
      <span className="text-xs text-foreground font-medium truncate flex-1 min-w-0">
        {row.name}
      </span>
      <div className="relative h-2 w-24 bg-muted rounded">
        <div
          className="absolute left-0 top-0 h-full rounded"
          style={{ backgroundColor: barColor, width: `${widthPct}%` }}
        />
      </div>
      <span className={`text-xs font-semibold tabular-nums w-12 text-right ${colorClass}`}>
        {sign}
        {row.return_pct.toFixed(1)}%
      </span>
    </div>
  );
}

export function TopBottomFunds({ payload }: { payload: TopBottomFundsPayload }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-wealth">
      <h3 className="font-display italic text-foreground text-xl leading-tight mb-1">
        {payload.title}
      </h3>
      {payload.subtitle ? (
        <p className="text-xs text-muted-foreground mb-4">{payload.subtitle}</p>
      ) : null}

      <div className="grid grid-cols-1 gap-4">
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1 font-semibold">
            Top performers
          </p>
          <div className="divide-y divide-border/60">
            {payload.top.map((r) => (
              <Row key={`top-${r.name}`} row={r} tone="up" />
            ))}
          </div>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground mb-1 font-semibold">
            Bottom performers
          </p>
          <div className="divide-y divide-border/60">
            {payload.bottom.map((r) => (
              <Row key={`bot-${r.name}`} row={r} tone="down" />
            ))}
          </div>
        </div>
      </div>

      <p className="mt-3 text-[11px] text-muted-foreground tabular-nums">
        Portfolio average: {payload.portfolio_average_pct >= 0 ? "+" : ""}
        {payload.portfolio_average_pct.toFixed(1)}%
      </p>
    </div>
  );
}
```

- [ ] **Step 3: Manual checkpoint**

---

### Task 15: Frontend `ProfileDial` chart component

**Files:**
- Create: `Prozpr_Frontend/src/components/chat/visualization_tools/ProfileDial/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create directory + types**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/ProfileDial
```

`types.ts`:

```ts
export type { ProfileDial as ProfileDialPayload } from "../types";
```

- [ ] **Step 2: Create `Chart.tsx`**

```tsx
import type { ProfileDialPayload } from "./types";

const BANDS = [
  { label: "Conservative", from: 0, to: 20, fill: "hsl(160 30% 93%)" },
  { label: "Moderate-Conservative", from: 20, to: 40, fill: "hsl(160 50% 75%)" },
  { label: "Balanced", from: 40, to: 60, fill: "hsl(215 40% 75%)" },
  { label: "Moderate-Aggressive", from: 60, to: 80, fill: "hsl(38 70% 70%)" },
  { label: "Aggressive", from: 80, to: 100, fill: "hsl(0 70% 75%)" },
];

export function ProfileDial({ payload }: { payload: ProfileDialPayload }) {
  const score = Math.max(0, Math.min(100, payload.score));
  // Half-circle SVG dial: 200x110 viewbox, dial arc from -180° (left) to 0° (right).
  // Needle angle in degrees: -180 at score=0, 0 at score=100.
  const angle = -180 + (score / 100) * 180;
  const cx = 100;
  const cy = 100;
  const needleLen = 70;
  const rad = (angle * Math.PI) / 180;
  const tipX = cx + needleLen * Math.cos(rad);
  const tipY = cy + needleLen * Math.sin(rad);

  // Band arc paths
  const arcs = BANDS.map((band) => {
    const startAngle = -180 + (band.from / 100) * 180;
    const endAngle = -180 + (band.to / 100) * 180;
    const r = 86;
    const innerR = 60;
    const x1 = cx + r * Math.cos((startAngle * Math.PI) / 180);
    const y1 = cy + r * Math.sin((startAngle * Math.PI) / 180);
    const x2 = cx + r * Math.cos((endAngle * Math.PI) / 180);
    const y2 = cy + r * Math.sin((endAngle * Math.PI) / 180);
    const ix1 = cx + innerR * Math.cos((endAngle * Math.PI) / 180);
    const iy1 = cy + innerR * Math.sin((endAngle * Math.PI) / 180);
    const ix2 = cx + innerR * Math.cos((startAngle * Math.PI) / 180);
    const iy2 = cy + innerR * Math.sin((startAngle * Math.PI) / 180);
    const d = [
      `M ${x1} ${y1}`,
      `A ${r} ${r} 0 0 1 ${x2} ${y2}`,
      `L ${ix1} ${iy1}`,
      `A ${innerR} ${innerR} 0 0 0 ${ix2} ${iy2}`,
      "Z",
    ].join(" ");
    return { d, fill: band.fill, label: band.label };
  });

  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-wealth">
      <h3 className="font-display italic text-foreground text-xl leading-tight mb-1">
        {payload.title}
      </h3>
      {payload.subtitle ? (
        <p className="text-xs text-muted-foreground mb-4">{payload.subtitle}</p>
      ) : null}

      <div className="flex flex-col items-center">
        <svg viewBox="0 0 200 120" className="w-full max-w-[260px]" role="img" aria-label="Risk profile dial">
          {arcs.map((a) => (
            <path key={a.label} d={a.d} fill={a.fill} />
          ))}
          <line
            x1={cx}
            y1={cy}
            x2={tipX}
            y2={tipY}
            stroke="hsl(222 47% 14%)"
            strokeWidth={2.5}
            strokeLinecap="round"
          />
          <circle cx={cx} cy={cy} r={4} fill="hsl(222 47% 14%)" />
        </svg>
        <p className="mt-2 text-sm font-semibold text-foreground">{payload.headline}</p>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Manual checkpoint**

---

### Task 16: Frontend `BuySellLedger` chart component

**Files:**
- Create: `Prozpr_Frontend/src/components/chat/visualization_tools/BuySellLedger/{Chart.tsx, types.ts}`

- [ ] **Step 1: Create directory + types**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/BuySellLedger
```

`types.ts`:

```ts
export type { BuySellLedger as BuySellLedgerPayload, BuySellRow } from "../types";
```

- [ ] **Step 2: Create `Chart.tsx`**

```tsx
import type { BuySellLedgerPayload } from "./types";
import { formatInrCompact } from "@/lib/utils";

export function BuySellLedger({ payload }: { payload: BuySellLedgerPayload }) {
  return (
    <div className="rounded-2xl border border-border bg-card p-5 shadow-wealth">
      <h3 className="font-display italic text-foreground text-xl leading-tight mb-1">
        {payload.title}
      </h3>
      {payload.subtitle ? (
        <p className="text-xs text-muted-foreground mb-4">{payload.subtitle}</p>
      ) : null}

      <div className="overflow-hidden rounded-lg border border-border">
        <table className="w-full text-xs">
          <thead className="bg-muted/30">
            <tr>
              <th className="text-left font-semibold text-muted-foreground uppercase tracking-wide text-[10px] px-3 py-2">
                Fund
              </th>
              <th className="text-right font-semibold text-muted-foreground uppercase tracking-wide text-[10px] px-3 py-2 w-20">
                Buy
              </th>
              <th className="text-right font-semibold text-muted-foreground uppercase tracking-wide text-[10px] px-3 py-2 w-20">
                Sell
              </th>
            </tr>
          </thead>
          <tbody>
            {payload.rows.map((row) => (
              <tr key={`${row.name}-${row.sub_category}`} className="border-t border-border/60">
                <td className="px-3 py-2">
                  <div className="text-foreground font-medium truncate">{row.name}</div>
                  <div className="text-[10px] text-muted-foreground">{row.sub_category}</div>
                </td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-[hsl(160_50%_28%)]">
                  {row.buy_inr > 0 ? formatInrCompact(row.buy_inr) : "—"}
                </td>
                <td className="px-3 py-2 text-right tabular-nums font-semibold text-destructive">
                  {row.sell_inr > 0 ? formatInrCompact(row.sell_inr) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Manual checkpoint**

---

### Task 17: Update `ChartRenderer.tsx` (drop legacy fallback) + archive old `rebalancing/` folder

**Files:**
- Modify: `Prozpr_Frontend/src/components/chat/visualization_tools/ChartRenderer.tsx`
- Move: `Prozpr_Frontend/src/components/chat/visualization_tools/rebalancing/` → `Prozpr_Frontend/src/components/chat/visualization_tools/_archive/rebalancing_pre_central_registry/`

- [ ] **Step 1: Replace `ChartRenderer.tsx`**

```tsx
import type { ChartPayload } from "./types";
import { CurrentDonut } from "./CurrentDonut/Chart";
import { ConcentrationRisk } from "./ConcentrationRisk/Chart";
import { TargetVsActual } from "./TargetVsActual/Chart";
import { TopBottomFunds } from "./TopBottomFunds/Chart";
import { ProfileDial } from "./ProfileDial/Chart";
import { CategoryGapBar } from "./CategoryGapBar/Chart";
import { PlannedDonut } from "./PlannedDonut/Chart";
import { TaxCostBar } from "./TaxCostBar/Chart";
import { BuySellLedger } from "./BuySellLedger/Chart";

interface ChartRendererProps {
  payload: ChartPayload;
}

export function ChartRenderer({ payload }: ChartRendererProps) {
  switch (payload.type) {
    case "current_donut":
      return <CurrentDonut payload={payload} />;
    case "concentration_risk":
      return <ConcentrationRisk payload={payload} />;
    case "target_vs_actual":
      return <TargetVsActual payload={payload} />;
    case "top_bottom_funds":
      return <TopBottomFunds payload={payload} />;
    case "profile_dial":
      return <ProfileDial payload={payload} />;
    case "category_gap_bar":
      return <CategoryGapBar payload={payload} />;
    case "planned_donut":
      return <PlannedDonut payload={payload} />;
    case "tax_cost_bar":
      return <TaxCostBar payload={payload} />;
    case "buy_sell_ledger":
      return <BuySellLedger payload={payload} />;
    default: {
      const _exhaustive: never = payload;
      return null;
    }
  }
}
```

- [ ] **Step 2: Archive the old rebalancing folder**

```bash
mkdir -p /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/_archive
mv /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/rebalancing \
   /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend/src/components/chat/visualization_tools/_archive/rebalancing_pre_central_registry
```

- [ ] **Step 3: Type-check the frontend**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend && npx tsc --noEmit -p tsconfig.app.json 2>&1 | head -20
```

Expected: clean (exit 0, no errors). If anything fails, the most likely cause is a payload-type mismatch in one of the per-chart `Chart.tsx` files; read the error and adjust the cast or the payload field shape.

- [ ] **Step 4: Try the dev build (optional smoke)**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend && npm run build 2>&1 | tail -10
```

Expected: build success. (Skip if `npm run build` is unavailable; the tsc check above is the contract.)

- [ ] **Step 5: Manual checkpoint**

Move on to docs.

---

## Phase 6 — Docs + final smoke

### Task 18: Regenerate `docs/charts.md` (now 9 charts) + final test sweep

- [ ] **Step 1: Regenerate the catalogue**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 scripts/regen_chart_docs.py
```

Expected: `wrote docs/charts.md (9 charts)`.

- [ ] **Step 2: Inspect the output**

```bash
head -10 /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/docs/charts.md
```

Expected: header reads `_9 charts registered._`. Sections appear alphabetically: `buy_sell_ledger`, `category_gap_bar`, `concentration_risk`, `current_donut`, `planned_donut`, `profile_dial`, `target_vs_actual`, `tax_cost_bar`, `top_bottom_funds`.

- [ ] **Step 3: Full backend test sweep**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend && python3 -m pytest app/ 2>&1 | tail -10
```

Expected: pass. Compare the count to Plan 1's 151 — should be at least 151 + 12 (2 tests × 6 new chart builders) + 1-2 brain integration tests.

- [ ] **Step 4: Frontend type-check + (optional) build**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend && npx tsc --noEmit -p tsconfig.app.json 2>&1 | head -10
echo "---"
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Frontend && npm run build 2>&1 | tail -5
```

Expected: tsc exit 0; build succeeds.

- [ ] **Step 5: Manual checkpoint**

Done. Plan 2 complete: 9 charts in central registry, no chart_picker.py, no `chart_type` fallback, full editorial-wealth visual language across both AA and rebalancing answers.

---

## Self-review against the spec

The plan covers:

- ✅ Migrate 3 rebal charts to typed payloads (Tasks 2, 3, 4)
- ✅ Build 3 new charts: `top_bottom_funds`, `profile_dial`, `buy_sell_ledger` (Tasks 5, 6, 7)
- ✅ `build_rebalancing.py` rebal-side dispatcher (Task 1)
- ✅ Wire selector into rebalancing branch of `brain.py` (Task 8)
- ✅ Strip chart-related code from `ai_bridge/rebalancing/{chat,service}.py` (Task 8)
- ✅ Archive `chart_picker.py` + `charts.py` + their tests (Task 9)
- ✅ Frontend rewrite of 3 rebal charts in editorial-wealth style (Tasks 11, 12, 13)
- ✅ Frontend new chart components: `TopBottomFunds`, `ProfileDial`, `BuySellLedger` (Tasks 14, 15, 16)
- ✅ Update frontend types to drop legacy rebal shape (Task 10)
- ✅ Drop `chart_type` fallback in `ChartRenderer.tsx`; archive old rebal frontend folder (Task 17)
- ✅ Regenerate `docs/charts.md` (Task 18)
- ✅ Full backend + frontend smoke (Task 18)

**Caveat acknowledged in plan body:** Task 8 step 5 notes that the rebalancing branch's selector currently runs *after* `dispatch_chat` returns rather than truly parallel with the formatter LLM (because the formatter is invoked inside `dispatch_chat`). Achieving full parallelism would require refactoring `dispatch_chat` to expose the formatter task — explicitly deferred. The Plan 2 win is removing the chart-picker LLM from the critical path entirely, which alone saves ~1-2s per rebalancing turn.

**Unknowns that may need adaptation during execution** (each will surface as `DONE_WITH_CONCERNS`):
- The exact shape of `RebalancingRunOutcome` / dispatch result type — Task 8 step 4(a) instructs the implementer to discover and add the `rebalancing_response` field where it lives (likely `chat_dispatcher.py`).
- The exact field name for fund identity on `FundRowAfterStep5` (`fund_name` vs `name` vs `instrument_name`) — Task 7's builder uses `getattr` chain to be tolerant; report which one was actually present.
- Whether the rebalancing autouse `_no_llm_chart_picker` fixture in `app/services/ai_bridge/rebalancing/tests/conftest.py` needs deletion after the picker module moves — Task 9 step 4 covers this.
