# Allocation v2 Part A — `asset_allocation_pydantic` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply 5 surgical changes to `asset_allocation_pydantic` per spec sections A.1–A.5. Input/output contract unchanged.

**Architecture:** Existing 7-step pipeline kept; only constants in `tables.py` and per-step orchestration in `step1`/`step2`/`step3`/`step4` change. No structural changes, no new modules, no schema migration.

**Tech Stack:** Python 3.11+, pydantic v2, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md` Part A.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `AI_Agents/src/asset_allocation_pydantic/tables.py` | Modify | Bucket-boundary constants (`MEDIUM_TERM_BOUNDARY_MONTHS`, `LONG_TERM_BOUNDARY_MONTHS`); rename `MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE` → `..._INCLUSIVE`. |
| `AI_Agents/src/asset_allocation_pydantic/steps/step1_emergency.py` | Modify | Drop tax-rate routing branch; hard-code `short_debt`. |
| `AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py` | Modify | Split into ST1 (months < 24) + ST2 (24 ≤ months < 36) with per-sub-bucket tax thresholds. |
| `AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py` | Modify | Boundary `<= LONG_TERM_BOUNDARY_MONTHS` → `< LONG_TERM_BOUNDARY_MONTHS`; `_risk_bucket` `<` → `<=` on Low/Medium edge; add `market_commentary.equities ≤ 3` override. |
| `AI_Agents/src/asset_allocation_pydantic/steps/step4_long_term.py` | Modify | Boundary `> LONG_TERM_BOUNDARY_MONTHS` → `>= LONG_TERM_BOUNDARY_MONTHS`. |
| `AI_Agents/src/asset_allocation_pydantic/models.py` | Modify (small) | Widen `Step2Output.asset_subgroup` literal to `"short_debt" | "arbitrage" | "mixed"` for the dual-sub-bucket case. |
| `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py` | Create (LOCAL — gitignored) | New TDD tests for each of the 5 changes. |

---

## Conventions

- Tests live in `AI_Agents/src/asset_allocation_pydantic/Testing/` — this folder is **gitignored** per `.gitignore` (`/AI_Agents/src/*/Testing/`). Never `git add` files in this folder; tests run locally for TDD discipline but are not part of the shipping artifact.
- Each task's commit step adds engine code **only**.
- Bucket boundary constants are the single source of truth: `MEDIUM_TERM_BOUNDARY_MONTHS` = upper-exclusive edge of short-term (i.e. ST is `months < MTBM`); `LONG_TERM_BOUNDARY_MONTHS` = upper-exclusive edge of medium-term (MT is `MTBM ≤ months < LTBM`). LT is `months ≥ LTBM`.
- Commit messages follow `feat(asset-allocation): <one-liner>` and reference the spec section.

---

### Task 1: A.1 — Bucket boundary shift (`< 36 / 36–71 / ≥ 72` months)

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/tables.py:154-158`
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py:36`
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step4_long_term.py:362`
- Test: `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

Add to `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py`:

```python
from asset_allocation_pydantic.models import AllocationInput, Goal
from asset_allocation_pydantic.pipeline import run_allocation_with_state


def _base_input(**overrides) -> AllocationInput:
    """Minimal AllocationInput shared across tests in this file."""
    base = dict(
        effective_risk_score=5.5,
        age=40,
        annual_income=2_000_000,
        osi=0.0,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        shortfall_amount=0.0,
        total_corpus=10_000_000,
        monthly_household_expense=100_000,
        effective_tax_rate=15.0,
        net_financial_assets=10_000_000,
        goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_a1_year_2_goal_is_short_term_not_medium():
    """Per A.1: goal at month 30 (year 2) is now short-term, was medium-term."""
    inp = _base_input(
        goals=[Goal(
            goal_name="Year-2 goal",
            time_to_goal_months=30,
            amount_needed=200_000,
            goal_priority="non_negotiable",
        )]
    )
    state, out = run_allocation_with_state(inp)
    assert state["step2_short_term"].total_goal_amount == 200_000, (
        "year-2 goal must be in short-term bucket after A.1"
    )
    assert state["step3_medium_term"].total_goal_amount == 0


def test_a1_year_5_goal_is_medium_term_not_long_term():
    """Per A.1: goal at month 60 (year 5) is medium-term, was long-term."""
    inp = _base_input(
        goals=[Goal(
            goal_name="Year-5 goal",
            time_to_goal_months=60,
            amount_needed=500_000,
            goal_priority="non_negotiable",
        )]
    )
    state, out = run_allocation_with_state(inp)
    assert state["step3_medium_term"].total_goal_amount == 500_000, (
        "year-5 goal must be in medium-term bucket after A.1"
    )
    assert state["step4_long_term"].total_long_term_corpus < 10_000_000, (
        "long-term must NOT have received the year-5 goal"
    )


def test_a1_year_6_goal_is_long_term():
    """Per A.1: goal at month 72 (year 6) is long-term."""
    inp = _base_input(
        goals=[Goal(
            goal_name="Year-6 goal",
            time_to_goal_months=72,
            amount_needed=300_000,
            goal_priority="non_negotiable",
        )]
    )
    state, out = run_allocation_with_state(inp)
    assert state["step3_medium_term"].total_goal_amount == 0
    # Long-term consumes everything left after emergency.
    assert state["step4_long_term"].total_long_term_corpus > 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py::test_a1_year_2_goal_is_short_term_not_medium -v
```

Expected: FAIL — `assert state["step2_short_term"].total_goal_amount == 200_000` because month 30 is still classified as medium-term under current boundaries (`24 ≤ months ≤ 60`).

- [ ] **Step 3: Update boundary constants in `tables.py`**

Replace lines 154–158:

```python
# Bucket boundaries (in months) used when classifying goals.
# short-term:   months <  MEDIUM_TERM_BOUNDARY_MONTHS
# medium-term:  MEDIUM_TERM_BOUNDARY_MONTHS <= months <  LONG_TERM_BOUNDARY_MONTHS
# long-term:    months >= LONG_TERM_BOUNDARY_MONTHS
MEDIUM_TERM_BOUNDARY_MONTHS: int = 36
LONG_TERM_BOUNDARY_MONTHS: int = 72
```

(Old values 24 / 60 → new values 36 / 72; docstring comment updated to reflect upper-exclusive convention on both boundaries.)

- [ ] **Step 4: Update `step3_medium_term.py` filter operator**

Replace line 36:

```diff
-    goals_in_bucket = [
-        g for g in inp.goals
-        if MEDIUM_TERM_BOUNDARY_MONTHS <= g.time_to_goal_months <= LONG_TERM_BOUNDARY_MONTHS
-    ]
+    goals_in_bucket = [
+        g for g in inp.goals
+        if MEDIUM_TERM_BOUNDARY_MONTHS <= g.time_to_goal_months < LONG_TERM_BOUNDARY_MONTHS
+    ]
```

(Inner `<=` on upper bound becomes `<` — year 6 goals exit medium-term.)

- [ ] **Step 5: Update `step4_long_term.py` filter operator**

Replace line 362:

```diff
-    lt_goals = [g for g in inp.goals if g.time_to_goal_months > LONG_TERM_BOUNDARY_MONTHS]
+    lt_goals = [g for g in inp.goals if g.time_to_goal_months >= LONG_TERM_BOUNDARY_MONTHS]
```

(`>` becomes `>=` so month 72 is long-term — was previously orphaned because medium upper was inclusive at 60 and long-term lower was strict at 60.)

- [ ] **Step 6: Run the three A.1 tests — all should pass**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a1_" -v
```

Expected: 3 passed.

- [ ] **Step 7: Run the full asset_allocation_pydantic test suite — catch regressions**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green. The existing `test_no_fund_mapping.py` uses goals at 18 / 48 / 240 / 300 months — month 18 stays short-term, month 48 stays medium-term, months 240 and 300 stay long-term under both old and new boundaries, so it should pass unchanged.

- [ ] **Step 8: Commit (engine code only — tests are gitignored)**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
git add AI_Agents/src/asset_allocation_pydantic/tables.py \
        AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py \
        AI_Agents/src/asset_allocation_pydantic/steps/step4_long_term.py
git commit -m "feat(asset-allocation): shift bucket boundaries to 36 / 72 months (A.1)

Short-term now covers years 0-2 (months < 36), medium-term covers years 3-5
(36 <= months < 72), long-term covers year 6+ (months >= 72). Adopts the
upper-exclusive convention on both boundaries so a year-5 goal at month 60
is medium-term and a year-6 goal at month 72 is long-term — previously the
year-5 goal was long-term and month 72 was orphaned at the medium upper edge.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §A.1"
```

---

### Task 2: A.2 — Step 1 emergency always routes to `short_debt`

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step1_emergency.py:34-39`
- Test: `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

Append to `test_part_a.py`:

```python
def test_a2_emergency_routes_to_short_debt_high_tax():
    """Per A.2: with tax > 20%, emergency must route to short_debt (was: arbitrage)."""
    inp = _base_input(effective_tax_rate=30.0)
    state, _ = run_allocation_with_state(inp)
    assert state["step1_emergency"].subgroup_amounts == {
        "short_debt": state["step1_emergency"].total_emergency
    }, "emergency must always land in short_debt regardless of tax rate"


def test_a2_emergency_routes_to_short_debt_low_tax():
    """Per A.2: with tax <= 20%, emergency still routes to short_debt (unchanged)."""
    inp = _base_input(effective_tax_rate=10.0)
    state, _ = run_allocation_with_state(inp)
    assert "short_debt" in state["step1_emergency"].subgroup_amounts
    assert "arbitrage" not in state["step1_emergency"].subgroup_amounts
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py::test_a2_emergency_routes_to_short_debt_high_tax -v
```

Expected: FAIL — current code routes to `arbitrage` when `tax > 20`.

- [ ] **Step 3: Make the change in `step1_emergency.py`**

Replace lines 34–39:

```diff
-    asset_subgroup = (
-        "arbitrage"
-        if inp.effective_tax_rate > TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD
-        else "short_debt"
-    )
-    subgroup_amounts: dict[str, int] = {asset_subgroup: total_emergency}
+    # A.2: emergency fund always routes to short_debt regardless of tax rate.
+    subgroup_amounts: dict[str, int] = {"short_debt": total_emergency}
```

Also remove the now-orphaned import on line 4:

```diff
-from ..tables import EMERGENCY_FUND_MONTHS, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD
+from ..tables import EMERGENCY_FUND_MONTHS
```

(Do NOT remove `TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD` from `tables.py` — `step2_short_term.py` still imports it for ST1 routing.)

- [ ] **Step 4: Run the A.2 tests — both should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a2_" -v
```

Expected: 2 passed.

- [ ] **Step 5: Run the full module test suite**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/asset_allocation_pydantic/steps/step1_emergency.py
git commit -m "feat(asset-allocation): emergency fund always routes to short_debt (A.2)

Removes the tax-rate gate that previously sent emergency to arbitrage when
effective_tax_rate > 20%. Emergency is a stability bucket; arbitrage funds
add NAV volatility that defeats the purpose. TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD
remains in tables.py for step2 ST1 routing.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §A.2"
```

---

### Task 3: A.3 — Step 2 short-term year-split with two tax thresholds

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/models.py:204` (widen `Step2Output.asset_subgroup` literal)
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py` (full rewrite of `run`)
- Test: `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

Append to `test_part_a.py`:

```python
def test_a3_st1_yr01_routes_arbitrage_when_tax_above_20():
    """ST1 (months < 24, years 0-1): arbitrage when tax > 20."""
    inp = _base_input(
        effective_tax_rate=25.0,
        goals=[Goal(goal_name="ST1 goal", time_to_goal_months=12,
                    amount_needed=300_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    s2 = state["step2_short_term"]
    assert s2.subgroup_amounts.get("arbitrage", 0) == 300_000
    assert s2.subgroup_amounts.get("short_debt", 0) == 0


def test_a3_st1_yr01_routes_short_debt_when_tax_at_or_below_20():
    """ST1: short_debt when tax <= 20."""
    inp = _base_input(
        effective_tax_rate=18.0,
        goals=[Goal(goal_name="ST1 goal", time_to_goal_months=12,
                    amount_needed=300_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    s2 = state["step2_short_term"]
    assert s2.subgroup_amounts.get("short_debt", 0) == 300_000
    assert s2.subgroup_amounts.get("arbitrage", 0) == 0


def test_a3_st2_yr2_routes_arbitrage_when_tax_above_12_5():
    """ST2 (24 <= months < 36, year 2): arbitrage when tax > 12.5."""
    inp = _base_input(
        effective_tax_rate=15.0,
        goals=[Goal(goal_name="ST2 goal", time_to_goal_months=30,
                    amount_needed=400_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    s2 = state["step2_short_term"]
    assert s2.subgroup_amounts.get("arbitrage", 0) == 400_000


def test_a3_split_st1_and_st2_can_pick_different_subgroups():
    """At tax=18%: ST1 -> short_debt, ST2 -> arbitrage (both subgroups present)."""
    inp = _base_input(
        effective_tax_rate=18.0,
        goals=[
            Goal(goal_name="ST1 goal", time_to_goal_months=12,
                 amount_needed=100_000, goal_priority="non_negotiable"),
            Goal(goal_name="ST2 goal", time_to_goal_months=30,
                 amount_needed=200_000, goal_priority="non_negotiable"),
        ],
    )
    state, _ = run_allocation_with_state(inp)
    s2 = state["step2_short_term"]
    assert s2.subgroup_amounts.get("short_debt", 0) == 100_000
    assert s2.subgroup_amounts.get("arbitrage", 0) == 200_000
    assert s2.total_goal_amount == 300_000
    assert s2.allocated_amount == 300_000


def test_a3_both_route_short_debt_when_tax_at_or_below_12_5():
    """ST1 and ST2 both -> short_debt when tax <= 12.5."""
    inp = _base_input(
        effective_tax_rate=10.0,
        goals=[
            Goal(goal_name="ST1 goal", time_to_goal_months=12,
                 amount_needed=100_000, goal_priority="non_negotiable"),
            Goal(goal_name="ST2 goal", time_to_goal_months=30,
                 amount_needed=200_000, goal_priority="non_negotiable"),
        ],
    )
    state, _ = run_allocation_with_state(inp)
    s2 = state["step2_short_term"]
    assert s2.subgroup_amounts.get("short_debt", 0) == 300_000
    assert s2.subgroup_amounts.get("arbitrage", 0) == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a3_" -v
```

Expected: at least `test_a3_split_st1_and_st2_can_pick_different_subgroups` fails — current code uses one subgroup decision for all short-term goals.

- [ ] **Step 3: Widen `Step2Output.asset_subgroup` literal in `models.py:204`**

```diff
 class Step2Output(BaseModel):
     goals_allocated: List[Goal]
-    asset_subgroup: Literal["short_debt", "arbitrage"]
+    asset_subgroup: Literal["short_debt", "arbitrage", "mixed"]
     total_goal_amount: int
     allocated_amount: int
```

The `"mixed"` label denotes the dual-sub-bucket case where ST1 and ST2 picked different subgroups; the per-bucket detail lives in `subgroup_amounts`. Internal field — `step7_presentation` reads `subgroup_amounts`, not this label, so the public contract is unaffected.

- [ ] **Step 4: Rewrite `step2_short_term.py`**

Replace the file contents:

```python
from __future__ import annotations

from typing import Literal

from ..models import AllocationInput, FutureInvestment, Goal, Step2Output
from ..tables import (
    MEDIUM_TERM_BOUNDARY_MONTHS,
    TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD,
)
from ..utils import round_to_100


# A.3: year-2 goals (ST2) get a lower tax threshold (12.5%) than year-0/1 (ST1, 20%).
ST2_LOWER_MONTHS_INCLUSIVE: int = 24
ST2_TAX_THRESHOLD_PCT: float = 12.5


def _route(tax_rate_pct: float, threshold_pct: float) -> Literal["short_debt", "arbitrage"]:
    return "arbitrage" if tax_rate_pct > threshold_pct else "short_debt"


def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output:
    # A.1: short-term bucket is months < MEDIUM_TERM_BOUNDARY_MONTHS (36).
    # A.3: split into ST1 (months < 24) and ST2 (24 <= months < 36).
    st1_goals = [g for g in inp.goals if g.time_to_goal_months < ST2_LOWER_MONTHS_INCLUSIVE]
    st2_goals = [
        g for g in inp.goals
        if ST2_LOWER_MONTHS_INCLUSIVE <= g.time_to_goal_months < MEDIUM_TERM_BOUNDARY_MONTHS
    ]
    goals_allocated = st1_goals + st2_goals

    st1_sg = _route(inp.effective_tax_rate, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD)
    st2_sg = _route(inp.effective_tax_rate, ST2_TAX_THRESHOLD_PCT)

    st1_amount = round_to_100(sum(g.amount_needed for g in st1_goals))
    st2_amount = round_to_100(sum(g.amount_needed for g in st2_goals))
    total_goal_amount = st1_amount + st2_amount

    # Allocate ST1 first against remaining_corpus, then ST2 from what's left.
    st1_allocated = min(st1_amount, remaining_corpus)
    pool_after_st1 = remaining_corpus - st1_allocated
    st2_allocated = min(st2_amount, pool_after_st1)
    allocated_amount = st1_allocated + st2_allocated
    new_remaining = remaining_corpus - allocated_amount

    # Combine subgroup amounts (may carry one or two entries depending on routing).
    subgroup_amounts: dict[str, int] = {}
    if st1_allocated > 0:
        subgroup_amounts[st1_sg] = subgroup_amounts.get(st1_sg, 0) + st1_allocated
    if st2_allocated > 0:
        subgroup_amounts[st2_sg] = subgroup_amounts.get(st2_sg, 0) + st2_allocated

    # Future investment when corpus runs out mid-bucket.
    future_investment: FutureInvestment | None = None
    if total_goal_amount > remaining_corpus:
        negotiable = [g.goal_name for g in goals_allocated if g.goal_priority == "negotiable"]
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
            future_investment_amount=total_goal_amount - remaining_corpus,
            message=msg,
        )

    # Decide the headline asset_subgroup label.
    if st1_sg == st2_sg:
        headline_sg: Literal["short_debt", "arbitrage", "mixed"] = st1_sg  # type: ignore[assignment]
    elif st1_allocated > 0 and st2_allocated > 0:
        headline_sg = "mixed"
    elif st1_allocated > 0:
        headline_sg = st1_sg  # type: ignore[assignment]
    else:
        headline_sg = st2_sg  # type: ignore[assignment]

    return Step2Output(
        goals_allocated=goals_allocated,
        asset_subgroup=headline_sg,
        total_goal_amount=total_goal_amount,
        allocated_amount=allocated_amount,
        remaining_corpus=new_remaining,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )
```

- [ ] **Step 5: Run the A.3 tests — all five should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a3_" -v
```

Expected: 5 passed.

- [ ] **Step 6: Run the full module test suite**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py \
        AI_Agents/src/asset_allocation_pydantic/models.py
git commit -m "feat(asset-allocation): split short-term into ST1/ST2 with tax thresholds 20% / 12.5% (A.3)

ST1 covers months < 24 (years 0-1) and routes to arbitrage when tax > 20%,
else short_debt. ST2 covers 24 <= months < 36 (year 2) and routes to
arbitrage when tax > 12.5%, else short_debt. Year-2 goals now reach
short-term arbitrage at a meaningfully lower tax bracket, since the
12-month STCG window for arbitrage works in their favour. ST1 is allocated
first against the remaining_corpus pool, then ST2 from what's left.

Widens Step2Output.asset_subgroup literal to include 'mixed' for the case
where ST1 and ST2 land in different subgroups; per-sub-bucket amounts live
in Step2Output.subgroup_amounts as before.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §A.3"
```

---

### Task 4: A.4 — Step 3 medium-term market-view override

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py:53` (insert override after the `MEDIUM_TERM_SPLIT` lookup)
- Test: `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

Append to `test_part_a.py`:

```python
def test_a4_equities_view_at_or_below_3_forces_low_column():
    """Per A.4: market_commentary.equities <= 3 forces the Low column for medium-term."""
    from asset_allocation_pydantic.models import MarketCommentaryScores

    # Risk 5.5 -> Medium bucket normally. Year-5 goal would get 70/30 eq/debt.
    # With equities view = 2, override to Low column: 50/50.
    inp = _base_input(
        effective_risk_score=5.5,
        market_commentary=MarketCommentaryScores(equities=2.0),
        goals=[Goal(goal_name="Year-5", time_to_goal_months=60,
                    amount_needed=1_000_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    s3 = state["step3_medium_term"]
    assert len(s3.goals_allocated) == 1
    alloc = s3.goals_allocated[0]
    assert alloc.equity_pct == 50, "year-5 Medium with eq view <= 3 should drop to Low (50/50)"
    assert alloc.debt_pct == 50


def test_a4_equities_view_above_3_no_override():
    """Equities view > 3 -> normal lookup applies."""
    from asset_allocation_pydantic.models import MarketCommentaryScores

    inp = _base_input(
        effective_risk_score=5.5,
        market_commentary=MarketCommentaryScores(equities=5.0),
        goals=[Goal(goal_name="Year-5", time_to_goal_months=60,
                    amount_needed=1_000_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    alloc = state["step3_medium_term"].goals_allocated[0]
    assert alloc.equity_pct == 70, "year-5 Medium with eq view > 3 keeps 70/30"
    assert alloc.debt_pct == 30


def test_a4_year_4_override_applies():
    """Year-4 Medium normally 50/50; eq view <= 3 -> Low (35/65)."""
    from asset_allocation_pydantic.models import MarketCommentaryScores

    inp = _base_input(
        effective_risk_score=5.5,
        market_commentary=MarketCommentaryScores(equities=3.0),  # boundary: <= 3
        goals=[Goal(goal_name="Year-4", time_to_goal_months=48,
                    amount_needed=600_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    alloc = state["step3_medium_term"].goals_allocated[0]
    assert alloc.equity_pct == 35, "year-4 Medium with eq view = 3 drops to Low (35/65)"
    assert alloc.debt_pct == 65
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a4_" -v
```

Expected: `test_a4_equities_view_at_or_below_3_forces_low_column` fails — current code returns 70/30 regardless of market view.

- [ ] **Step 3: Add the override in `step3_medium_term.py`**

In `run()`, after the `eq_pct, dt_pct = MEDIUM_TERM_SPLIT[(horizon, risk_bucket)]` lookup (currently around line 53), add the override:

```diff
     for g in goals_in_bucket:
         horizon = min(
             MEDIUM_TERM_HORIZON_MAX, max(MEDIUM_TERM_HORIZON_MIN, floor(g.time_to_goal_months / 12))
         )
         eq_pct, dt_pct = MEDIUM_TERM_SPLIT[(horizon, risk_bucket)]
+        # A.4: when equities market view is very bearish (<= 3), force the Low
+        # (most conservative) column across all medium-term horizons.
+        if inp.market_commentary.equities <= 3:
+            eq_pct, dt_pct = MEDIUM_TERM_SPLIT[(horizon, "Low")]
         eq_amt = round_to_100(g.amount_needed * eq_pct / 100)
         dt_amt = round_to_100(g.amount_needed * dt_pct / 100)
```

- [ ] **Step 4: Run the A.4 tests — all three should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a4_" -v
```

Expected: 3 passed.

- [ ] **Step 5: Run the full module test suite**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green (default `MarketCommentaryScores.equities = 5.0`, well above 3, so existing tests are unaffected).

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py
git commit -m "feat(asset-allocation): medium-term bearish-equities override forces Low column (A.4)

When market_commentary.equities <= 3 (very bearish equities view), the
per-goal MEDIUM_TERM_SPLIT lookup is overridden to the Low (most
conservative) column regardless of effective_risk_score, across all three
medium-term horizons (years 3, 4, 5). Uses equities view for all three
because the override conceptually answers 'is the market scared of
equities right now'; the literal Excel mapping (year-5->equities /
year-4->debt / year-3->others) reads as a typo per spec discussion.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §A.4"
```

---

### Task 5: A.5 — Risk-band boundary disambiguation (`lower < score ≤ upper`)

**Files:**
- Modify: `AI_Agents/src/asset_allocation_pydantic/tables.py:184` (rename constant)
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py:25-30` (operator flip + import rename)
- Test: `AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

Append to `test_part_a.py`:

```python
def test_a5_risk_4_0_routes_to_low_bucket():
    """Per A.5: risk = 4.0 is now Low (was Medium under score < 4.0)."""
    inp = _base_input(
        effective_risk_score=4.0,
        goals=[Goal(goal_name="Year-5", time_to_goal_months=60,
                    amount_needed=500_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    assert state["step3_medium_term"].risk_bucket == "Low", (
        "risk = 4.0 should route to Low under the new lower-exclusive convention"
    )
    # Year-5 Low: 50/50 split.
    alloc = state["step3_medium_term"].goals_allocated[0]
    assert alloc.equity_pct == 50
    assert alloc.debt_pct == 50


def test_a5_risk_4_001_routes_to_medium():
    """Just above the boundary -> Medium (unchanged behaviour)."""
    inp = _base_input(
        effective_risk_score=4.001,
        goals=[Goal(goal_name="Year-5", time_to_goal_months=60,
                    amount_needed=500_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    assert state["step3_medium_term"].risk_bucket == "Medium"


def test_a5_risk_7_0_routes_to_medium_unchanged():
    """Per A.5: risk = 7.0 stays Medium (Medium upper edge unchanged)."""
    inp = _base_input(
        effective_risk_score=7.0,
        goals=[Goal(goal_name="Year-5", time_to_goal_months=60,
                    amount_needed=500_000, goal_priority="non_negotiable")],
    )
    state, _ = run_allocation_with_state(inp)
    assert state["step3_medium_term"].risk_bucket == "Medium"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a5_" -v
```

Expected: `test_a5_risk_4_0_routes_to_low_bucket` fails — current `_risk_bucket(4.0)` returns "Medium" because the current operator is `score < 4.0` for Low.

- [ ] **Step 3: Rename the constant in `tables.py`**

Replace line 184:

```diff
-MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE: float = 4.0
+MEDIUM_TERM_RISK_LOW_MAX_INCLUSIVE: float = 4.0
```

- [ ] **Step 4: Flip the operator in `step3_medium_term.py:_risk_bucket`**

Update the import on line 18:

```diff
-    MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE,
+    MEDIUM_TERM_RISK_LOW_MAX_INCLUSIVE,
```

Replace lines 25–30:

```diff
 def _risk_bucket(score: float) -> Literal["Low", "Medium", "High"]:
-    if score < MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE:
+    # A.5: lower < score <= upper convention.
+    if score <= MEDIUM_TERM_RISK_LOW_MAX_INCLUSIVE:
         return "Low"
     if score <= MEDIUM_TERM_RISK_MEDIUM_MAX:
         return "Medium"
     return "High"
```

- [ ] **Step 5: Run the A.5 tests — all three should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/test_part_a.py -k "a5_" -v
```

Expected: 3 passed.

- [ ] **Step 6: Run the full module test suite**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green. `test_no_fund_mapping.py` uses risk scores 5.0 and 6.0, both unaffected by the boundary flip at 4.0.

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/asset_allocation_pydantic/tables.py \
        AI_Agents/src/asset_allocation_pydantic/steps/step3_medium_term.py
git commit -m "feat(asset-allocation): risk-band lower-exclusive convention (A.5)

_risk_bucket now uses 'lower < score <= upper' consistently: score = 4.0
maps to Low (was Medium under score < 4.0); score = 7.0 stays Medium
(unchanged). Renames MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE ->
MEDIUM_TERM_RISK_LOW_MAX_INCLUSIVE to reflect the new operator.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §A.5"
```

---

### Task 6: Verification — full asset_allocation suite and quick end-to-end sanity

**Files:**
- (read-only verification task; no edits)

- [ ] **Step 1: Run the full asset_allocation_pydantic test suite**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green — both `test_no_fund_mapping.py` (pre-existing) and `test_part_a.py` (new local file).

- [ ] **Step 2: Run the bridge integration tests for asset_allocation**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
pytest app/services/ai_bridge/asset_allocation/tests/ -v
```

Expected: all green. If a test relies on a goal at month 24 or 60 specifically (boundary edge cases), it may need a one-line fixture update — fix in this same task and commit separately.

- [ ] **Step 3: Run the goal allocation persistence test**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
pytest tests/test_goal_allocation_persistence.py -v
```

Expected: all green. Persisted schema discriminator references `GoalAllocationOutput` class name; that class isn't renamed in this spec (deferred refactor), so this should pass without changes.

- [ ] **Step 4: Verify no lint regressions**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
ruff check AI_Agents/src/asset_allocation_pydantic/
```

Expected: clean.

- [ ] **Step 5: Verify no type regressions**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
pyright AI_Agents/src/asset_allocation_pydantic/
```

Expected: clean (or no new errors vs baseline).

- [ ] **Step 6: If any bridge / persistence test required a fixture update, commit it separately**

```bash
# Only if step 2 or 3 required changes:
git add <updated test files>
git commit -m "test(asset-allocation): update fixtures for v2 bucket boundaries (Part A)

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §A.1"
```

- [ ] **Step 7: Push the branch (if applicable per local workflow)**

(Defer to local Git workflow — no further action specified by this plan.)

---

## Self-review checklist

- ✅ Each spec section A.1–A.5 mapped to exactly one task.
- ✅ Every step shows real code (no `TBD` / `TODO` placeholders).
- ✅ Test code is complete and executable; expected fail/pass outcomes named.
- ✅ Diffs cited against current file line numbers verified during plan-writing.
- ✅ `ST2_LOWER_MONTHS_INCLUSIVE = 24` and `ST2_TAX_THRESHOLD_PCT = 12.5` introduced in Task 3 are used consistently within that task.
- ✅ The `"mixed"` literal added to `Step2Output.asset_subgroup` in Task 3 is referenced consistently in the rewritten `step2_short_term.py`.
- ✅ `MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE → ..._INCLUSIVE` rename in Task 5 lands in both `tables.py` and the `step3_medium_term.py` import.
- ✅ Commits add engine code only — test files in `AI_Agents/src/asset_allocation_pydantic/Testing/` are gitignored and excluded from `git add`.

---

## Done criteria

- All 6 tasks' checkboxes ticked.
- All commits land on the working branch.
- Step 6 verification passes (full module suite + bridge tests + persistence test green; lint + types clean).
- No changes to `AllocationInput` or `GoalAllocationOutput` public contracts.
