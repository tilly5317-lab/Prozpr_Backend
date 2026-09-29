# SIP-First Goal Waterfall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fund short-term goals nearest first from held short-term money, then a front-loaded SIP, then corpus — computed once in allocation step 2 and read by rebalancing, SIP and lumpsum.

**Architecture:** A new pure function `goal_waterfall()` in `asset_allocation_pydantic/steps/step2_short_term.py` decides step 2's `allocated_amount` and the SIP's `monthly_sip_to_goals`. Both allocation engines surface the result as `goal_funding`. App builders supply three inputs (goal future value, stated SIP, held short-term money); the fresh-money engine's only split becomes "goal money first, then the long-term plan"; rebalancing stops swapping one held debt fund for another.

**Tech Stack:** Python 3, pydantic v2, pytest (`asyncio_mode=auto`), FastAPI app layer, SQLAlchemy async (read-only here).

**Spec:** `docs/superpowers/specs/2026-09-28-sip-first-goal-waterfall-design.md`

## Global Constraints

- Run from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`. Tests: `.venv-mac/bin/python -m pytest …` (`pyproject.toml` puts `AI_Agents/src` and `.` on the path).
- `SCRATCH=/private/tmp/claude-502/-Users-Amoul-Documents-AILAX-AI-Financial-advisor-ailax-Prozpr-Backend/2a5a4f5f-632d-45d2-9f88-ef37fe2ae7ec/scratchpad`
- **Do not commit.** Project rule: leave every change in the working tree; each task ends with a checkpoint.
- **Edit with the Edit tool.** Several `app/` files are CRLF; a Python text-mode rewrite silently converts them to LF.
- Tracked tests: `AI_Agents/tests/` (flat files) and `app/**/tests/`. `AI_Agents/src/*/Testing/` is gitignored — touch it only where a task says so.
- Comments only where a reader would otherwise introduce a bug. No narrative.
- No database migration; `target_bucket` keeps its enum values.
- **Names — one per concept, in the allocation family's style (no `_inr` suffix):**

| Concept | Name |
|---|---|
| Stated monthly SIP (S) | `monthly_sip` |
| Held short-term money (H) | `short_term_holdings`; subgroup set `SHORT_TERM_HOLDING_SUBGROUPS`; total `short_term_holdings_total()` |
| Short-term target (T) | `allocated_amount` (as on `Step2Output`) |
| Corpus moved now (C) | `from_corpus` |
| SIP's goal share (s) | `monthly_sip_to_goals` |
| Shortfall | `shortfall` |
| Routed subgroup | `asset_subgroup` |
| Goal cost on its date | `amount_needed_fv` |
| Per-goal parts | `from_holdings`, `from_sip`, `from_corpus`, `shortfall` |
| Fresh-money engine (uses `_inr`) | `goal_share_inr`, `goal_subgroup` |

- Defaults (`monthly_sip=0.0`, `short_term_holdings=None`, `amount_needed_fv=None`) reproduce today's step 2 exactly.
- `SIP_REVIEW_WINDOW_MONTHS = 6`. SIP long-term plan floor: `_SIP_MIN_LONG_TERM_COLUMN_INR = 10_000.0`.
- Preference customers are untouched (`_no_carveout_buckets` already replaces step 2).
- Versions: Rebalancing `ENGINE_VERSION` `1.13.0` → `1.14.0`; `AINV_ENGINE_VERSION` `ainv-3.4.0` → `ainv-3.5.0`.
- Domain imports: rebalancing must never import `additional_investment`; `AI_Agents/src` never imports `app/`.

## File Structure

| File | Responsibility |
|---|---|
| `AI_Agents/src/asset_allocation_pydantic/models.py` | Input fields; `GoalFunding`, `GoalFundingRow`; `goal_funding` on `Step2Output`, `GoalAllocationOutput` |
| `AI_Agents/src/asset_allocation_pydantic/tables.py`, `utils.py` | `SIP_REVIEW_WINDOW_MONTHS`; `ceil_to_100` |
| `AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py` | `goal_waterfall()`; step 2 carves its `allocated_amount` |
| `AI_Agents/src/asset_allocation_pydantic/steps/step7_presentation.py` | Ideal output carries `goal_funding` |
| `AI_Agents/src/practical_asset_allocation/pipeline.py` | Practical output carries `goal_funding` |
| `AI_Agents/src/cashflow_statement/engine/goals_table.py` (+ both `__init__.py`) | Public `custom_goal_fv()` |
| `app/domains/cashflow/services/goal_planning_engine/input_builder.py` | Public `map_custom_goal()` |
| `app/domains/asset_allocation/services/aa_engine/input_builder.py` | Fills FV, `monthly_sip`, `short_term_holdings=0.0` |
| `app/domains/mutual_funds/services/scheme_classification.py` | `SHORT_TERM_HOLDING_SUBGROUPS`, `short_term_holdings_total()` |
| `app/domains/portfolio/services/holdings_snapshot.py` (new) | Pure `HoldingsSnapshot`, `aggregate_holdings`, `snapshot_from_holdings` (moved from ainv) |
| `app/domains/additional_investment/services/ainv_engine/holdings_snapshot.py` | Keeps `load_holdings_snapshot`; re-exports the moved names |
| `app/domains/practical_asset_allocation/services/paa_engine/input_builder.py`, `service.py` | Holdings from the preloaded user unless passed; SIP override |
| `app/domains/rebalancing/services/rebal_engine/input_builder.py` | Passes holdings from its ledger rows |
| `AI_Agents/src/Rebalancing/tables.py`, `steps/step2b_suppress_debt_switch.py`, `models.py`, `config.py` | Never swap one held debt fund for another |
| `AI_Agents/src/additional_investment/models.py`, `ratio.py`, `pipeline.py` | Goal-first is the only split; legacy single-bucket mode deleted |
| `app/domains/additional_investment/services/ainv_engine/input_builder.py`, `service.py` | Goal share wiring, long-term holdings for lumpsum, facts, rescue, profile gate removed |
| `AI_Agents/lifecycle_sim_testing/*` (gitignored) | Before/after churn metrics |

---

### Task 0: Baseline

**Files:**
- Create: `AI_Agents/lifecycle_sim_testing/compare_waterfall.py` (gitignored, dev-only)

**Interfaces:**
- Produces: `$SCRATCH/baseline_targeted.txt`, `$SCRATCH/baseline_full.txt`, `AI_Agents/lifecycle_sim_testing/waterfall_metrics_before.json`.

- [ ] **Step 1: Targeted baseline**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors -rfE AI_Agents/tests AI_Agents/src/asset_allocation_pydantic AI_Agents/src/practical_asset_allocation AI_Agents/src/additional_investment AI_Agents/src/Rebalancing AI_Agents/src/cashflow_statement app/domains/additional_investment app/domains/practical_asset_allocation app/domains/asset_allocation app/domains/rebalancing app/domains/cashflow app/domains/profile app/domains/portfolio app/domains/mutual_funds 2>&1 | grep -E "^(FAILED|ERROR)|passed|failed" | sort > $SCRATCH/baseline_targeted.txt
tail -3 $SCRATCH/baseline_targeted.txt
```

- [ ] **Step 2: Full-suite baseline (about 12 minutes)**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors -rfE 2>&1 | grep -E "^(FAILED|ERROR)" | sort > $SCRATCH/baseline_full.txt
wc -l $SCRATCH/baseline_full.txt
```

Bare `pytest -q` collects nothing; the flag is required. Compare node ids later, not counts.

- [ ] **Step 3: Write the sim comparison script**

```python
"""Churn metrics for the SIP-first goal waterfall, before vs after (dev-only).

Run from AI_Agents/:  ../.venv-mac/bin/python -m lifecycle_sim_testing.compare_waterfall <label>
Writes lifecycle_sim_testing/waterfall_metrics_<label>.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import constants as c  # noqa: E402
import simulate  # noqa: E402
from Rebalancing.Testing.Master_testing.profiles import PROFILES  # noqa: E402

_SELLS = {"SELL", "EXIT"}


def _profile_metrics(res) -> dict:
    rebalances = []
    for e in res.events:
        if e["kind"] != "rebalance":
            continue
        sold = {"equity": 0.0, "debt": 0.0, "gold": 0.0}
        bought = {"equity": 0.0, "debt": 0.0, "gold": 0.0}
        for t in e["trades"]:
            cls = c.asset_class_of(t["subgroup"])
            if t["action"] in _SELLS:
                sold[cls] += t["amount"]
            elif t["action"] == "BUY":
                bought[cls] += t["amount"]
        rebalances.append({
            "month": e["month"],
            "equity_sold": round(sold["equity"]),
            "debt_bought": round(bought["debt"]),
            "debt_to_debt": round(min(sold["debt"], bought["debt"])),
        })
    sip_debt = {
        e["month"]: round(sum(b["amount"] for b in e["buys"]
                              if c.asset_class_of(b["subgroup"]) == "debt"))
        for e in res.events if e["kind"] == "sip"
    }
    debt_months = [m for m, v in sip_debt.items() if v > 0]
    return {
        "rebalances": rebalances,
        "equity_sold_total": sum(r["equity_sold"] for r in rebalances),
        "debt_to_debt_total": sum(r["debt_to_debt"] for r in rebalances),
        "sip_to_debt_by_month": sip_debt,
        "last_month_sip_bought_debt": max(debt_months) if debt_months else None,
        "goal_shortfall": round(res.metrics["shortfall"]),
    }


def main() -> None:
    label = sys.argv[1] if len(sys.argv) > 1 else "run"
    out = {name: _profile_metrics(simulate.simulate_profile(name)) for name in PROFILES}
    path = Path(__file__).resolve().parent / f"waterfall_metrics_{label}.json"
    path.write_text(json.dumps(out, indent=1))
    for name, m in out.items():
        print(f"{name}: equity_sold={m['equity_sold_total']:,} "
              f"debt_to_debt={m['debt_to_debt_total']:,} "
              f"last_sip_debt_month={m['last_month_sip_bought_debt']} "
              f"shortfall={m['goal_shortfall']:,}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Capture "before" sim metrics**

```bash
cd AI_Agents && ../.venv-mac/bin/python -m lifecycle_sim_testing.compare_waterfall before; cd ..
```

Expected: one line per profile; `waterfall_metrics_before.json` written.

- [ ] **Step 5: Checkpoint** — no source changed; the three baseline files exist.

---

### Task 1: `GoalFunding` models and `goal_waterfall()`

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/models.py`, `tables.py`, `utils.py`, `steps/step2_short_term.py`
- Test: `AI_Agents/tests/test_goal_waterfall.py`

**Interfaces:**
- Produces:
  - `Goal.amount_needed_fv: Optional[float] = None`
  - `AllocationInput.monthly_sip: float = 0.0`, `AllocationInput.short_term_holdings: Optional[float] = None`
  - `GoalFundingRow(goal_name: str, time_to_goal_months: int, amount_needed_fv: float, from_holdings: float, from_sip: float, from_corpus: float, shortfall: float)`
  - `GoalFunding(allocated_amount: int, from_corpus: float, shortfall: int, monthly_sip: float, monthly_sip_to_goals: float, asset_subgroup: Literal["short_debt","arbitrage"], goals: list[GoalFundingRow])`
  - `goal_waterfall(goals: list[Goal], short_term_holdings: Optional[float], monthly_sip: float, remaining_corpus: int, asset_subgroup: Literal["short_debt","arbitrage"]) -> GoalFunding`
  - `tables.SIP_REVIEW_WINDOW_MONTHS = 6`; `utils.ceil_to_100(x: float) -> int`

- [ ] **Step 1: Write the failing tests**

`AI_Agents/tests/test_goal_waterfall.py`:

```python
"""goal_waterfall: held short-term money first, then the front-loaded SIP, then corpus."""

from __future__ import annotations

import random

import pytest

from asset_allocation_pydantic.models import Goal
from asset_allocation_pydantic.steps.step2_short_term import goal_waterfall

L = 100_000
CORPUS = 100 * L


def _g(name, months, amount, fv=None):
    return Goal(goal_name=name, time_to_goal_months=months, amount_needed=amount,
                goal_priority="non_negotiable", amount_needed_fv=fv)


FOUR_GOALS = [_g("G1", 6, 3 * L), _g("G2", 10, 4 * L), _g("G3", 14, 2 * L), _g("G4", 20, 6 * L)]


def _run(goals, holdings, sip, corpus=CORPUS, subgroup="short_debt"):
    return goal_waterfall(goals, holdings, sip, corpus, subgroup)


def test_holdings_cover_near_goals_and_full_sip_builds_the_last():
    f = _run(FOUR_GOALS, 9 * L, 50_000)
    assert f.allocated_amount == 9 * L
    assert f.from_corpus == 0
    assert f.shortfall == 0
    assert f.monthly_sip_to_goals == 50_000
    rows = {r.goal_name: r for r in f.goals}
    assert [rows[n].from_holdings for n in ("G1", "G2", "G3")] == [3 * L, 4 * L, 2 * L]
    assert rows["G4"].from_sip == 6 * L
    assert rows["G4"].from_corpus == 0


def test_small_sip_leaves_a_gap_the_corpus_covers_now():
    f = _run(FOUR_GOALS, 9 * L, 20_000)
    assert f.from_corpus == 2 * L
    assert f.allocated_amount == 11 * L
    assert f.monthly_sip_to_goals == 20_000
    g4 = next(r for r in f.goals if r.goal_name == "G4")
    assert (g4.from_sip, g4.from_corpus, g4.shortfall) == (4 * L, 2 * L, 0)


def test_last_window_splits_the_sip():
    assert _run([_g("Car", 20, 5 * L)], 0.0, 50_000).monthly_sip_to_goals == 50_000
    assert _run([_g("Car", 14, 5 * L)], 3 * L, 50_000).monthly_sip_to_goals == 33_400


def test_goal_inside_the_window_is_met_on_time():
    f = _run([_g("Fees", 2, 1 * L)], 0.0, 50_000)
    assert f.from_corpus == 0
    assert f.monthly_sip_to_goals == 50_000


def test_corpus_too_small_reports_shortfall_and_full_sip():
    f = _run([_g("House", 12, 10 * L)], 0.0, 10_000, corpus=3 * L)
    assert f.allocated_amount == 3 * L
    assert f.shortfall == 580_000
    assert f.monthly_sip_to_goals == 10_000


def test_no_holdings_on_file_sends_the_whole_sip_to_goals():
    f = _run([_g("Car", 20, 6 * L)], None, 20_000, corpus=50 * L)
    assert f.allocated_amount == 2 * L
    assert f.from_corpus == 2 * L
    assert f.monthly_sip_to_goals == 20_000
    car = f.goals[0]
    assert (car.from_sip, car.from_corpus, car.shortfall) == (4 * L, 2 * L, 0)


def test_holdings_beyond_every_goal_send_the_sip_long_term():
    f = _run([_g("Trip", 10, 5 * L)], 20 * L, 50_000)
    assert f.allocated_amount == 5 * L
    assert f.monthly_sip_to_goals == 0


def test_no_sip_carves_the_gap_after_holdings():
    f = _run([_g("A", 6, 3 * L), _g("B", 12, 2 * L)], 1 * L, 0.0)
    assert f.allocated_amount == 5 * L
    assert f.from_corpus == 4 * L
    assert f.monthly_sip_to_goals == 0


def test_no_goals():
    f = _run([], 5 * L, 50_000)
    assert f.allocated_amount == 0
    assert f.monthly_sip_to_goals == 0
    assert f.goals == []


def test_goal_share_never_exceeds_an_odd_sip():
    assert _run([_g("Car", 20, 10 * L)], 0.0, 12_345).monthly_sip_to_goals == 12_345
    assert _run([_g("Gift", 20, 20_000)], 0.0, 12_345).monthly_sip_to_goals == 3_400


def test_future_value_is_used_when_given():
    f = _run([_g("Car", 12, 5 * L, fv=6 * L)], 0.0, 0.0)
    assert f.allocated_amount == 6 * L
    assert f.goals[0].amount_needed_fv == 6 * L


def test_attribution_invariants_hold_on_random_cases():
    rng = random.Random(20260928)
    for _ in range(2000):
        goals = [_g(f"g{i}", rng.randint(1, 35), rng.randint(1, 5000) * 1000)
                 for i in range(rng.randint(1, 5))]
        holdings = float(rng.randint(0, 3000) * 1000)
        sip = float(rng.randint(0, 200) * 500)
        f = goal_waterfall(goals, holdings, sip, 10**12, "arbitrage")
        for r in f.goals:
            parts = r.from_holdings + r.from_sip + r.from_corpus + r.shortfall
            assert parts == pytest.approx(r.amount_needed_fv)
        left, cum, need = holdings, 0.0, 0.0
        for g in sorted(goals, key=lambda g: g.time_to_goal_months):
            used = min(left, g.amount_needed)
            left -= used
            cum += g.amount_needed - used
            need = max(need, cum - sip * g.time_to_goal_months)
        assert sum(r.from_corpus + r.shortfall for r in f.goals) == pytest.approx(need)
        assert 0 <= f.monthly_sip_to_goals <= sip
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_goal_waterfall.py -q`
Expected: collection error — `cannot import name 'goal_waterfall'`.

- [ ] **Step 3: Input fields**

`models.py` — `Goal`, after `investment_goal`:

```python
    # Cost on the goal date. Read by step 2 only; None falls back to amount_needed.
    amount_needed_fv: Optional[float] = Field(default=None, gt=0)
```

`AllocationInput`, after `months_to_fy_end`:

```python
    monthly_sip: float = Field(default=0.0, ge=0)
    # Held debt + arbitrage funds (not income-plus-arbitrage). None = no holdings on file.
    short_term_holdings: Optional[float] = Field(default=None, ge=0)
```

- [ ] **Step 4: Output models** — `models.py`, directly after `class FutureInvestment`:

```python
class GoalFundingRow(BaseModel):
    goal_name: str
    time_to_goal_months: int
    amount_needed_fv: float = Field(..., ge=0)
    from_holdings: float = Field(..., ge=0)
    from_sip: float = Field(..., ge=0)
    from_corpus: float = Field(..., ge=0)
    shortfall: float = Field(..., ge=0)


class GoalFunding(BaseModel):
    allocated_amount: int = Field(..., ge=0)
    from_corpus: float = Field(..., ge=0)
    shortfall: int = Field(..., ge=0)
    monthly_sip: float = Field(..., ge=0)
    monthly_sip_to_goals: float = Field(..., ge=0)
    asset_subgroup: Literal["short_debt", "arbitrage"]
    goals: List[GoalFundingRow] = []
```

- [ ] **Step 5: Constant and rounding helper**

`tables.py`, after `HORIZON_BOUNDARY_MONTHS: int = 24`:

```python
# Months between reviews; the SIP's goal share is constant within one window.
SIP_REVIEW_WINDOW_MONTHS: int = 6
```

`utils.py`, after `round_to_100`:

```python
def ceil_to_100(x: float) -> int:
    """Round up to the next multiple of 100. Negative or zero inputs return 0."""
    if x <= 0:
        return 0
    return int(ceil(x / 100.0)) * 100
```

- [ ] **Step 6: `goal_waterfall()`** — in `steps/step2_short_term.py`, replace the imports with:

```python
from __future__ import annotations

from typing import Literal, Optional

from ..models import (
    AllocationInput,
    FutureInvestment,
    Goal,
    GoalFunding,
    GoalFundingRow,
    Step2Output,
)
from ..tables import (
    HORIZON_BOUNDARY_MONTHS,
    SIP_REVIEW_WINDOW_MONTHS,
    TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD,
)
from ..utils import ceil_to_100, round_to_100
```

and add after `_route`:

```python
def _future_value(goal: Goal) -> float:
    return goal.amount_needed_fv if goal.amount_needed_fv is not None else goal.amount_needed


def goal_waterfall(
    goals: list[Goal],
    short_term_holdings: Optional[float],
    monthly_sip: float,
    remaining_corpus: int,
    asset_subgroup: Literal["short_debt", "arbitrage"],
) -> GoalFunding:
    """Fund short-term goals nearest first: held short-term money, then the
    front-loaded SIP, then corpus for only what the SIP cannot reach in time.
    short_term_holdings=None means no holdings on file, so the SIP share
    assumes no corpus can be moved."""
    plan: list[tuple[Goal, float, float, float]] = []
    left = short_term_holdings or 0.0
    cum_gap = 0.0
    corpus_needed = 0.0
    for g in sorted(goals, key=lambda g: g.time_to_goal_months):
        fv = _future_value(g)
        from_holdings = min(left, fv)
        left -= from_holdings
        cum_gap += fv - from_holdings
        corpus_needed = max(corpus_needed, cum_gap - monthly_sip * g.time_to_goal_months)
        plan.append((g, fv, from_holdings, fv - from_holdings))

    held = sum(p[2] for p in plan)
    need = round_to_100(held + corpus_needed)
    allocated = min(need, remaining_corpus)
    from_corpus = max(0.0, allocated - held)
    corpus_for_sip = 0.0 if short_term_holdings is None else from_corpus

    to_goals = 0.0
    sip_need = cum_gap - corpus_for_sip
    if sip_need > 0 and monthly_sip > 0:
        rate = sip_need / SIP_REVIEW_WINDOW_MONTHS
        running = 0.0
        for g, _, _, gap in plan:
            running += gap
            if g.time_to_goal_months < SIP_REVIEW_WINDOW_MONTHS:
                rate = max(rate, (running - corpus_for_sip) / g.time_to_goal_months)
        to_goals = min(monthly_sip, ceil_to_100(rate))

    rows: list[GoalFundingRow] = []
    sip_used = 0.0
    corpus_left = from_corpus
    for g, fv, from_holdings, gap in plan:
        from_sip = min(gap, max(0.0, monthly_sip * g.time_to_goal_months - sip_used))
        sip_used += from_sip
        corpus_part = max(0.0, gap - from_sip)
        goal_from_corpus = min(corpus_part, corpus_left)
        corpus_left -= goal_from_corpus
        rows.append(
            GoalFundingRow(
                goal_name=g.goal_name,
                time_to_goal_months=g.time_to_goal_months,
                amount_needed_fv=fv,
                from_holdings=from_holdings,
                from_sip=from_sip,
                from_corpus=goal_from_corpus,
                shortfall=max(0.0, corpus_part - goal_from_corpus),
            )
        )

    return GoalFunding(
        allocated_amount=allocated,
        from_corpus=from_corpus,
        shortfall=need - allocated,
        monthly_sip=monthly_sip,
        monthly_sip_to_goals=to_goals,
        asset_subgroup=asset_subgroup,
        goals=rows,
    )
```

- [ ] **Step 7: Run the tests**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_goal_waterfall.py -q`
Expected: 12 passed.

- [ ] **Step 8: Checkpoint (no commit)**

---

### Task 2: Step 2 carves the waterfall's `allocated_amount`; both engines surface `goal_funding`

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py` (`run`), `models.py` (`Step2Output`, `GoalAllocationOutput`), `steps/step7_presentation.py` (`run`)
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py` (import, `PracticalAllocationOutput`, `_build_output`)
- Test: `AI_Agents/tests/test_goal_waterfall_step2.py`
- Re-pin: `AI_Agents/tests/fixtures/golden_practical_no_pref.json`, `golden_ideal_no_pref.json`

**Interfaces:**
- Consumes: Task 1.
- Produces: `goal_funding: Optional[GoalFunding] = None` on `Step2Output`, `GoalAllocationOutput`, `PracticalAllocationOutput` (`None` when a preference suspends step 2).

- [ ] **Step 1: Write the failing tests**

`AI_Agents/tests/test_goal_waterfall_step2.py`:

```python
"""Step 2 on the waterfall. The golden fixtures have goals=[] and never exercise
step 2, so the property test here is the guard that defaults reproduce it."""

from __future__ import annotations

import random

from asset_allocation_pydantic.models import AllocationInput, Goal
from asset_allocation_pydantic.steps import step2_short_term
from asset_allocation_pydantic.tables import HORIZON_BOUNDARY_MONTHS
from asset_allocation_pydantic.utils import round_to_100


def _inp(goals, **kw):
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, total_corpus=20_000_000.0,
        monthly_household_expense=100_000, effective_tax_rate=15.0, goals=goals,
    )
    base.update(kw)
    return AllocationInput(**base)


def _old_step2(inp, remaining):
    goals = [g for g in inp.goals
             if g.time_to_goal_months < HORIZON_BOUNDARY_MONTHS + inp.months_to_fy_end]
    total = round_to_100(sum(g.amount_needed for g in goals))
    allocated = min(total, remaining)
    gap = total - remaining if total > remaining else None
    return total, allocated, remaining - allocated, gap


def test_defaults_reproduce_the_old_step2():
    rng = random.Random(7)
    for _ in range(3000):
        goals = [
            Goal(goal_name=f"g{i}", time_to_goal_months=rng.randint(1, 40),
                 amount_needed=float(rng.randint(1, 50_000) * rng.choice([1, 10, 100])),
                 goal_priority=rng.choice(["negotiable", "non_negotiable"]))
            for i in range(rng.randint(0, 5))
        ]
        inp = _inp(goals, months_to_fy_end=rng.randint(0, 11),
                   effective_tax_rate=rng.choice([10.0, 30.0]))
        remaining = rng.randint(0, 400_000) * 100
        out = step2_short_term.run(inp, remaining)
        total, allocated, left, gap = _old_step2(inp, remaining)
        assert out.total_goal_amount == total
        assert out.allocated_amount == allocated
        assert out.remaining_corpus == left
        got_gap = out.future_investment.future_investment_amount if out.future_investment else None
        assert got_gap == gap
        assert out.subgroup_amounts == ({out.asset_subgroup: allocated} if allocated > 0 else {})


def _car_input():
    goals = [Goal(goal_name="Car", time_to_goal_months=12, amount_needed=600_000.0,
                  goal_priority="non_negotiable")]
    return _inp(goals, monthly_sip=30_000.0, short_term_holdings=100_000.0)


def test_both_engines_surface_the_same_goal_funding():
    from asset_allocation_pydantic.pipeline import run_allocation
    from asset_allocation_pydantic.steps import _rationale_llm
    from practical_asset_allocation.pipeline import (
        PracticalAllocationInput,
        run_practical_allocation,
    )

    inp = _car_input()
    ideal = run_allocation(inp, rationale_fn=_rationale_llm.no_llm_rationale_fn)
    assert ideal.goal_funding.allocated_amount == 240_000
    assert ideal.goal_funding.monthly_sip_to_goals == 30_000
    practical = run_practical_allocation(
        PracticalAllocationInput(**inp.model_dump(), mf_corpus=inp.total_corpus)
    )
    assert practical.goal_funding == ideal.goal_funding


def test_preference_run_has_no_goal_funding():
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import (
        PracticalAllocationInput,
        run_practical_allocation,
    )

    inp = _car_input()
    practical = run_practical_allocation(
        PracticalAllocationInput(
            **inp.model_dump(), mf_corpus=inp.total_corpus,
            human_override=HumanOverridePreferences(
                asset_class_requested={"equity": 60.0, "debt": 35.0, "others": 5.0}
            ),
        )
    )
    assert practical.goal_funding is None
```

- [ ] **Step 2: Run to verify**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_goal_waterfall_step2.py -q`
Expected: `test_defaults_reproduce_the_old_step2` passes (step 2 unchanged so far); the other two fail with `AttributeError: … 'goal_funding'`.

- [ ] **Step 3: Output fields** — add `goal_funding: Optional[GoalFunding] = None` as the last field of `Step2Output` and of `GoalAllocationOutput`. In `practical_asset_allocation/pipeline.py` add `GoalFunding,` to the `asset_allocation_pydantic.models` import and `goal_funding: Optional[GoalFunding] = None` as the last field of `PracticalAllocationOutput`.

- [ ] **Step 4: Step 2 carves the waterfall's target** — replace `run` (keep the comment above `goals_allocated`):

```python
def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output:
    goals_allocated = [
        g for g in inp.goals
        if g.time_to_goal_months < HORIZON_BOUNDARY_MONTHS + inp.months_to_fy_end
    ]
    subgroup = _route(inp.effective_tax_rate, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD)
    funding = goal_waterfall(
        goals_allocated, inp.short_term_holdings, inp.monthly_sip, remaining_corpus, subgroup,
    )

    total_goal_amount = round_to_100(sum(_future_value(g) for g in goals_allocated))
    allocated_amount = funding.allocated_amount
    new_remaining = remaining_corpus - allocated_amount

    subgroup_amounts: dict[str, int] = {}
    if allocated_amount > 0:
        subgroup_amounts[subgroup] = allocated_amount

    future_investment: FutureInvestment | None = None
    if funding.shortfall > 0:
        negotiable = [
            g.goal_name for g in goals_allocated if g.goal_priority == "negotiable"
        ]
        negotiable_str = ", ".join(negotiable) if negotiable else "none flagged"
        msg = (
            f"Your short-term goals ask for a bit more than your current corpus "
            f"alone. The remaining amount is wealth to create through your "
            f"monthly investments before these goals come due — stepping up "
            f"your SIPs (or flexing negotiable goals like {negotiable_str}) "
            f"makes each one comfortably reachable."
        )
        future_investment = FutureInvestment(
            bucket="short_term",
            future_investment_amount=funding.shortfall,
            message=msg,
        )

    return Step2Output(
        goals_allocated=goals_allocated,
        asset_subgroup=subgroup,
        total_goal_amount=total_goal_amount,
        allocated_amount=allocated_amount,
        remaining_corpus=new_remaining,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
        goal_funding=funding,
    )
```

The message text is unchanged on purpose; its wording for SIP customers is in the chat spec.

- [ ] **Step 5: Surface it** — `step7_presentation.py` `run`: add `goal_funding=step2.goal_funding,` to `GoalAllocationOutput(...)`. `practical_asset_allocation/pipeline.py` `_build_output`: add `goal_funding=s2.goal_funding,` to `PracticalAllocationOutput(...)`.

- [ ] **Step 6: Run the new tests**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_goal_waterfall_step2.py AI_Agents/tests/test_goal_waterfall.py -q`
Expected: 15 passed.

- [ ] **Step 7: Re-pin the goldens; check the diff**

```bash
rm AI_Agents/tests/fixtures/golden_practical_no_pref.json AI_Agents/tests/fixtures/golden_ideal_no_pref.json
.venv-mac/bin/python -m pytest -q AI_Agents/tests/test_human_override_golden.py
git diff AI_Agents/tests/fixtures/ | grep '^[+-]' | grep -v '^+++\|^---'
```

Expected: only added lines, all inside a new `"goal_funding": {...}` block. Any removed line is a bug — stop.

- [ ] **Step 8: Engine suites**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors AI_Agents/tests AI_Agents/src/asset_allocation_pydantic AI_Agents/src/practical_asset_allocation AI_Agents/src/Rebalancing
```

Expected: no FAILED/ERROR node that is not in `$SCRATCH/baseline_targeted.txt`.

- [ ] **Step 9: Checkpoint (no commit)**

---

### Task 3: Public goal future value in the cashflow engine

**Files:**
- Modify: `AI_Agents/src/cashflow_statement/engine/goals_table.py`, `engine/__init__.py`, `cashflow_statement/__init__.py`
- Modify: `app/domains/cashflow/services/goal_planning_engine/input_builder.py`
- Test: `AI_Agents/tests/test_custom_goal_fv.py`

**Interfaces:**
- Produces: `cashflow_statement.custom_goal_fv(goal: CustomGoal, assumptions: Assumptions, as_of: date) -> float`; `goal_planning_engine.input_builder.map_custom_goal(g: Any) -> CustomGoal` (callers filter status and date).

- [ ] **Step 1: Write the failing test** — `AI_Agents/tests/test_custom_goal_fv.py`:

```python
from datetime import date

from cashflow_statement import Assumptions, CustomGoal, GoalType, custom_goal_fv
from cashflow_statement.engine.dates import _round_thousand
from financial_primitives.inflation import inflate

TODAY = date(2026, 9, 28)
YEARS = (date(2028, 3, 31) - TODAY).days / 365


def _goal(**kw):
    base = dict(name="Car", goal_type=GoalType.custom, goal_value_pv=500_000.0,
                goal_date=date(2028, 3, 15))
    base.update(kw)
    return CustomGoal(**base)


def test_inflates_to_end_of_goal_month_at_the_type_default_and_rounds():
    a = Assumptions()
    assert custom_goal_fv(_goal(), a, TODAY) == _round_thousand(
        inflate(500_000.0, a.inflation_household_expense, YEARS)
    )


def test_per_goal_override_wins():
    assert custom_goal_fv(_goal(inflation_rate_override=0.10), Assumptions(), TODAY) == (
        _round_thousand(inflate(500_000.0, 0.10, YEARS))
    )


def test_property_goal_uses_property_inflation():
    a = Assumptions(inflation_property=0.09)
    assert custom_goal_fv(_goal(goal_type=GoalType.property), a, TODAY) == _round_thousand(
        inflate(500_000.0, 0.09, YEARS)
    )


def test_given_future_value_is_returned_as_is():
    assert custom_goal_fv(_goal(goal_value_fv=777_000.0), Assumptions(), TODAY) == 777_000.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_custom_goal_fv.py -q`
Expected: `ImportError: cannot import name 'custom_goal_fv'`.

- [ ] **Step 3: Helpers, used by the goals table** — `goals_table.py`, after `_INFLATION_BY_GOAL_TYPE`:

```python
def _goal_inflation(goal: CustomGoal, assumptions: Assumptions) -> float:
    if goal.inflation_rate_override is not None:
        return goal.inflation_rate_override
    return getattr(
        assumptions,
        _INFLATION_BY_GOAL_TYPE.get(goal.goal_type, "inflation_household_expense"),
    )


def custom_goal_fv(goal: CustomGoal, assumptions: Assumptions, as_of: date) -> float:
    """Cost at EOMONTH(goal_date), rounded to ₹1,000 — the figure the plan funds."""
    if goal.goal_value_fv is not None:
        return goal.goal_value_fv
    years = (eomonth(goal.goal_date, 0) - as_of).days / 365
    return _round_thousand(inflate(goal.goal_value_pv, _goal_inflation(goal, assumptions), years))
```

In `build_goals_table` section "# 3. Custom goals", keep `inflation_years = …` and the explanatory comments, and replace the `inflation = (...)` expression and the `if g.goal_value_fv is not None: … else: …` block with:

```python
        inflation = _goal_inflation(g, assumptions)
        corpus_required_fv = custom_goal_fv(g, assumptions, ctx.latest_update_date)
        if g.goal_value_fv is not None and g.goal_value_pv is None:
            goal_value_pv = g.goal_value_fv / (1 + inflation) ** inflation_years
        else:
            goal_value_pv = g.goal_value_pv
```

(Same results as before for all three input shapes.)

- [ ] **Step 4: Export** — `engine/__init__.py`:

```python
from .goals_table import custom_goal_fv
from .pipeline import compute_full_projection, validate_input_only, ENGINE_VERSION

__all__ = ["compute_full_projection", "validate_input_only", "ENGINE_VERSION", "custom_goal_fv"]
```

Package `__init__.py`: line 7 becomes `from .engine import compute_full_projection, validate_input_only, ENGINE_VERSION, custom_goal_fv`; add `"custom_goal_fv",` to `__all__`.

- [ ] **Step 5: `map_custom_goal` in the cashflow input builder** — add above `_map_custom_goals`:

```python
def map_custom_goal(g: Any) -> CustomGoal:
    """Engine goal for one ORM goal; callers filter status and date."""
    gt = getattr(g, "goal_type", None)
    gt_name = (gt.value if hasattr(gt, "value") else str(gt or "")).upper()
    inflation_override = None
    infl = getattr(g, "inflation_rate", None)
    if infl is not None:
        try:
            inflation_override = float(infl) / 100.0 if float(infl) > 1 else float(infl)
        except (TypeError, ValueError):
            pass
    return CustomGoal(
        name=getattr(g, "name", None) or getattr(g, "goal_name", None) or "goal",
        goal_type=_ORM_GOAL_TYPE_TO_ENGINE.get(gt_name, GoalType.custom),
        goal_value_pv=float(
            getattr(g, "goal_value_pv", None)
            or getattr(g, "present_value_amount", None)
            or 0.0
        ),
        goal_date=getattr(g, "target_date", None) or getattr(g, "goal_date", None),
        inflation_rate_override=inflation_override,
    )
```

In `_map_custom_goals`: delete the now-unused `gt = …` / `gt_name = …` lines; replace everything after `seen_names.add(norm)` (the retirement comment may stay) through the `mapped.append(CustomGoal(...))` call with:

```python
        goal = map_custom_goal(g)
        if goal.goal_type == GoalType.property:
            issues.append(
                f"goal:{goal_name} (HOME_PURCHASE) modeled as a cash goal — "
                "downpayment and mortgage data are not yet captured on the profile"
            )
        mapped.append(goal)
```

- [ ] **Step 6: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors AI_Agents/tests/test_custom_goal_fv.py app/domains/cashflow AI_Agents/src/cashflow_statement
```

Expected: 4 new tests pass; no FAILED/ERROR outside the baseline.

- [ ] **Step 7: Checkpoint (no commit)**

---

### Task 4: The allocation input builder fills future value, stated SIP and zero holdings

**Files:**
- Modify: `app/domains/asset_allocation/services/aa_engine/input_builder.py`
- Test: `app/domains/asset_allocation/services/aa_engine/tests/test_input_builder.py` (append)

**Interfaces:**
- Consumes: `map_custom_goal`, `custom_goal_fv` (Task 3).
- Produces: every app `AllocationInput` carries `amount_needed_fv`, `monthly_sip` (from `starting_monthly_investment`, missing → 0.0) and `short_term_holdings=0.0` (the ideal starts from nothing; Task 5 sets the practical value).

- [ ] **Step 1: Write the failing tests** — append to `test_input_builder.py` (add `from types import SimpleNamespace` to the imports):

```python
class GoalFundingInputTests(unittest.TestCase):
    """Goal future value, the stated SIP and zero holdings reach AllocationInput."""

    _build_minimal_user = ChatOverrideTests._build_minimal_user
    _make_ctx = ChatOverrideTests._make_ctx

    @staticmethod
    def _goal():
        today = date.today()
        return SimpleNamespace(
            status=SimpleNamespace(value="ACTIVE"),
            target_date=date(today.year + 1, today.month, 1), goal_date=None,
            goal_type=None, name="Car", goal_name="Car",
            present_value_amount=500_000.0, goal_value_pv=500_000.0, inflation_rate=None,
        )

    def test_goal_future_value_matches_the_cashflow_engine(self):
        from app.domains.cashflow.services.goal_planning_engine.input_builder import (
            map_custom_goal,
        )
        from cashflow_statement import Assumptions, custom_goal_fv

        goal = self._goal()
        user = self._build_minimal_user()
        user.financial_goals = [goal]
        alloc_input, _ = build_goal_allocation_input_for_user(self._make_ctx(user))
        expected = custom_goal_fv(map_custom_goal(goal), Assumptions(), date.today())
        self.assertEqual(alloc_input.goals[0].amount_needed, 500_000.0)
        self.assertEqual(alloc_input.goals[0].amount_needed_fv, expected)
        self.assertGreater(expected, 500_000.0)

    def test_stated_sip_and_zero_holdings(self):
        user = self._build_minimal_user()
        user.personal_finance_profile.starting_monthly_investment = 25_000.0
        alloc_input, _ = build_goal_allocation_input_for_user(self._make_ctx(user))
        self.assertEqual(alloc_input.monthly_sip, 25_000.0)
        self.assertEqual(alloc_input.short_term_holdings, 0.0)

    def test_missing_sip_is_zero(self):
        user = self._build_minimal_user()
        alloc_input, _ = build_goal_allocation_input_for_user(self._make_ctx(user))
        self.assertEqual(alloc_input.monthly_sip, 0.0)
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/asset_allocation/services/aa_engine/tests/test_input_builder.py -q -k GoalFunding`
Expected: 3 failed.

- [ ] **Step 3: Implement** — with the app imports at the top of `input_builder.py`:

```python
from app.domains.cashflow.services.goal_planning_engine.input_builder import map_custom_goal
```

next to `from asset_allocation_pydantic.models import AllocationInput, Goal` (after `ensure_ai_agents_path()`):

```python
from cashflow_statement import Assumptions, custom_goal_fv
```

In `_map_goals`, add `assumptions = Assumptions()` after `today = date.today()`, and build each goal as:

```python
        fv = custom_goal_fv(map_custom_goal(g), assumptions, today)
        mapped.append(
            Goal(
                goal_name=getattr(g, "goal_name", None) or "goal",
                time_to_goal_months=_months_between(today, target),
                amount_needed=float(getattr(g, "present_value_amount", 0.0) or 0.0),
                amount_needed_fv=fv if fv > 0 else None,
                goal_priority="non_negotiable",
                investment_goal=gt_val.lower(),
            )
        )
```

In `build_goal_allocation_input_for_user`, next to `monthly_household_expense = pf.monthly_household_expense_pfp(pfp)`:

```python
    monthly_sip = max(pf.starting_monthly_investment_pfp(pfp) or 0.0, 0.0)
```

and in `AllocationInput(...)`, after `months_to_fy_end=...`:

```python
        monthly_sip=monthly_sip,
        short_term_holdings=0.0,
```

- [ ] **Step 4: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors app/domains/asset_allocation
```

Expected: 3 new tests pass; no FAILED/ERROR outside the baseline.

- [ ] **Step 5: Checkpoint (no commit)**

---

### Task 5: Holdings helpers move to `portfolio`; the practical builder reads holdings and takes a SIP override

**Files:**
- Create: `app/domains/portfolio/services/holdings_snapshot.py`
- Modify: `app/domains/additional_investment/services/ainv_engine/holdings_snapshot.py`
- Modify: `app/domains/mutual_funds/services/scheme_classification.py`
- Modify: `app/domains/practical_asset_allocation/services/paa_engine/input_builder.py`, `service.py`
- Test: `app/domains/practical_asset_allocation/services/paa_engine/tests/test_input_builder.py` (append)

**Interfaces:**
- Produces:
  - `portfolio.services.holdings_snapshot`: `HoldingsSnapshot`, `aggregate_holdings`, `snapshot_from_holdings(holdings: Iterable[Any]) -> HoldingsSnapshot` (ainv's module re-exports all three and keeps `load_holdings_snapshot`)
  - `scheme_classification.SHORT_TERM_HOLDING_SUBGROUPS: frozenset[str]`, `short_term_holdings_total(by_subgroup: Mapping[str, float]) -> float`
  - `paa input_builder.short_term_holdings_for_user(user: Any) -> float | None`
  - `build_practical_allocation_input_for_user(ctx, corpus_pin=None, apply_saved_preferences=True, monthly_sip: float | None = None, short_term_holdings: float | None = None)` — an explicit `short_term_holdings` is used verbatim and the user's holdings are not read
  - `compute_practical_allocation_result(user, user_question, *, chat_ctx, corpus_pin=None, monthly_sip: float | None = None, short_term_holdings: float | None = None)`

- [ ] **Step 1: Write the failing tests** — append to `paa_engine/tests/test_input_builder.py`:

```python
def _holding(sub_category, value, scheme_name="X Fund"):
    return types.SimpleNamespace(
        instrument_type="mutual_fund", current_value=value, instrument_name=scheme_name,
        fund_metadata=types.SimpleNamespace(sub_category=sub_category, scheme_name=scheme_name),
    )


def test_short_term_holdings_count_debt_and_arbitrage_not_income_plus_arbitrage():
    user = types.SimpleNamespace(portfolios=[types.SimpleNamespace(holdings=[
        _holding("Liquid Fund", 300_000.0),
        _holding("Arbitrage Fund", 200_000.0),
        _holding("Gilt Fund", 100_000.0),
        _holding("Large Cap Fund", 900_000.0),
        _holding("FoF Domestic", 400_000.0, scheme_name="ICICI Income plus Arbitrage FoF"),
    ])])
    assert input_builder.short_term_holdings_for_user(user) == 600_000.0


def test_no_holdings_on_file_is_none():
    assert input_builder.short_term_holdings_for_user(types.SimpleNamespace(portfolios=[])) is None
    assert input_builder.short_term_holdings_for_user(None) is None


def test_builder_sets_holdings_and_the_sip_override(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(1_000_000.0), {}),
    )
    monkeypatch.setattr(input_builder, "short_term_holdings_for_user", lambda user: 250_000.0)
    input_builder.build_practical_allocation_input_for_user(None, monthly_sip=40_000.0)
    assert captured["short_term_holdings"] == 250_000.0
    assert captured["monthly_sip"] == 40_000.0


def test_builder_keeps_the_profile_sip_without_an_override(monkeypatch, captured):
    base = _fake_base(1_000_000.0)
    base.monthly_sip = 15_000.0
    monkeypatch.setattr(input_builder, "build_goal_allocation_input_for_user", lambda ctx: (base, {}))
    input_builder.build_practical_allocation_input_for_user(None)
    assert captured["monthly_sip"] == 15_000.0


def test_explicit_holdings_skip_the_user_read(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(1_000_000.0), {}),
    )

    def _boom(user):
        raise AssertionError("must not read the user's holdings")

    monkeypatch.setattr(input_builder, "short_term_holdings_for_user", _boom)
    input_builder.build_practical_allocation_input_for_user(None, short_term_holdings=123_000.0)
    assert captured["short_term_holdings"] == 123_000.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/practical_asset_allocation/services/paa_engine/tests/test_input_builder.py -q`
Expected: 5 new tests fail.

- [ ] **Step 3: Move the pure holdings code to `portfolio`** — create `app/domains/portfolio/services/holdings_snapshot.py`:

```python
"""Current-holdings snapshot aggregated to canonical asset subgroups.

Pure: classifies already-loaded holdings through the canonical
``classify_holding`` (the same vocabulary as the practical allocation's subgroup
rows), valued at ``PortfolioHolding.current_value``. Lives here, not in
additional_investment, because the practical-allocation builder — which
rebalancing imports — reads it too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.domains.mutual_funds.services.scheme_classification import classify_holding
```

then move, verbatim, from `ainv_engine/holdings_snapshot.py`: `_EQUITY_INSTRUMENT_TYPES` (with its comment), `_SUBGROUP_NON_MF_EQUITIES`, `_SUBGROUP_ELSS`, `class HoldingsSnapshot`, `def aggregate_holdings`. Append:

```python
def snapshot_from_holdings(holdings: Iterable[Any]) -> HoldingsSnapshot:
    """Classify already-loaded PortfolioHolding rows (fund metadata preloaded)."""
    return aggregate_holdings(
        [
            (
                h.instrument_type,
                float(h.current_value or 0.0),
                h.fund_metadata.sub_category if h.fund_metadata else None,
                h.fund_metadata.scheme_name if h.fund_metadata else h.instrument_name,
            )
            for h in holdings
        ]
    )
```

Replace `ainv_engine/holdings_snapshot.py` with:

```python
"""Load the current-holdings snapshot for the deficit-fill lumpsum path (spec 2026-07-03).

The snapshot model and its pure aggregation live in
``app.domains.portfolio.services.holdings_snapshot`` and are re-exported here.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding
from app.domains.portfolio.services.holdings_snapshot import (
    HoldingsSnapshot,
    aggregate_holdings,
    snapshot_from_holdings,
)

__all__ = [
    "HoldingsSnapshot",
    "aggregate_holdings",
    "load_holdings_snapshot",
    "snapshot_from_holdings",
]


async def load_holdings_snapshot(
    db: AsyncSession, user_id: uuid.UUID
) -> HoldingsSnapshot:
    """Load + classify the user's holdings across their portfolios.

    ``fund_metadata`` joins on scheme_code (see the PortfolioHolding
    relationship); rows without metadata fall back to the instrument name so
    name-based classification overrides still get a chance."""
    stmt = (
        select(PortfolioHolding)
        .join(Portfolio, PortfolioHolding.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user_id)
        .options(selectinload(PortfolioHolding.fund_metadata))
    )
    holdings = (await db.execute(stmt)).scalars().all()
    return snapshot_from_holdings(holdings)
```

- [ ] **Step 4: Classification rule** — `scheme_classification.py`: change `from typing import Optional` to `from typing import Mapping, Optional`; after `SUBGROUP_TO_ASSET_CLASS = _build_subgroup_to_asset_class()` add:

```python
# Held funds whose value counts toward short-term goals: every debt or arbitrage
# fund except the income-plus-arbitrage FoF, which is long-term debt.
SHORT_TERM_HOLDING_SUBGROUPS: frozenset[str] = frozenset(
    sg for sg, ac in SUBGROUP_TO_ASSET_CLASS.items() if ac == ASSET_CLASS_DEBT
) - {"arbitrage_plus_income"}


def short_term_holdings_total(by_subgroup: Mapping[str, float]) -> float:
    return float(
        sum(v for sg, v in by_subgroup.items() if v > 0 and sg in SHORT_TERM_HOLDING_SUBGROUPS)
    )
```

- [ ] **Step 5: The practical builder** — `paa_engine/input_builder.py` app imports:

```python
from app.domains.mutual_funds.services.scheme_classification import short_term_holdings_total
from app.domains.portfolio.services.holdings_snapshot import snapshot_from_holdings
```

Above `build_practical_allocation_input_for_user`:

```python
def short_term_holdings_for_user(user: Any) -> float | None:
    """Held short-term money from the user's preloaded holdings; None when they
    have no holdings on file."""
    holdings = [
        h
        for p in (getattr(user, "portfolios", None) or [])
        for h in (getattr(p, "holdings", None) or [])
    ]
    snapshot = snapshot_from_holdings(holdings)
    if snapshot.total_inr <= 0:
        return None
    return short_term_holdings_total(snapshot.by_subgroup)
```

Add `monthly_sip: float | None = None,` and `short_term_holdings: float | None = None,` after `apply_saved_preferences`; after the `if corpus_pin is not None:` block:

```python
    shared["short_term_holdings"] = (
        short_term_holdings
        if short_term_holdings is not None
        else short_term_holdings_for_user(getattr(ctx, "user_ctx", None))
    )
    if monthly_sip is not None:
        shared["monthly_sip"] = monthly_sip
```

Add `"short_term_holdings": practical_input.short_term_holdings,` and `"monthly_sip": practical_input.monthly_sip,` to `debug`. In the module docstring's "New scalars" list, add a line: `short_term_holdings  = held debt + arbitrage from the preloaded user (None = no holdings); a caller may pass its own`.

- [ ] **Step 6: The practical service** — `compute_practical_allocation_result` gains `monthly_sip: float | None = None,` and `short_term_holdings: float | None = None,` after `corpus_pin`, passed through to the builder.

- [ ] **Step 7: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors app/domains/practical_asset_allocation app/domains/additional_investment app/domains/profile app/domains/mutual_funds app/domains/portfolio app/domains/rebalancing
```

Expected: 5 new tests pass; no FAILED/ERROR outside the baseline (`test_contract_single_computation_reader` is pre-existing). `User.portfolios` and `Portfolio.holdings` lazy-load: production users arrive preloaded (`user_context_loader.py`), but a DB-fixture test whose user was not loaded that way fails with `MissingGreenlet` inside `short_term_holdings_for_user`. Fix such a test by loading its user with `selectinload(User.portfolios).selectinload(Portfolio.holdings).selectinload(PortfolioHolding.fund_metadata)` — never with a try/except in the helper.

- [ ] **Step 8: Checkpoint (no commit)**

---

### Task 6: Rebalancing passes held short-term money from its ledger rows

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/input_builder.py`
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_input_builder.py` (append)

**Interfaces:**
- Consumes: `short_term_holdings_total`, the builder's `short_term_holdings=` (Task 5).
- Produces: `_short_term_holdings_from_rows(rows) -> float`; `request.practical_allocation_input.short_term_holdings` from the rows the rebalancer trades, without reading the user's holdings.

- [ ] **Step 1: Write the failing tests**

```python
def test_short_term_holdings_from_rows_count_held_debt_only():
    from types import SimpleNamespace

    from app.domains.rebalancing.services.rebal_engine.input_builder import (
        _short_term_holdings_from_rows,
    )

    rows = [
        SimpleNamespace(asset_subgroup="near_debt", present_allocation_inr=Decimal("300000")),
        SimpleNamespace(asset_subgroup="arbitrage", present_allocation_inr=Decimal("200000")),
        SimpleNamespace(asset_subgroup="arbitrage_plus_income", present_allocation_inr=Decimal("500000")),
        SimpleNamespace(asset_subgroup="low_beta_equities", present_allocation_inr=Decimal("900000")),
        SimpleNamespace(asset_subgroup="short_debt", present_allocation_inr=Decimal("0")),
    ]
    assert _short_term_holdings_from_rows(rows) == 500_000.0


@pytest.mark.asyncio
async def test_practical_input_takes_short_term_holdings_from_rows(
    monkeypatch,
    db_session,
    fixture_user_with_two_holdings,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    from app.domains.practical_asset_allocation.services.paa_engine import (
        input_builder as paa_ib,
    )
    from app.domains.rebalancing.services.rebal_engine.input_builder import (
        build_rebalancing_input_for_user,
    )

    def _boom(user):
        raise AssertionError("rebalancing must value holdings from its ledger rows")

    monkeypatch.setattr(paa_ib, "short_term_holdings_for_user", _boom)
    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_two_holdings, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    assert request.practical_allocation_input.short_term_holdings == 0.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_input_builder.py -q`
Expected: 2 new tests fail (import error; `AssertionError: rebalancing must value holdings from its ledger rows`).

- [ ] **Step 3: Implement** — import `short_term_holdings_total` alongside `classify_holding`; add:

```python
def _short_term_holdings_from_rows(rows: list[FundRowInput]) -> float:
    by_subgroup: dict[str, float] = {}
    for r in rows:
        if r.present_allocation_inr > 0:
            by_subgroup[r.asset_subgroup] = by_subgroup.get(r.asset_subgroup, 0.0) + float(
                r.present_allocation_inr
            )
    return short_term_holdings_total(by_subgroup)
```

In step 7b change the builder call to (the `model_copy` below it stays):

```python
    practical_input, _paa_debug = build_practical_allocation_input_for_user(
        ctx, short_term_holdings=_short_term_holdings_from_rows(rows)
    )
```

- [ ] **Step 4: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors app/domains/rebalancing
```

Expected: both new tests pass; no FAILED/ERROR outside the baseline.

- [ ] **Step 5: Checkpoint (no commit)**

---

### Task 7: Never swap one held debt fund for another

**Files:**
- Modify: `AI_Agents/src/Rebalancing/tables.py`, `steps/step2b_suppress_debt_switch.py`, `models.py` (`FundRowInput` docstring), `config.py`
- Modify (gitignored, local): `AI_Agents/src/Rebalancing/Testing/test_step2b_debt_netting.py`
- Test: `AI_Agents/tests/test_debt_netting_keeps_held_debt.py`, `app/domains/rebalancing/tests/test_debt_netting_pool_parity.py`

**Interfaces:**
- Produces: `DEBT_NETTING_POOL` = every Debt-class subgroup; off-list (rank 0) debt sells are nettable; force-exits still exit.

- [ ] **Step 1: Write the failing tests** — `AI_Agents/tests/test_debt_netting_keeps_held_debt.py`:

```python
"""A held debt fund is never sold to buy another debt fund; debt with no matching
debt buy still sells, and force-exits still exit."""

from __future__ import annotations

from decimal import Decimal

from practical_asset_allocation.pipeline import PracticalAllocationInput
from Rebalancing.config import FORCE_EXIT_RANK
from Rebalancing.models import FundRowInput, RebalancingComputeRequest
from Rebalancing.steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step2b_suppress_debt_switch,
)

CORPUS = Decimal("10000000")


def _row(isin, subgroup, rank, target, present, lt=None, **kw):
    lt = present if lt is None else lt
    return FundRowInput(
        asset_subgroup=subgroup, sub_category=kw.pop("sub_category", "Liquid Fund"),
        recommended_fund=f"Fund {isin}", isin=isin, rank=rank,
        target_amount_pre_cap=Decimal(target), present_allocation_inr=Decimal(present),
        invested_cost_inr=Decimal(present) * Decimal("0.85"),
        lt_value_inr=Decimal(lt), lt_cost_inr=Decimal(lt) * Decimal("0.85"),
        current_nav=Decimal("100"), fund_rating=8, **kw,
    )


def _step2b(rows):
    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, shortfall_amount=0.0,
        total_corpus=float(CORPUS), monthly_household_expense=100_000,
        effective_tax_rate=15.0, net_financial_assets=float(CORPUS), goals=[],
        mf_corpus=float(CORPUS), non_mf_equity_corpus=0, elss_corpus=0,
    )
    req = RebalancingComputeRequest(
        practical_allocation_input=inp, tax_regime="new", effective_tax_rate_pct=30.0, rows=rows,
    )
    s1, _, _ = step1_cap_and_spill.apply(req.rows, req)
    s2, _ = step2_compare_and_decide.apply(s1, req)
    out, _ = step2b_suppress_debt_switch.apply(s2, req)
    return {r.isin: r for r in out}


def test_off_list_liquid_fund_is_kept_against_a_debt_buy():
    by_isin = _step2b([
        _row("LIQ", "near_debt", 0, "400000", "1000000", lt="600000", is_recommended=False),
        _row("ARB", "arbitrage", 1, "800000", "0", sub_category="Arbitrage Fund"),
    ])
    assert by_isin["LIQ"].diff == Decimal(0)
    assert by_isin["LIQ"].worth_to_change is False
    assert by_isin["ARB"].diff == Decimal("200000")


def test_off_list_debt_with_no_debt_buy_still_sells():
    by_isin = _step2b([
        _row("LIQ", "near_debt", 0, "400000", "1000000", lt="600000", is_recommended=False),
        _row("EQ", "low_beta_equities", 1, "600000", "0", sub_category="Large Cap Fund"),
    ])
    assert by_isin["LIQ"].diff == Decimal("-600000")
    assert by_isin["LIQ"].worth_to_change is True


def test_force_exit_debt_still_exits():
    by_isin = _step2b([
        _row("BAD", "short_debt", FORCE_EXIT_RANK, "0", "500000", is_recommended=False),
        _row("ARB", "arbitrage", 1, "800000", "0", sub_category="Arbitrage Fund"),
    ])
    assert by_isin["BAD"].exit_flag is True
    assert by_isin["BAD"].diff == Decimal("-500000")
```

`app/domains/rebalancing/tests/test_debt_netting_pool_parity.py`:

```python
"""The engine's debt-netting pool and the app's short-term holding set are both
derived from the classifier's Debt subgroups; they must not drift."""


def test_netting_pool_is_every_debt_subgroup():
    from app.domains.ai_engine.common import ensure_ai_agents_path
    from app.domains.mutual_funds.services.scheme_classification import (
        ASSET_CLASS_DEBT,
        SHORT_TERM_HOLDING_SUBGROUPS,
        SUBGROUP_TO_ASSET_CLASS,
    )

    ensure_ai_agents_path()
    from Rebalancing.tables import DEBT_NETTING_POOL

    debt = {sg for sg, ac in SUBGROUP_TO_ASSET_CLASS.items() if ac == ASSET_CLASS_DEBT}
    assert set(DEBT_NETTING_POOL) == debt
    assert SHORT_TERM_HOLDING_SUBGROUPS == DEBT_NETTING_POOL - {"arbitrage_plus_income"}
```

- [ ] **Step 2: Run to verify**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_debt_netting_keeps_held_debt.py app/domains/rebalancing/tests/test_debt_netting_pool_parity.py -q`
Expected: `test_off_list_liquid_fund_is_kept_against_a_debt_buy` and `test_netting_pool_is_every_debt_subgroup` fail; the other two pass.

- [ ] **Step 3: Widen the pool** — `Rebalancing/tables.py`, keep the comment above and add one line to it (`# Must equal every Debt-class subgroup the app's classifier produces (app/domains/rebalancing/tests/test_debt_netting_pool_parity.py).`); replace the set:

```python
DEBT_NETTING_POOL: frozenset[str] = frozenset(
    {
        "arbitrage",
        "arbitrage_plus_income",
        "debt_subgroup",
        "floating_debt",
        "high_risk_debt",
        "long_duration_debt",
        "medium_debt",
        "near_debt",
        "other_debt",
        "sector_debt",
        "short_debt",
    }
)
```

- [ ] **Step 4: Off-list debt sells are nettable** — `step2b_suppress_debt_switch.py`, replace the eligibility comment and `sells`:

```python
    # Force-exits are never netted: a bad fund is still a bad fund. Off-list
    # (rank 0) debt IS netted — a held debt fund is not sold to buy another.
    sells = [
        r
        for r in debt
        if r.worth_to_change and r.diff < 0 and not r.exit_flag
    ]
```

- [ ] **Step 5: Docstring and version** — `Rebalancing/models.py` `FundRowInput` docstring, NEUTRAL bullet: append `In a debt subgroup, step2b nets that sell against debt buys, so a held off-list debt fund is kept.` `config.py`: append to the changelog block above `ENGINE_VERSION`:

```python
# 1.14.0: SIP-first goal waterfall (spec 2026-09-28). The short-term target comes
#         from step 2's goal waterfall, and debt-switch netting covers every debt
#         subgroup including off-list holdings — a held debt fund is never sold
#         to buy another.
```

and set `ENGINE_VERSION: str = "1.14.0"`.

- [ ] **Step 6: Flip the local test that pinned the old carve-out** — in `AI_Agents/src/Rebalancing/Testing/test_step2b_debt_netting.py` replace `test_neutral_off_list_debt_sell_is_never_netted` with:

```python
def test_neutral_off_list_debt_sell_is_netted():
    """A held off-list debt fund is kept rather than switched into the
    recommended debt fund (spec 2026-09-28)."""
    rows = [
        _debt_row(
            "NEUT", "short_debt", 0, "400000", "1000000",
            is_recommended=False, lt_value_inr="600000",
        ),
        _debt_row("ARB", "arbitrage", 1, "800000", "0"),
    ]
    by_isin = _run_to_step2b(rows)

    assert by_isin["NEUT"].diff == Decimal(0)
    assert by_isin["NEUT"].worth_to_change is False
    assert by_isin["ARB"].diff == Decimal("200000")
```

- [ ] **Step 7: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors AI_Agents/tests/test_debt_netting_keeps_held_debt.py app/domains/rebalancing AI_Agents/src/Rebalancing
```

Expected: new tests pass. Any other new FAILED node that asserts a rank-0 debt row being sold against a debt buy is testing the old rule — update it as in Step 6 and note it at the checkpoint.

- [ ] **Step 8: Checkpoint (no commit)**

---

### Task 8: Fresh-money engine — goal money first is the only split

**Files:**
- Modify: `AI_Agents/src/additional_investment/models.py`, `ratio.py`, `pipeline.py`
- Modify (tracked tests): `AI_Agents/tests/test_preference_propagation_e2e.py` (lines 109-110, 170-171)
- Modify (gitignored, local): `AI_Agents/src/additional_investment/Testing/*`
- Test: `AI_Agents/tests/test_ainv_goal_share.py`

**Interfaces:**
- Produces:
  - `AdditionalInvestmentInput.goal_share_inr: float = 0.0`, `goal_subgroup: Optional[str] = None`; `short_term_fulfilled` / `medium_term_fulfilled` deleted
  - `ratio.compute_long_term_targets(subgroups, deploy_amount, exclude_subgroups=frozenset()) -> list[SubgroupTarget]`
  - `ratio.compute_goal_first_targets(subgroups, deploy_amount, goal_share, goal_subgroup, exclude_subgroups=frozenset(), current_by_subgroup=None) -> tuple[TargetBucket, list[SubgroupTarget]]`
  - Deleted: `select_target_bucket`, `_bucket_weight`, `compute_targets`, `dominant_bucket`, the legacy branches in `run_additional_investment`

- [ ] **Step 1: Write the failing tests** — `AI_Agents/tests/test_ainv_goal_share.py`:

```python
"""Fresh money: goal money first, then the long-term plan."""

from __future__ import annotations

from additional_investment.models import (
    AdditionalInvestmentInput,
    Cadence,
    SubgroupBucketAmounts,
    TargetBucket,
)
from additional_investment.pipeline import run_additional_investment


def _row(sg, short=0.0, long=0.0):
    return SubgroupBucketAmounts(subgroup=sg, short_term=short, long_term=long, total=short + long)


def _inp(rows, deploy, cadence=Cadence.SIP_MONTHLY, **kw):
    return AdditionalInvestmentInput(
        deploy_amount_inr=deploy, cadence=cadence, subgroups=rows, ranked_funds=[], **kw
    )


def _targets(out):
    return {t.subgroup: round(t.target_inr) for t in out.per_subgroup_target}


def test_sip_goal_share_goes_to_the_named_subgroup_even_with_no_short_term_row():
    rows = [_row("low_beta_equities", long=600_000), _row("medium_beta_equities", long=400_000)]
    out = run_additional_investment(_inp(rows, 50_000, goal_share_inr=30_000, goal_subgroup="arbitrage"))
    assert _targets(out) == {"arbitrage": 30_000, "low_beta_equities": 12_000, "medium_beta_equities": 8_000}
    assert out.target_bucket is TargetBucket.SHORT_TERM


def test_sip_without_a_goal_share_follows_the_long_term_plan():
    rows = [_row("arbitrage", short=500_000), _row("low_beta_equities", long=750_000),
            _row("medium_beta_equities", long=250_000)]
    out = run_additional_investment(_inp(rows, 20_000))
    assert _targets(out) == {"low_beta_equities": 15_000, "medium_beta_equities": 5_000}
    assert out.target_bucket is TargetBucket.LONG_TERM


def test_goal_subgroup_also_in_the_long_term_plan_gets_one_target():
    rows = [_row("short_debt", short=100_000, long=100_000), _row("low_beta_equities", long=300_000)]
    out = run_additional_investment(_inp(rows, 10_000, goal_share_inr=4_000, goal_subgroup="short_debt"))
    assert _targets(out) == {"short_debt": 5_500, "low_beta_equities": 4_500}


def test_lumpsum_goal_share_first_then_long_term_deficits_only():
    rows = [_row("arbitrage", short=200_000), _row("low_beta_equities", long=600_000)]
    out = run_additional_investment(_inp(
        rows, 300_000, cadence=Cadence.LUMPSUM,
        current_value_by_subgroup={"low_beta_equities": 400_000},
        goal_share_inr=100_000, goal_subgroup="arbitrage",
    ))
    assert _targets(out) == {"arbitrage": 100_000, "low_beta_equities": 200_000}
    assert out.target_bucket is TargetBucket.LONG_TERM


def test_goal_share_is_capped_at_the_deploy_amount():
    rows = [_row("low_beta_equities", long=1_000_000)]
    out = run_additional_investment(_inp(rows, 10_000, goal_share_inr=25_000, goal_subgroup="arbitrage"))
    assert _targets(out) == {"arbitrage": 10_000}
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_ainv_goal_share.py -q`
Expected: assertion failures (the model ignores unknown kwargs — no `extra="forbid"` — so the legacy split runs).

- [ ] **Step 3: Models** — `models.py`: delete `short_term_fulfilled`, `medium_term_fulfilled` and their comment block; in their place:

```python
    # Money for short-term goals, deployed first into goal_subgroup by name; the
    # rest follows the long-term plan (SIP) or the long-term deficits (lumpsum).
    goal_share_inr: float = Field(default=0.0, ge=0)
    goal_subgroup: Optional[str] = None
```

`TargetBucket` docstring becomes `"""Horizon that receives most of the deposit — a label, not the split driver."""`.

- [ ] **Step 4: `ratio.py`** — replace the module docstring with:

```python
"""Subgroup splits for additional investment. Pure, no state, no I/O.

Goal money first: the goal share goes to the routed short-term subgroup by name.
The rest follows the long-term column (SIP), or — with a holdings map (lumpsum)
— the long-term deficits: each row's total minus its short-term column, against
current holdings that exclude short-term money.
"""
```

Delete `select_target_bucket`, `_bucket_weight`, `compute_targets` and `dominant_bucket`. Keep `compute_deficit_targets` unchanged. Add:

```python
def compute_long_term_targets(
    subgroups: list[SubgroupBucketAmounts],
    deploy_amount: float,
    exclude_subgroups: set[str] = frozenset(),
) -> list[SubgroupTarget]:
    """Split by each eligible subgroup's long-term column, renormalised."""
    weights = {
        r.subgroup: 0.0 if r.subgroup in exclude_subgroups else max(r.long_term, 0.0)
        for r in subgroups
    }
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return []
    return [
        SubgroupTarget(
            subgroup=r.subgroup,
            ratio=weights[r.subgroup] / total_weight,
            target_inr=weights[r.subgroup] / total_weight * deploy_amount,
        )
        for r in subgroups
        if weights[r.subgroup] > 0
    ]


def compute_goal_first_targets(
    subgroups: list[SubgroupBucketAmounts],
    deploy_amount: float,
    goal_share: float,
    goal_subgroup: str | None,
    exclude_subgroups: set[str] = frozenset(),
    current_by_subgroup: dict[str, float] | None = None,
) -> tuple[TargetBucket, list[SubgroupTarget]]:
    """Goal money first, into goal_subgroup by name — never weighted by the
    short-term column, which is empty when the plan holds no short-term money."""
    goal = min(goal_share, deploy_amount) if goal_subgroup else 0.0
    rest = deploy_amount - goal
    rest_targets: list[SubgroupTarget] = []
    if rest > 0:
        if current_by_subgroup is None:
            rest_targets = compute_long_term_targets(subgroups, rest, exclude_subgroups)
        else:
            long_term_rows = [
                r.model_copy(update={"total": max(0.0, r.total - r.short_term), "short_term": 0.0})
                for r in subgroups
            ]
            rest_targets = compute_deficit_targets(
                long_term_rows, current_by_subgroup, rest, exclude_subgroups
            )
    amounts: dict[str, float] = {}
    if goal > 0:
        amounts[goal_subgroup] = goal
    for t in rest_targets:
        amounts[t.subgroup] = amounts.get(t.subgroup, 0.0) + t.target_inr
    targets = [
        SubgroupTarget(subgroup=sg, ratio=amt / deploy_amount, target_inr=amt)
        for sg, amt in amounts.items()
    ]
    bucket = TargetBucket.SHORT_TERM if goal * 2 >= deploy_amount else TargetBucket.LONG_TERM
    return bucket, targets
```

- [ ] **Step 5: `pipeline.py`** — import only `compute_goal_first_targets` from `.ratio`; in `run_additional_investment` replace the whole `if … else …` split selection and its comment with:

```python
    bucket, targets = compute_goal_first_targets(
        inp.subgroups,
        inp.deploy_amount_inr,
        inp.goal_share_inr,
        inp.goal_subgroup,
        inp.exclude_subgroups,
        inp.current_value_by_subgroup if inp.cadence is Cadence.LUMPSUM else None,
    )
```

- [ ] **Step 6: Remove the deleted fields from tests** — delete the `short_term_fulfilled=True,` / `medium_term_fulfilled=True,` kwargs at `AI_Agents/tests/test_preference_propagation_e2e.py:109-110,170-171`. In gitignored `AI_Agents/src/additional_investment/Testing/`, delete tests that exercise `select_target_bucket`, `compute_targets`, `dominant_bucket` or the flags, and drop the flag kwargs elsewhere.

- [ ] **Step 7: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors AI_Agents/tests AI_Agents/src/additional_investment
grep -rn "short_term_fulfilled\|medium_term_fulfilled\|select_target_bucket\|dominant_bucket\|compute_targets(" AI_Agents/src AI_Agents/tests --include="*.py"
```

Expected: 5 new tests pass; no FAILED/ERROR outside the baseline; the grep prints nothing (the app side is Task 9).

- [ ] **Step 8: Checkpoint (no commit)**

---

### Task 9: SIP and lumpsum flows — goal share, long-term holdings, facts, rescue; profile gate removed

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/input_builder.py`, `service.py`
- Modify: `app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py`, `test_service.py`, `test_persist.py`, `test_persist_roundtrip.py`

**Interfaces:**
- Consumes: `goal_funding` (Task 2), `compute_practical_allocation_result(..., monthly_sip=, short_term_holdings=)` (Task 5), `SHORT_TERM_HOLDING_SUBGROUPS` / `short_term_holdings_total` (Task 5), the engine's `goal_share_inr` / `goal_subgroup` (Task 8).
- Produces:
  - `input_builder.goal_share_for(allocation_output: Any, cadence: Cadence, deploy_amount_inr: float) -> tuple[float, str | None]`
  - `build_additional_investment_input_for_user(allocation_output, *, deploy_amount_inr, cadence, current_value_by_subgroup=None, investable_corpus_inr=0.0, goal_share_inr: float = 0.0, goal_subgroup: str | None = None)` — the unused `ctx` parameter is removed; it stays `async` so its callers and `AsyncMock`s are unchanged
  - `AINV_ENGINE_VERSION = "ainv-3.5.0"`

- [ ] **Step 1: Rewrite the input-builder tests** — in `tests/test_input_builder.py`:
  1. Replace `_patch` with:

```python
def _patch(monkeypatch, *, ranking=None):
    """Patch the fund-ranking CSV loader — the builder's only collaborator."""
    monkeypatch.setattr(ib, "get_fund_ranking", lambda: ranking or {})
```

  2. Delete `_Goal`, `_months_from_now`, `_ctx` and imports left unused (`date`, `uuid`); delete `test_short_term_unfunded_sets_flag_false`, `test_medium_term_unfunded_sets_flag_false`, `test_both_flags_true_when_funded_or_none`, `test_deficit_map_skips_cashflow_projection`, `test_sip_never_attaches_the_map_and_still_runs_flags`.
  3. In every remaining call, drop the leading `_ctx(),` argument; change `_patch(monkeypatch, ranking={}, goals=())` to `_patch(monkeypatch, ranking={})`; `debug["deployment_mode"] == "single_bucket"` becomes `"long_term"`; delete any assertion on the removed flags.
  4. Update the module docstring to: `"""Unit tests for the additional-investment engine input builder. The only collaborator is the fund-ranking CSV (an in-memory stand-in) plus a stand-in allocation output."""`
  5. Append:

```python
@pytest.mark.asyncio
async def test_goal_share_reaches_the_engine_input(monkeypatch):
    _patch(monkeypatch)
    inp, debug = await build_additional_investment_input_for_user(
        _alloc([_Row("low_beta_equities", long_term=1.0, total=1.0)]),
        deploy_amount_inr=25000.0,
        cadence=ib.Cadence.SIP_MONTHLY,
        current_value_by_subgroup={"low_beta_equities": 100000.0},
        goal_share_inr=10000.0,
        goal_subgroup="arbitrage",
    )
    assert inp.goal_share_inr == 10000.0
    assert inp.goal_subgroup == "arbitrage"
    assert inp.current_value_by_subgroup is None
    assert debug["deployment_mode"] == "long_term"


@pytest.mark.asyncio
async def test_lumpsum_keeps_the_holdings_map(monkeypatch):
    _patch(monkeypatch)
    inp, debug = await build_additional_investment_input_for_user(
        _alloc([_Row("low_beta_equities", long_term=1.0, total=1.0)]),
        deploy_amount_inr=500000.0,
        cadence=ib.Cadence.LUMPSUM,
        current_value_by_subgroup={"low_beta_equities": 100000.0},
    )
    assert inp.current_value_by_subgroup == {"low_beta_equities": 100000.0}
    assert inp.goal_share_inr == 0.0
    assert debug["deployment_mode"] == "deficit_fill"


def _funding(to_goals=15000.0, from_corpus=900000.0, subgroup="arbitrage"):
    return SimpleNamespace(goal_funding=SimpleNamespace(
        monthly_sip_to_goals=to_goals, from_corpus=from_corpus, asset_subgroup=subgroup,
    ))


def test_goal_share_for_sip_is_the_monthly_goal_share():
    assert ib.goal_share_for(_funding(), ib.Cadence.SIP_MONTHLY, 25000.0) == (15000.0, "arbitrage")


def test_goal_share_for_lumpsum_is_from_corpus_capped_at_deploy():
    assert ib.goal_share_for(_funding(), ib.Cadence.LUMPSUM, 500000.0) == (500000.0, "arbitrage")


def test_goal_share_for_a_preference_run_is_zero():
    assert ib.goal_share_for(SimpleNamespace(goal_funding=None), ib.Cadence.SIP_MONTHLY, 25000.0) == (0.0, None)
    assert ib.goal_share_for(SimpleNamespace(), ib.Cadence.LUMPSUM, 25000.0) == (0.0, None)
```

- [ ] **Step 2: Update the service tests** — in `tests/test_service.py`:
  1. Delete `test_incomplete_profile_dob_returns_blocking` and `test_incomplete_profile_required_inputs_returns_blocking`.
  2. Delete the `short_term_fulfilled=True,` / `medium_term_fulfilled=True,` kwargs in `_fake_ainv_input`, `_sip_empty_target_input`, `_sip_populated_input` (and likewise in `test_persist.py` and `test_persist_roundtrip.py`).
  3. In `test_sip_takes_no_snapshot_and_no_pin`, add `assert paa_mock.call_args.kwargs["monthly_sip"] == 25000.0`.
  4. Append:

```python
def _fake_alloc_with_goal_funding(to_goals=0.0, from_corpus=0.0, subgroup="arbitrage"):
    alloc = _fake_alloc()
    alloc.result.goal_funding = SimpleNamespace(
        monthly_sip_to_goals=to_goals, from_corpus=from_corpus, asset_subgroup=subgroup,
        allocated_amount=0, goals=[],
    )
    return alloc


async def _run_sip(svc, paa, builder, deploy):
    from additional_investment.models import Cadence

    user = SimpleNamespace(id=uuid.uuid4())
    with patch.object(svc, "compute_practical_allocation_result", new=paa), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder
    ), patch.object(svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None)):
        return await svc.compute_additional_investment_result(
            user, "start a sip", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=deploy, cadence=Cadence.SIP_MONTHLY,
            chat_ctx=SimpleNamespace(), persist=False,
        )


@pytest.mark.asyncio
async def test_sip_goal_share_comes_from_the_real_corpus_run():
    from app.domains.additional_investment.services.ainv_engine import service as svc

    builder = AsyncMock(return_value=(_sip_populated_input(25_000.0), {}))
    paa = AsyncMock(return_value=_fake_alloc_with_goal_funding(to_goals=15_000.0))
    await _run_sip(svc, paa, builder, 25_000.0)
    assert builder.call_args.kwargs["goal_share_inr"] == 15_000.0
    assert builder.call_args.kwargs["goal_subgroup"] == "arbitrage"


@pytest.mark.asyncio
async def test_thin_long_term_plan_rebuilds_from_a_sized_run_keeping_the_goal_share():
    from app.domains.additional_investment.services.ainv_engine import service as svc

    paa = AsyncMock(side_effect=[
        _fake_alloc_with_goal_funding(to_goals=5_000.0),
        _fake_alloc_with_goal_funding(to_goals=0.0),
    ])
    builder = AsyncMock(side_effect=[
        (_sip_empty_target_input(20_000.0), {}),
        (_sip_populated_input(20_000.0), {}),
    ])
    outcome = await _run_sip(svc, paa, builder, 20_000.0)
    assert paa.await_count == 2
    assert builder.await_args_list[1].kwargs["goal_share_inr"] == 5_000.0
    assert len(outcome.output.buys) >= 1


@pytest.mark.asyncio
async def test_sip_entirely_for_goals_needs_no_long_term_plan():
    from app.domains.additional_investment.services.ainv_engine import service as svc

    paa = AsyncMock(return_value=_fake_alloc_with_goal_funding(to_goals=20_000.0))
    builder = AsyncMock(return_value=(_sip_empty_target_input(20_000.0), {}))
    await _run_sip(svc, paa, builder, 20_000.0)
    assert paa.await_count == 1
    assert builder.await_count == 1


@pytest.mark.asyncio
async def test_lumpsum_uses_long_term_holdings_and_facts_count_goal_money():
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )
    from app.domains.additional_investment.services.ainv_engine import service as svc

    snapshot = HoldingsSnapshot(by_subgroup={
        "near_debt": 100_000.0, "arbitrage": 50_000.0, "low_beta_equities": 400_000.0,
    })
    alloc = SimpleNamespace(
        result=SimpleNamespace(
            aggregated_subgroups=[
                SimpleNamespace(subgroup="arbitrage", total=250_000.0, short_term=250_000.0),
                SimpleNamespace(subgroup="low_beta_equities", total=600_000.0, short_term=0.0),
            ],
            goal_funding=SimpleNamespace(
                allocated_amount=250_000, from_corpus=100_000.0, monthly_sip_to_goals=0.0,
                asset_subgroup="arbitrage", goals=[SimpleNamespace(from_holdings=150_000.0)],
            ),
            human_override_applied=None,
            corpus_breakdown=_fake_corpus_breakdown(),
        ),
        blocking_message=None,
    )
    engine_input = AdditionalInvestmentInput(
        deploy_amount_inr=300_000.0, cadence=Cadence.LUMPSUM,
        subgroups=[
            SubgroupBucketAmounts(subgroup="arbitrage", short_term=250_000.0, total=250_000.0),
            SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=600_000.0, total=600_000.0),
        ],
        current_value_by_subgroup={"low_beta_equities": 400_000.0},
        goal_share_inr=100_000.0, goal_subgroup="arbitrage",
        ranked_funds=[
            RankedFund(asset_subgroup="arbitrage", sub_category="Arbitrage Fund", rank=1,
                       isin="INF000000021", scheme_code="100021", recommended_fund="Arb Fund"),
            RankedFund(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund", rank=1,
                       isin="INF000000022", scheme_code="100022", recommended_fund="Bluechip"),
        ],
    )
    paa = AsyncMock(return_value=alloc)
    builder = AsyncMock(return_value=(engine_input, {}))
    user = SimpleNamespace(id=uuid.uuid4())
    with patch.object(svc, "load_holdings_snapshot", new=AsyncMock(return_value=snapshot)), \
            patch.object(svc, "compute_practical_allocation_result", new=paa), \
            patch.object(svc, "build_additional_investment_input_for_user", new=builder):
        outcome = await svc.compute_additional_investment_result(
            user, "invest 3 lakh", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=300_000.0, cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(), persist=False,
        )

    assert paa.call_args.kwargs["short_term_holdings"] == 150_000.0
    assert builder.call_args.kwargs["current_value_by_subgroup"] == {"low_beta_equities": 400_000.0}
    assert builder.call_args.kwargs["goal_share_inr"] == 100_000.0
    facts = {f["subgroup"]: f for f in outcome.deficit_facts}
    assert (facts["arbitrage"]["ideal_inr"], facts["arbitrage"]["current_inr"], facts["arbitrage"]["gap_inr"]) == (
        250_000.0, 150_000.0, 100_000.0,
    )
    assert (facts["low_beta_equities"]["ideal_inr"], facts["low_beta_equities"]["current_inr"]) == (
        600_000.0, 400_000.0,
    )
```

- [ ] **Step 3: Run to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests -q`
Expected: the new tests fail (`goal_share_for` missing; builder signature; missing kwargs on the practical call; old `deficit_facts`).

- [ ] **Step 4: Input builder** — `ainv_engine/input_builder.py`:
  1. Replace the module docstring with:

```python
"""Materialise an AdditionalInvestmentInput from the practical allocation.

Money is plain ``float`` (allocation family, not Decimal). The builder reads no
DB, ledger or NAV: subgroup rows come from the practical allocation, the goal
share is computed by the caller with ``goal_share_for``, and the BUY list comes
from the ranked-fund CSV. A lumpsum's ``current_value_by_subgroup`` is
pre-aggregated by the service. The two synthetic rows (ELSS + non-MF equity) are
passed through and excluded via ``exclude_subgroups``, not hand-dropped.
"""
```

  2. Delete the imports of `run_cashflow_projection_for_user`, `HORIZON_BOUNDARY_MONTHS`, `months_to_fy_end`, `date`, and the `TYPE_CHECKING` `TurnContext` import if nothing else uses it; delete `_months_to` and `_goal_funding_flags`.
  3. Add:

```python
def goal_share_for(
    allocation_output: Any, cadence: Cadence, deploy_amount_inr: float
) -> tuple[float, str | None]:
    """Money for short-term goals out of this deployment, and the subgroup it buys."""
    funding = getattr(allocation_output, "goal_funding", None)
    if funding is None:
        return 0.0, None
    share = (
        funding.monthly_sip_to_goals
        if cadence is Cadence.SIP_MONTHLY
        else funding.from_corpus
    )
    return min(float(share), deploy_amount_inr), funding.asset_subgroup
```

  4. `build_additional_investment_input_for_user`: remove the `ctx` parameter; add `goal_share_inr: float = 0.0,` and `goal_subgroup: str | None = None,` after `investable_corpus_inr`; delete `user = ctx.user_ctx`, `asof = date.today()` and the "2. Goal-funding flags" block, keeping only:

```python
    deficit_mode = (
        cadence is Cadence.LUMPSUM and current_value_by_subgroup is not None
    )
```

In `AdditionalInvestmentInput(...)` replace the two flag arguments with `goal_share_inr=goal_share_inr,` and `goal_subgroup=goal_subgroup,`. In `debug`: `"deployment_mode": "deficit_fill" if deficit_mode else "long_term",` and replace the two flag keys with `"goal_share_inr": goal_share_inr,` and `"goal_subgroup": goal_subgroup,`. Replace the function docstring with `"""Return ``(input, debug_dict)`` for ``run_additional_investment(...)``."""`.

- [ ] **Step 5: Service** — `ainv_engine/service.py`:
  1. Import `goal_share_for` with `build_additional_investment_input_for_user`; add `from app.domains.mutual_funds.services.scheme_classification import SHORT_TERM_HOLDING_SUBGROUPS, short_term_holdings_total`.
  2. Delete `_MSG_MISSING_DOB` and `_MSG_INCOMPLETE_PROFILE`.
  3. Next to `_SIP_RATIO_SIZING_CORPUS_INR`:

```python
# A SIP's long-term share needs a long-term plan at least this big to split by;
# below it the ratios come from a sized allocation instead.
_SIP_MIN_LONG_TERM_COLUMN_INR = 10_000.0


def _long_term_column_inr(inp) -> float:
    return sum(r.long_term for r in inp.subgroups if r.subgroup not in inp.exclude_subgroups)


def _long_term_holdings(by_subgroup: dict[str, float]) -> dict[str, float]:
    return {sg: v for sg, v in by_subgroup.items() if sg not in SHORT_TERM_HOLDING_SUBGROUPS}
```

> **Superseded 2026-09-29 (product owner):** `_long_term_holdings(by_subgroup, held_for_goals)` removes only the goal-used held money (Σ `from_holdings`), pro rata across held short-term subgroups; excess held debt stays current; preference customers keep full holdings. `held_for_goals` and one `current_lt` map are computed once after the practical run and feed both the engine input and `deficit_facts`. As built in the working tree.

  4. Update the comments above the snapshot load ("SIP keeps the legacy profile-corpus path…") and in the function docstring ("holding-agnostic — no holdings fetch") to: "Lumpsum pins the corpus and held short-term money to one holdings snapshot; SIP reads held short-term money off the preloaded user."
  5. First `compute_practical_allocation_result(...)` call: add

```python
        monthly_sip=deploy_amount_inr if cadence is Cadence.SIP_MONTHLY else None,
        short_term_holdings=(
            short_term_holdings_total(snapshot.by_subgroup) if snapshot is not None else None
        ),
```

  6. After `investable_corpus_inr = …`:

```python
    goal_share_inr, goal_subgroup = goal_share_for(
        paa_outcome.result, cadence, deploy_amount_inr
    )
```

  7. First builder call: drop the `chat_ctx,` positional argument; pass `current_value_by_subgroup=(_long_term_holdings(snapshot.by_subgroup) if snapshot is not None else None),`, `goal_share_inr=goal_share_inr,` and `goal_subgroup=goal_subgroup,`. Delete its `except ValueError as exc:` branch (keep the generic `except Exception`).
  8. Delete the post-run block starting `if cadence is Cadence.SIP_MONTHLY and not response.buys:` (and its comment). Insert immediately before `if progress: await progress(75, …)`:

```python
    if (
        cadence is Cadence.SIP_MONTHLY
        and deploy_amount_inr - goal_share_inr > 0
        and _long_term_column_inr(inp) < _SIP_MIN_LONG_TERM_COLUMN_INR
    ):
        try:
            sized = await compute_practical_allocation_result(
                user,
                user_question,
                chat_ctx=chat_ctx,
                corpus_pin=CorpusPin(
                    total_corpus=_SIP_RATIO_SIZING_CORPUS_INR,
                    mf_corpus=_SIP_RATIO_SIZING_CORPUS_INR,
                    non_mf_equity_corpus=0.0,
                    elss_corpus=0.0,
                ),
                monthly_sip=deploy_amount_inr,
            )
            if sized.result is not None:
                inp, debug = await build_additional_investment_input_for_user(
                    sized.result,
                    deploy_amount_inr=deploy_amount_inr,
                    cadence=cadence,
                    current_value_by_subgroup=None,
                    investable_corpus_inr=investable_corpus_inr,
                    goal_share_inr=goal_share_inr,
                    goal_subgroup=goal_subgroup,
                )
                trace_line("additional_investment SIP long-term split taken from a sized allocation")
        except Exception:  # noqa: BLE001 — keep the real-corpus split, never raise
            logger.exception(
                "additional_investment: sized SIP long-term split failed — keeping "
                "the real-corpus split"
            )
```

  9. Replace the `deficit_facts` block with:

```python
    deficit_facts: list[dict] | None = None
    if snapshot is not None:
        rows_by = {r.subgroup: r for r in paa_outcome.result.aggregated_subgroups}
        funding = getattr(paa_outcome.result, "goal_funding", None)
        held = sum(g.from_holdings for g in funding.goals) if funding is not None else 0.0
        current_lt = _long_term_holdings(snapshot.by_subgroup)
        buys_by: dict[str, float] = {}
        for b in response.buys:
            buys_by[b.asset_subgroup] = buys_by.get(b.asset_subgroup, 0.0) + float(b.amount_inr)
        deficit_facts = []
        for t in response.per_subgroup_target:
            row = rows_by.get(t.subgroup)
            ideal = float(row.total - row.short_term) if row is not None else 0.0
            current = current_lt.get(t.subgroup, 0.0)
            if funding is not None and t.subgroup == funding.asset_subgroup:
                ideal += funding.allocated_amount
                current += held
            deficit_facts.append(
                {
                    "subgroup": t.subgroup,
                    "ideal_inr": ideal,
                    "current_inr": current,
                    "gap_inr": max(0.0, ideal - current),
                    "buy_inr": buys_by.get(t.subgroup, 0.0),
                }
            )
```

  10. Append to the version changelog `# 3.5.0: SIP-first goal waterfall — goal money first from the practical allocation's goal_funding (no cashflow projection); the rest follows the long-term plan; lumpsum deficits exclude short-term money.` and set `AINV_ENGINE_VERSION = "ainv-3.5.0"`.

- [ ] **Step 6: Run the tests**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors app/domains/additional_investment AI_Agents/tests
grep -rn "run_cashflow_projection_for_user\|_goal_funding_flags\|short_term_fulfilled\|medium_term_fulfilled\|_MSG_MISSING_DOB\|_MSG_INCOMPLETE_PROFILE" app/domains/additional_investment
```

Expected: all new tests pass; `test_sip_with_empty_target_bucket_still_names_funds` and `test_funded_sip_does_not_trigger_sized_fallback` still pass; no FAILED/ERROR outside the baseline; the grep prints nothing.

- [ ] **Step 7: Checkpoint (no commit)**

---

### Task 10: Lifecycle sim — before vs after (dev-only, gitignored)

**Files:**
- Modify: `AI_Agents/lifecycle_sim_testing/engines.py`, `simulate.py`

- [ ] **Step 1: New inputs and goal share** — `engines.py`, after the existing `src` imports:

```python
from Rebalancing.tables import DEBT_NETTING_POOL  # noqa: E402

_SHORT_TERM_HOLDING_SUBGROUPS = DEBT_NETTING_POOL - {"arbitrage_plus_income"}
```

`refresh_alloc_input(base_profile, month: int, port: Portfolio, age_goals: bool = True, monthly_sip: float = 0.0)`; before its `return` add `held = port.current_value_by_subgroup()` and add to the `update` dict:

```python
        "monthly_sip": float(monthly_sip),
        "short_term_holdings": float(
            sum(v for sg, v in held.items() if sg in _SHORT_TERM_HOLDING_SUBGROUPS)
        ),
```

`sip()`, after `inp = _build_input(...)`:

```python
    gf = alloc.goal_funding
    inp = inp.model_copy(update={
        "goal_share_inr": gf.monthly_sip_to_goals if gf else 0.0,
        "goal_subgroup": gf.asset_subgroup if gf else None,
    })
```

`lumpsum()`, after `inp = _build_input(...)`:

```python
    gf = alloc_pinned.goal_funding
    held = port.current_value_by_subgroup()
    held_for_goals = sum(g.from_holdings for g in gf.goals) if gf else 0.0
    short_total = sum(v for sg, v in held.items() if sg in _SHORT_TERM_HOLDING_SUBGROUPS and v > 0)
    keep = 1.0 - min(held_for_goals, short_total) / short_total if short_total > 0 else 1.0
    inp = inp.model_copy(update={
        "goal_share_inr": min(float(amount), gf.from_corpus) if gf else 0.0,
        "goal_subgroup": gf.asset_subgroup if gf else None,
        "current_value_by_subgroup": {
            sg: v * keep if sg in _SHORT_TERM_HOLDING_SUBGROUPS else v for sg, v in held.items()
        },
    })
```

This mirrors production's `_long_term_holdings(by_subgroup, held_for_goals)` in `ainv_engine/service.py` (decided 2026-09-29): only the held short-term money the goals use is removed, pro rata; excess held debt stays current.

(The rebalance bridge copies the refreshed profile, so rebalancing picks up the new fields unchanged. `_build_input` still passes the deleted flag kwargs; pydantic ignores them.)

- [ ] **Step 2: Pass the SIP** — `simulate.py`: both `engines.refresh_alloc_input(profile, 0, port, age_goals)` and `engines.refresh_alloc_input(profile, m, port, age_goals)` gain a fifth argument `sip_amt if investments_on else 0`.

- [ ] **Step 3: Sim tests** — `cd AI_Agents && ../.venv-mac/bin/python -m pytest -q lifecycle_sim_testing/tests; cd ..` → pass (money conservation every month).

- [ ] **Step 4: "After" metrics and comparison**

```bash
cd AI_Agents && ../.venv-mac/bin/python -m lifecycle_sim_testing.compare_waterfall after; cd ..
.venv-mac/bin/python - <<'EOF'
import json
b = json.load(open("AI_Agents/lifecycle_sim_testing/waterfall_metrics_before.json"))
a = json.load(open("AI_Agents/lifecycle_sim_testing/waterfall_metrics_after.json"))
for n in b:
    print(f"{n}: equity_sold {b[n]['equity_sold_total']:,} -> {a[n]['equity_sold_total']:,} | "
          f"debt_to_debt {b[n]['debt_to_debt_total']:,} -> {a[n]['debt_to_debt_total']:,} | "
          f"last SIP-debt month {b[n]['last_month_sip_bought_debt']} -> {a[n]['last_month_sip_bought_debt']} | "
          f"shortfall {b[n]['goal_shortfall']:,} -> {a[n]['goal_shortfall']:,}")
EOF
```

Expected per profile: `equity_sold` after ≤ before for profiles with short-term goals; `debt_to_debt` after ≤ before; `shortfall` after is 0 unless already non-zero before. The sim's profiles hold no off-list debt and `asset_class_of` does not know near/medium debt, so Task 7 barely shows here — its evidence is Task 7's tests. Report any profile that breaks these expectations with its per-rebalance rows; do not tune the engine to hide it.

- [ ] **Step 5: Checkpoint (no commit)** — the sim folder is gitignored.

---

### Task 11: Context docs and the full-suite comparison

**Files:**
- Modify: `AI_Agents/src/CLAUDE.md`, `AI_Agents/src/asset_allocation_pydantic/CLAUDE.md`, `AI_Agents/src/practical_asset_allocation/CLAUDE.md`, `AI_Agents/src/additional_investment/CLAUDE.md`, `AI_Agents/src/Rebalancing/CLAUDE.md`, `app/domains/additional_investment/CLAUDE.md`, `app/domains/practical_asset_allocation/CLAUDE.md`

(`AI_Agents/Reference_docs/` is untouched here; the Logics doc refresh ships with the chat spec.)

- [ ] **Step 1: Replace stale lines, then add one bullet per new invariant**

Replace (not append):
- `AI_Agents/src/CLAUDE.md` Cross-module edges, the `additional_investment/` bullet: "…the app-layer adapter lifts that data from `practical_asset_allocation/` and the fund-ranking CSV." (drop `cashflow_statement/`).
- `AI_Agents/src/additional_investment/CLAUDE.md` header: "Lumpsum-with-holdings fills allocation deficits; SIP follows the ideal mix." → "Goal money first, then the long-term plan (SIP) or long-term deficits (lumpsum)." Entry/contract input list: replace `` `short_term_fulfilled`/`medium_term_fulfilled` `` with `` `goal_share_inr`/`goal_subgroup` ``. Gotchas: replace the "Two split modes", "Deficit-fill" and "Bucket targeting (legacy path)" bullets with one bullet: "**One split: goal money first** (`ratio.py::compute_goal_first_targets`). The goal share goes to `goal_subgroup` by name — never weighted by the short-term column, which is empty when the plan holds no short-term money. The rest follows the long-term column (SIP) or deficit-fill against each row's `total − short_term` (lumpsum; the caller passes current holdings without short-term money)."
- `app/domains/additional_investment/CLAUDE.md`: in "Lumpsum runs deficit-fill…" change `ainv-3.4.0` to `ainv-3.5.0` and "SIP keeps the legacy profile-corpus path" to "SIP runs on the profile corpus with held short-term money read off the preloaded user"; replace the "short-goal funding boundary is FY-anchored (`_goal_funding_flags`)" bullet with: "**The goal share comes from the practical allocation's `goal_funding`** (`ainv_engine/input_builder.py::goal_share_for`) — no cashflow projection and no incomplete-profile gate on this path. A SIP whose long-term plan is under ₹10,000 takes its long-term split from the ₹1cr sized run, keeping the real run's goal share."
- `AI_Agents/src/Rebalancing/CLAUDE.md` Entry/contract, the NEUTRAL sentence: append "In a debt subgroup step2b nets that sell against debt buys, so a held off-list debt fund is kept."

Add:
- `asset_allocation_pydantic/CLAUDE.md` Gotchas: "**Step 2 is the goal waterfall** (`steps/step2_short_term.py::goal_waterfall`): held short-term money, then the front-loaded SIP, then corpus for only what the SIP cannot reach; it rounds before capping (`need = round_to_100(held + corpus_needed)`), so defaults reproduce the old step 2 exactly — `AI_Agents/tests/test_goal_waterfall_step2.py` guards that, not the goldens (they have no goals). `amount_needed_fv` is read by step 2 only."
- `practical_asset_allocation/CLAUDE.md` Gotchas: "**`goal_funding` is the one source of the short-term target and the SIP's goal share**; `None` when a preference suspends step 2."
- `Rebalancing/CLAUDE.md` Gotchas: "**A held debt fund is never sold to buy another** (`steps/step2b_suppress_debt_switch.py`): `DEBT_NETTING_POOL` is every Debt-class subgroup (parity test `app/domains/rebalancing/tests/test_debt_netting_pool_parity.py`) and off-list debt sells are nettable; force-exits still exit."
- `app/domains/practical_asset_allocation/CLAUDE.md` Gotchas: "**Held short-term money is read off the preloaded user** (`paa_engine/input_builder.py::short_term_holdings_for_user`; `None` = no holdings on file) unless the caller passes `short_term_holdings` — rebalancing passes it from its ledger rows, lumpsum from its snapshot, so each flow uses one valuation. The stated SIP comes from the shared allocation builder; only the SIP flow overrides `monthly_sip`."

- [ ] **Step 2: Full-suite comparison (about 12 minutes)**

```bash
.venv-mac/bin/python -m pytest -q --continue-on-collection-errors -rfE 2>&1 | grep -E "^(FAILED|ERROR)" | sort > $SCRATCH/after_full.txt
comm -13 $SCRATCH/baseline_full.txt $SCRATCH/after_full.txt
comm -23 $SCRATCH/baseline_full.txt $SCRATCH/after_full.txt
```

Expected: the first `comm` (new failures) prints nothing; fix or re-baseline (with the reason) anything it prints. The second lists tests this change fixed — report them.

- [ ] **Step 3: Final checkpoint (no commit)** — list changed files (`git status --short`), note that the spec and plan under `docs/superpowers/` need `git add -f`, and hand back the Task 10 before/after table.

---

## Not in this plan (mention only)

- Chat facts packs, prompts (including `ainv_engine/chat.py`'s `target_bucket` definition, now stale), the SIP-amount what-if and the Logics doc refresh — the chat spec.
- Pre-existing vestigial code, untouched here: `cap_pct_by_subgroup`, `default_cap_pct`, `sip_fund_cap_floor_inr`, `lumpsum_fund_cap_floor_inr`, `rebal_buy_isins_by_subgroup` and the builder's cap wiring; the `medium_term` columns and `TargetBucket.MEDIUM_TERM`; `current_subgroup_allocation`'s "populated by every real caller" docstring (no app caller sets it); the ainv_engine `__init__` "later Plan-3a tasks" note.

## Self-review

- **Spec coverage:** algorithm and defaults → Tasks 1–2; outputs → 2; FV → 3–4; stated SIP → 4–5, 9; held short-term money (user, rebalancing rows, lumpsum snapshot) → 5, 6, 9; debt-for-debt rule → 7; goal-first split and dead legacy mode → 8; SIP/lumpsum wiring, facts, rescue, profile gate → 9; sim → 0, 10; docs → 11.
- **Names:** every task uses the table in Global Constraints (`monthly_sip`, `short_term_holdings`, `allocated_amount`, `from_corpus`, `monthly_sip_to_goals`, `shortfall`, `asset_subgroup`, `amount_needed_fv`, `from_holdings`, `from_sip`; `goal_share_inr`, `goal_subgroup` in the fresh-money engine).
- **Types:** `GoalFunding.allocated_amount`/`shortfall` are `int`, other amounts `float`; `goal_share_for` returns `(float, str | None)`; `short_term_holdings` is `Optional[float]` in the engine and `float | None` in the app.
