# Rebalancing Constraint-Aware Consolidation (F3-B) Implementation Plan — LEAN

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a customer's free-text rebalancing constraint ("fewer funds", "only largecap") reshape the *buy* side of a real engine run and be answered with real numbers, asking any clarifying question exactly once.

**Architecture:** The engine runs **once, unmodified** (real tax-aware sells frozen). A new **pure, deterministic reshape module** (`AI_Agents/src/Rebalancing/consolidation.py`) redistributes only the buy budget across the funds the customer allows — collapse to N funds and/or redeploy into named categories — preserving the total buy and every sell. **No session state** (lean decision 2026-07-10): clarifications ride the conversation history the classifier already reads; each consolidate turn is self-contained; nothing is persisted as a `RebalancingRun`.

**Tech Stack:** Python 3.12, Pydantic v2, LangChain (Haiku classifier), pytest (`asyncio_mode=auto`). Money on Rebalancing engine models is `Decimal`.

## Global Constraints

- **Grounding:** every number comes from the engine or a *deterministic* transform of its real output. The LLM formatter never computes trades. (Spec §3)
- **Buy-side only:** reshape the buy list; the tax-aware sell logic and tax totals are untouched. (Spec §3)
- **Run once:** the engine runs exactly once per turn; constraints are applied *after*, never by re-running or re-targeting the engine. (Spec §5.2)
- **Ephemeral / chat-only:** no `RebalancingRun`/child rows written for a constrained result. Use `compute_rebalancing_result(..., persist=False)`. (Spec §3)
- **Stateless (lean):** NO new DB columns, migrations, `ChatSessionState` usage, or `TurnContext` changes. Clarification is history-based (Spec §5.4); continuity is self-contained turns (Spec §5.5). `_detect_rebal_action` stays a pure read — the speculative-safety invariant holds trivially.
- **Comply, never refuse (v1):** apply the constraint and add a grounded caution via `constraint_impact`; never block. (Spec §5.2)
- **Distribution rule (displaced-budget pro-rata, audit 2026-07-11):** survivors keep their engine-given buy amounts **frozen**; only the dropped funds' budget moves, spread pro-rata to the survivors' amounts (₹30k/₹30k/₹40k, drop the first → +30/70 and +40/70). **Identity when nothing is dropped.** Per-fund caps are not re-imposed (constraint outranks cap); total buy always fully placed, never idle, never outside the constraint. (Spec §5.2)
- **Money types:** Rebalancing engine models are `Decimal`; convert at boundaries. Never mix `float` into engine math.
- **Bundled import convention (critical):** the `Rebalancing` package is on `sys.path` via injection — import it as **top-level** `from Rebalancing.consolidation import ...` / `from Rebalancing.models import ...`, **never** `from AI_Agents.src.Rebalancing...`. App-layer modules that import bundled code must call `ensure_ai_agents_path()` first (see `service.py:33,51`) and tag the import `# type: ignore[import-not-found]  # noqa: E402`. Pure bundled modules (inside `AI_Agents/src/Rebalancing/`) import siblings directly (`from Rebalancing.models import ...`).
- **Bundled test location:** engine unit tests live in `AI_Agents/src/Rebalancing/Testing/` (capital-T `Testing/`, **not** `tests/`); they import `from Rebalancing.X import ...` and reuse `Rebalancing/Testing/conftest.py` (has `make_request`). App-layer tests live under `app/domains/rebalancing/services/rebal_engine/tests/`.
- **Test invocation:** `.venv-mac/bin/python -m pytest <path> -v`. Rebal-engine app-layer tests use the whole-schema sqlite conftest at `app/domains/rebalancing/services/rebal_engine/tests/conftest.py` (ARRAY→JSON swap), *not* the "create only the table under test" generic guidance.
- **Prompt-change gate:** any edit to `_DETECT_REBAL_SYSTEM` or `RebalanceAction` must update golden cases at `app/domains/ai_engine/tests/eval_gate/golden_cases.py` and pass `scripts/run_prompt_eval_gate.sh` in the same commit.
- **Logic-doc rule:** the change is not done until `AI_Agents/Reference_docs/Logics_reference_docs/Rebalancing.md` is updated and version-bumped (currently `Thesis version 1.2`).
- **Commits:** the user commits. Do NOT run `git commit`/`git add` — the "commit" steps below are for the user; leave changes in the working tree, tests green.

---

## File Structure

| File | Responsibility | New/Modify |
|---|---|---|
| `AI_Agents/src/Rebalancing/consolidation.py` | Pure reshape: `ConsolidationConstraints`, `reshape_response()` — survivors frozen, displaced budget pro-rata | **New** |
| `AI_Agents/src/Rebalancing/Testing/test_consolidation.py` | Pure reshape unit tests (`from Rebalancing.consolidation import ...`) | **New** |
| `AI_Agents/src/Rebalancing/Testing/consolidation_helpers.py` | `minimal_response_with_buys()` test builder for `RebalancingComputeResponse` | **New** |
| `app/domains/rebalancing/services/rebal_engine/constraint_impact.py` | `build_constraint_impact()` — target vs unconstrained vs constrained mix + deviations | **New** |
| `app/domains/mutual_funds/services/category_resolver.py` | **Shared** free-text → canonical `sub_category` mapper — a MOVE of ainv's `resolve_category` + new `resolve_categories` wrapper; consumed by rebalancing + additional_investment (+ future chat modules) | **New** |
| `app/domains/additional_investment/services/ainv_engine/category.py` | `resolve_category` delegates to the shared resolver (public API unchanged) | Modify |
| `app/domains/rebalancing/services/rebal_engine/chat.py` | `RebalanceAction` gains `consolidate` mode + fields; `_DETECT_REBAL_SYSTEM` bullet (history-fill guidance, extract customer's words as-is); `consolidate` branch in `handle` (canonicalizes via the shared resolver) | Modify |
| `app/domains/rebalancing/services/rebal_engine/service.py` | `build_rebal_facts_pack` accepts + emits `constraint_impact` | Modify |
| `AI_Agents/Reference_docs/Logics_reference_docs/Rebalancing.md` | Document consolidation; version bump to 1.3 | Modify |
| `AI_Agents/src/chat_eval/questions.yaml` + `questions_consolidation.yaml` | Eval cases incl. Sourbach regression (the loop tripwire) | Modify/New |
| `app/domains/ai_engine/tests/eval_gate/golden_cases.py` | Golden case for the new `consolidate` mode | Modify |

**Deliberately absent (Spec §7 deferrals):** `chat_session_state` column/migration, `consolidation_store`, `turn_context.py` changes, sticky re-apply in narrate/educate. Revive only if the Sourbach regression eval shows the clarify loop surviving.

---

## Task 1: Reshape constraints + pure algorithm

**Files:**
- Create: `AI_Agents/src/Rebalancing/consolidation.py`
- Test: `AI_Agents/src/Rebalancing/Testing/test_consolidation.py`

**Interfaces:**
- Consumes: `RebalancingComputeResponse`, `FundRowAfterStep5` from `AI_Agents/src/Rebalancing/models.py`. Buy amounts live on rows as `pass1_buy_amount` (`Decimal`); identity via `isin`, `recommended_fund`, `sub_category`, `asset_subgroup`, `rank`.
- Produces: `ConsolidationConstraints`, `constraints_active()`, `BuyCandidate`, `compute_reshaped_buys(...) -> dict[str, Decimal]` (isin → new buy). Task 2 adds `reshape_response`.

- [ ] **Step 1: Write the failing test — constraints dataclass + activity check**

```python
# AI_Agents/src/Rebalancing/Testing/test_consolidation.py
from decimal import Decimal
from Rebalancing.consolidation import (
    ConsolidationConstraints, constraints_active,
)

def test_constraints_active_only_when_set():
    assert constraints_active(ConsolidationConstraints(target_fund_count=5))
    assert constraints_active(ConsolidationConstraints(allowed_categories=("largecap",)))
    assert not constraints_active(ConsolidationConstraints())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py::test_constraints_active_only_when_set -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'Rebalancing.consolidation'`

- [ ] **Step 3: Write the constraints dataclass**

```python
# AI_Agents/src/Rebalancing/consolidation.py
"""Deterministic buy-side reshape for constraint-aware consolidation (F3-B).

Pure: operates only on a RebalancingComputeResponse. The engine runs ONCE,
unmodified; this redistributes ONLY the buy budget across the funds the
customer allows, preserving the total buy and every sell. No I/O, no CSV.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ConsolidationConstraints:
    target_fund_count: int | None = None      # max NEW-BUY funds
    allowed_categories: tuple[str, ...] | None = None  # redeploy whole budget here
    # NO reset flag: stateless design — "back to the full plan" is narrate mode.


def constraints_active(c: ConsolidationConstraints) -> bool:
    return c.target_fund_count is not None or bool(c.allowed_categories)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py::test_constraints_active_only_when_set -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — displaced-budget pro-rata + identity**

```python
# append to test_consolidation.py
from Rebalancing.consolidation import compute_reshaped_buys, BuyCandidate

def _cand(isin, sg, rank, buy):
    return BuyCandidate(isin=isin, recommended_fund=isin, sub_category=sg,
                        asset_subgroup=sg, rank=rank, buy_inr=Decimal(buy))

def test_displaced_budget_spreads_pro_rata():
    # The user's canonical example: A 30k, B 30k, C 40k → drop A (N=2, A is the
    # smallest of the lowest rank) → A's 30k splits 30/70 to B, 40/70 to C.
    cands = [
        _cand("A", "gold",     2, 30000),   # rank 2 → dropped first
        _cand("B", "hybrid",   1, 30000),
        _cand("C", "largecap", 1, 40000),
    ]
    out = compute_reshaped_buys(
        cands, ConsolidationConstraints(target_fund_count=2),
        rounding_multiple=100,
    )
    assert out["A"] == Decimal(0)
    assert out["B"] == Decimal(42900)   # 30000 + 30000*30/70 = 42857.14 → 42900 (residual rule)
    assert out["C"] == Decimal(57100)   # 40000 + 30000*40/70 = 57142.86 → 57100 rounded
    assert sum(out.values()) == Decimal(100000)          # total preserved exactly

def test_identity_when_nothing_dropped():
    cands = [_cand("A", "largecap", 1, 33300), _cand("B", "hybrid", 1, 33300)]
    out = compute_reshaped_buys(
        cands, ConsolidationConstraints(target_fund_count=2), rounding_multiple=100)
    assert out == {"A": Decimal(33300), "B": Decimal(33300)}   # NOTHING moves
```

Note: pin the exact rounded per-fund values while writing the test (compute by hand with the residual-to-largest rule); the assertions above show the intent — survivors' own amounts frozen, displacement split 30/70–40/70, total exact.

- [ ] **Step 6: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py::test_displaced_budget_spreads_pro_rata -v`
Expected: FAIL — `ImportError: cannot import name 'compute_reshaped_buys'`

- [ ] **Step 7: Implement `BuyCandidate` + `compute_reshaped_buys`**

```python
# add to consolidation.py
from typing import Iterable

_ONE = Decimal("1")


@dataclass(frozen=True)
class BuyCandidate:
    isin: str
    recommended_fund: str
    sub_category: str | None
    asset_subgroup: str
    rank: int
    buy_inr: Decimal


def _round_to_multiple(x: Decimal, multiple: int) -> Decimal:
    if multiple <= 0:
        return x
    m = Decimal(multiple)
    return (x / m).quantize(_ONE, rounding="ROUND_HALF_UP") * m


def compute_reshaped_buys(
    candidates: Iterable[BuyCandidate],
    constraints: ConsolidationConstraints,
    *,
    rounding_multiple: int = 100,
) -> dict[str, Decimal]:
    """Displaced-budget pro-rata reshape.

    Survivors keep their engine-given buy amounts FROZEN; only the dropped
    funds' combined budget moves, spread pro-rata to the survivors' amounts.
    Identity when nothing is dropped. Total preserved exactly; rounding
    residual onto the largest surviving buy. Returns isin -> new buy (Decimal).
    """
    cands = list(candidates)
    total = sum((c.buy_inr for c in cands), Decimal(0))
    if total <= 0 or not cands:
        return {c.isin: Decimal(0) for c in cands}

    # 1. Survivors: filter to allowed categories (match on sub_category — the
    #    SEBI-name vocabulary resolve_category emits; asset_subgroup is too
    #    coarse, e.g. multi_asset holds ten sub_categories), then keep top-N
    #    (rank asc, larger buy first).
    if constraints.allowed_categories:
        allowed = set(constraints.allowed_categories)
        eligible = [c for c in cands if c.sub_category in allowed]
        if not eligible:                       # honest no-op; caller surfaces error
            return {c.isin: Decimal(0) for c in cands}
    else:
        eligible = cands
    ordered = sorted(eligible, key=lambda c: (c.rank, -c.buy_inr))
    keep = (ordered[: max(1, constraints.target_fund_count)]
            if constraints.target_fund_count is not None else ordered)

    # 2. Identity fast-path: nothing dropped → nothing moves.
    if len(keep) == len(cands):
        return {c.isin: c.buy_inr for c in cands}

    # 3. Displaced budget = everything not surviving; spread pro-rata to the
    #    survivors' own (frozen) amounts. Caps deliberately not re-imposed.
    kept_isins = {c.isin for c in keep}
    kept_total = sum((c.buy_inr for c in keep), Decimal(0))
    displaced = total - kept_total

    out: dict[str, Decimal] = {c.isin: Decimal(0) for c in cands}
    for c in keep:
        share = displaced * c.buy_inr / kept_total if kept_total > 0 else (
            displaced / Decimal(len(keep)))
        out[c.isin] = c.buy_inr + _round_to_multiple(share, rounding_multiple)

    # 4. Preserve total exactly: residual onto the largest surviving buy.
    placed = sum(out.values(), Decimal(0))
    residual = total - placed
    if residual != 0:
        biggest = max(keep, key=lambda c: out[c.isin])
        out[biggest.isin] += residual
    return out
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py -v`
Expected: PASS (both tests)

- [ ] **Step 9: Write the failing test — allowed_categories redeploys whole budget, none outside**

```python
# append to test_consolidation.py
def test_allowed_categories_redeploys_whole_budget():
    # allowed_categories match on sub_category — use REAL ranking sub_category
    # names (the vocabulary resolve_category emits), not invented keys.
    cands = [
        _cand("A", "Large Cap Fund", 1, 40000),
        _cand("B", "Mid Cap Fund",   1, 30000),
        _cand("C", "Gold ETF",       1, 30000),
    ]
    out = compute_reshaped_buys(
        cands, ConsolidationConstraints(allowed_categories=("Large Cap Fund",)),
        rounding_multiple=100,
    )
    assert out["A"] == Decimal(100000)     # 40k own + 60k displaced → whole ₹1L
    assert out["B"] == Decimal(0)
    assert out["C"] == Decimal(0)
```

- [ ] **Step 10: Run to verify pass** (implementation already covers it)

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py::test_allowed_categories_redeploys_whole_budget -v`
Expected: PASS

- [ ] **Step 11: Commit** (user runs)

```bash
# USER runs — do not run automatically
git add AI_Agents/src/Rebalancing/consolidation.py AI_Agents/src/Rebalancing/Testing/test_consolidation.py
git commit -m "feat(rebal): pure buy-reshape algorithm for consolidation"
```

---

## Task 2: Apply reshape onto the engine response

**Files:**
- Modify: `AI_Agents/src/Rebalancing/consolidation.py`
- Create: `AI_Agents/src/Rebalancing/Testing/consolidation_helpers.py`
- Test: `AI_Agents/src/Rebalancing/Testing/test_consolidation.py`

**Interfaces:**
- Consumes: Task 1 `compute_reshaped_buys`; `RebalancingComputeResponse.rows` (`list[FundRowAfterStep5]`), `.subgroups[].actions` (`list[FundRowAfterStep5]`). Row fields: `pass1_buy_amount` (Decimal), `isin`, `rank`, `asset_subgroup`. (No PAA-weight dependency — pro-rata rule.)
- Produces: `reshape_response(response, constraints) -> tuple[RebalancingComputeResponse, str | None]` — deep-copied response with buys rewritten; 2nd element is an error code (`"category_not_in_plan"`) or `None`. Test helper `minimal_response_with_buys(buys, sells)`.

- [ ] **Step 1: Write the failing test — reshape rewrites buys on a minimal response, sells untouched**

```python
# append to test_consolidation.py
from Rebalancing.Testing.consolidation_helpers import minimal_response_with_buys
from Rebalancing.consolidation import reshape_response

def test_reshape_response_collapses_buys_keeps_sells():
    resp = minimal_response_with_buys(
        buys=[("A", "largecap", 1, 33300), ("B", "hybrid", 1, 33300),
              ("C", "gold", 1, 3000)],
        sells=[("Z", "focused", 172000)],
    )
    original_sell_total = resp.totals.total_sell_inr
    out, err = reshape_response(resp, ConsolidationConstraints(target_fund_count=2))
    assert err is None
    buys = {r.isin: r.pass1_buy_amount for r in out.rows if r.pass1_buy_amount > 0}
    assert len(buys) == 2
    assert sum(buys.values()) == Decimal(69600)
    assert out.totals.total_sell_inr == original_sell_total   # sells frozen
```

- [ ] **Step 2: Add the test helper**

```python
# AI_Agents/src/Rebalancing/Testing/consolidation_helpers.py  (new)
from decimal import Decimal
from Rebalancing.models import (
    RebalancingComputeResponse, SubgroupSummary, RebalancingTotals,
    RebalancingRunMetadata, FundRowAfterStep5, PracticalAllocationOutput,
)

def _row(isin, sg, rank, buy=0, sell=0):
    # Only the fields the reshape + facts pack read; complete required fields
    # per models.py:30-125 (FundRowInput → Step5 chain).
    return FundRowAfterStep5(
        asset_subgroup=sg, sub_category=sg, recommended_fund=isin, isin=isin,
        rank=rank, is_recommended=True,
        target_amount_pre_cap=Decimal(buy), present_allocation_inr=Decimal(0),
        pass1_buy_amount=Decimal(buy), pass1_sell_amount=Decimal(sell),
        final_holding_amount=Decimal(buy),
    )
# minimal_response_with_buys(buys, sells) assembles rows + one SubgroupSummary per
# subgroup (with total_buy_inr) + RebalancingTotals + metadata + practical_allocation
# + a trade_list entry per buy (action="BUY", amount_inr) and per sell (action="SELL")
# — trade_list MUST be populated so the consistency-invariant test is meaningful.
# Read models.py:30-290 and Rebalancing/Testing/conftest.py (make_request) to
# satisfy every required field without validation errors.
```

Complete the helper against the real model definitions (`AI_Agents/src/Rebalancing/models.py:30-290`); reuse patterns from `Rebalancing/Testing/conftest.py`'s `make_request`.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py::test_reshape_response_collapses_buys_keeps_sells -v`
Expected: FAIL — `ImportError: cannot import name 'reshape_response'`

- [ ] **Step 4: Implement `reshape_response`**

```python
# add to consolidation.py
import copy
from Rebalancing.models import RebalancingComputeResponse


def reshape_response(
    response: RebalancingComputeResponse,
    constraints: ConsolidationConstraints,
    *,
    rounding_multiple: int = 100,
) -> tuple[RebalancingComputeResponse, str | None]:
    if not constraints_active(constraints):
        return response, None

    candidates = [
        BuyCandidate(
            isin=r.isin or r.recommended_fund or "",
            recommended_fund=r.recommended_fund or "",
            sub_category=r.sub_category,
            asset_subgroup=r.asset_subgroup,
            rank=int(getattr(r, "rank", 0) or 0),
            buy_inr=Decimal(getattr(r, "pass1_buy_amount", 0) or 0),
        )
        for r in response.rows
        if Decimal(getattr(r, "pass1_buy_amount", 0) or 0) > 0
    ]
    if constraints.allowed_categories:
        present = {c.sub_category for c in candidates}
        if not (present & set(constraints.allowed_categories)):
            return response, "category_not_in_plan"

    new_buys = compute_reshaped_buys(
        candidates, constraints, rounding_multiple=rounding_multiple,
    )

    out = copy.deepcopy(response)
    for r in out.rows:
        key = r.isin or r.recommended_fund or ""
        if key in new_buys:
            r.pass1_buy_amount = new_buys[key]
    for sg in out.subgroups:
        sg_buy = Decimal(0)
        for a in sg.actions:
            key = a.isin or a.recommended_fund or ""
            if key in new_buys:
                a.pass1_buy_amount = new_buys[key]
            sg_buy += Decimal(getattr(a, "pass1_buy_amount", 0) or 0)
        sg.total_buy_inr = sg_buy                      # per-subgroup total stays true

    # trade_list carries the SAME buys a third time — rewrite it too, or a
    # downstream reader (e.g. the deterministic fallback brief) narrates the
    # old, un-consolidated buys. SELL/EXIT entries untouched.
    new_trades = []
    for t in out.trade_list:
        if t.action == "BUY":
            key = t.isin or t.recommended_fund or ""
            if key in new_buys:
                if new_buys[key] <= 0:
                    continue                           # dropped fund → drop trade
                t.amount_inr = new_buys[key]
        new_trades.append(t)
    out.trade_list = new_trades

    out.totals.funds_to_buy_count = sum(1 for v in new_buys.values() if v > 0)
    return out, None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py -v`
Expected: PASS (all)

- [ ] **Step 6: Verify grounding — identity when no constraint + response consistency invariant**

```python
# append
def test_reshape_noop_without_constraints():
    resp = minimal_response_with_buys(
        buys=[("A", "largecap", 1, 100)], sells=[])
    out, err = reshape_response(resp, ConsolidationConstraints())
    assert err is None
    assert out is resp   # identity: no copy, no change

def test_reshape_keeps_all_buy_representations_in_agreement():
    # the helper must populate trade_list (BUY per buy row, SELL per sell row)
    resp = minimal_response_with_buys(
        buys=[("A", "Large Cap Fund", 1, 33300), ("B", "Mid Cap Fund", 1, 33300),
              ("C", "Gold ETF", 1, 3000)],
        sells=[("Z", "Focused Fund", 172000)],
    )
    out, err = reshape_response(resp, ConsolidationConstraints(target_fund_count=2))
    assert err is None
    rows_total = sum(r.pass1_buy_amount for r in out.rows)
    trades_total = sum(t.amount_inr for t in out.trade_list if t.action == "BUY")
    sg_total = sum(sg.total_buy_inr for sg in out.subgroups)
    assert rows_total == trades_total == sg_total == Decimal(69600)
    assert out.totals.funds_to_buy_count == 2
    assert sum(1 for t in out.trade_list if t.action == "BUY") == 2  # dropped trade gone
    assert any(t.action != "BUY" for t in out.trade_list)           # sells untouched
```

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py -v` → PASS

- [ ] **Step 7: Commit** (user)

```bash
git add AI_Agents/src/Rebalancing/consolidation.py AI_Agents/src/Rebalancing/Testing/
git commit -m "feat(rebal): apply buy-reshape onto engine response (sells frozen)"
```

---

## Task 3: `constraint_impact` — comply-and-caution numbers

**Files:**
- Create: `app/domains/rebalancing/services/rebal_engine/constraint_impact.py`
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_constraint_impact.py`

**Interfaces:**
- Consumes: original + reshaped `RebalancingComputeResponse`; the asset-class rollup `build_rebal_facts_pack` already computes (`asset_class_mix_pct`, `service.py:444-467`). PAA ideal mix from `response.practical_allocation`. `risk_profile: str | None`.
- Produces: `build_constraint_impact(original, reshaped, *, risk_profile) -> dict` with keys `target_mix_pct`, `unconstrained_mix_pct`, `constrained_mix_pct`, `largest_deviations` (list of `[name, delta_pct]`), `risk_profile`.

- [ ] **Step 1: Write the failing test**

```python
# test_constraint_impact.py
from app.domains.rebalancing.services.rebal_engine.constraint_impact import (
    build_constraint_impact,
)
from Rebalancing.Testing.consolidation_helpers import minimal_response_with_buys
from Rebalancing.consolidation import reshape_response, ConsolidationConstraints

def test_constraint_impact_reports_deviation():
    original = minimal_response_with_buys(
        buys=[("A", "Large Cap Fund", 1, 50000), ("B", "Short Duration Fund", 1, 50000)],
        sells=[])
    reshaped, _ = reshape_response(
        original, ConsolidationConstraints(allowed_categories=("Large Cap Fund",)))
    impact = build_constraint_impact(original, reshaped, risk_profile="Moderate")
    assert impact["risk_profile"] == "Moderate"
    assert any(abs(d[1]) > 0 for d in impact["largest_deviations"])
    # the fine lens: buy composition shows the shift even when asset-class is flat
    mix = impact["buy_mix_by_category"]
    assert mix["unconstrained"]["Large Cap Fund"] == 50.0
    assert mix["constrained"]["Large Cap Fund"] == 100.0
    assert sum(mix["constrained"].values()) == 100.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_constraint_impact.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `build_constraint_impact`**

```python
# constraint_impact.py
from __future__ import annotations
from typing import Any


def _planned_mix_pct(response) -> dict[str, float]:
    """Asset-class mix of the PLANNED final holdings, as pct — reuse the same
    rollup build_rebal_facts_pack produces (asset_class_mix_pct)."""
    from app.domains.rebalancing.services.rebal_engine.service import (
        build_rebal_facts_pack,
    )
    return build_rebal_facts_pack(response).get("asset_class_mix_pct", {})


def _target_mix_pct(response) -> dict[str, float]:
    # NOTE (implementer): the getattr fallbacks below are placeholders — open
    # AI_Agents/src/practical_asset_allocation models (PracticalAllocationOutput)
    # and use the REAL field names for subgroup name + target weight before
    # writing this. Do not ship the guessing.
    out: dict[str, float] = {}
    for sg in getattr(response.practical_allocation, "subgroups", []) or []:
        name = getattr(sg, "asset_subgroup", None) or getattr(sg, "name", None)
        w = getattr(sg, "target_pct", None) or getattr(sg, "weight_pct", None)
        if name is not None and w is not None:
            out[str(name)] = round(float(w), 1)
    return out


def _buy_mix_by_category(response) -> dict[str, float]:
    """% of the BUY budget per sub_category — the lens at which the
    constraints actually act (asset-class mixes can be flat for
    intra-equity asks; this never is)."""
    from decimal import Decimal
    by_cat: dict[str, Decimal] = {}
    total = Decimal(0)
    for r in response.rows:
        buy = Decimal(getattr(r, "pass1_buy_amount", 0) or 0)
        if buy > 0:
            cat = r.sub_category or r.asset_subgroup
            by_cat[cat] = by_cat.get(cat, Decimal(0)) + buy
            total += buy
    if total <= 0:
        return {}
    return {k: round(float(v / total * 100), 1) for k, v in by_cat.items()}


def build_constraint_impact(original, reshaped, *, risk_profile: str | None) -> dict[str, Any]:
    target = _target_mix_pct(original)
    unconstrained = _planned_mix_pct(original)
    constrained = _planned_mix_pct(reshaped)
    keys = set(target) | set(unconstrained) | set(constrained)
    deviations = sorted(
        ([k, round(constrained.get(k, 0.0) - target.get(k, 0.0), 1)] for k in keys),
        key=lambda kv: abs(kv[1]), reverse=True,
    )[:5]
    return {
        "target_mix_pct": target,
        "unconstrained_mix_pct": unconstrained,
        "constrained_mix_pct": constrained,
        "largest_deviations": deviations,
        "buy_mix_by_category": {
            "unconstrained": _buy_mix_by_category(original),
            "constrained": _buy_mix_by_category(reshaped),
        },
        "risk_profile": risk_profile,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_constraint_impact.py -v`
Expected: PASS

- [ ] **Step 5: Commit** (user)

```bash
git add app/domains/rebalancing/services/rebal_engine/constraint_impact.py app/domains/rebalancing/services/rebal_engine/tests/test_constraint_impact.py
git commit -m "feat(rebal): constraint_impact deviation block for comply-and-caution"
```

---

## Task 4: Shared category resolver (free text → canonical `sub_category`) — a MOVE

**Verified 2026-07-11 (design audit):** `resolve_category` (`app/domains/additional_investment/services/ainv_engine/category.py:70`) already maps free text → canonical ranking **`sub_category`** values ("Large Cap Fund", "Mid Cap Fund", …) via a production-tested synonym table (`_CATEGORY_SYNONYMS`, :21-53) + live `_ranking_categories()`. The reshape matches on **`sub_category`** (NOT `asset_subgroup` — too coarse: `multi_asset` holds ten sub_categories, so subgroup-level matching would turn "only flexicap" into the whole hybrid bucket). Therefore this task is a **move + thin wrapper**, not new mapping logic.

**Files:**
- Create: `app/domains/mutual_funds/services/category_resolver.py` (logic moved from ainv's `category.py`: `_CATEGORY_SYNONYMS`, `_ranking_categories`, `resolve_category`; plus new `resolve_categories` list wrapper)
- Modify: `app/domains/additional_investment/services/ainv_engine/category.py` (delete the moved code; import + re-export `resolve_category` from the shared module — public API unchanged, all other helpers stay)
- Test: `app/domains/mutual_funds/tests/test_category_resolver.py`

**Interfaces:**
- Produces: `resolve_category(text) -> str | None` (moved, behavior identical) and `resolve_categories(texts: list[str]) -> tuple[list[str], list[str]]` — (canonical `sub_category` values, unresolved words). Pure, deterministic, no LLM, no DB.
- One mapping for every chat module (same pattern as `scheme_classification.py` — taxonomy lives in `mutual_funds`). Note the moved module keeps importing `get_fund_ranking` from `rebal_engine/fund_rank` (as `category.py` does today).

- [ ] **Step 1: Failing test**

```python
# test_category_resolver.py — real ranking sub_category names, verified 2026-07-11.
from app.domains.mutual_funds.services.category_resolver import (
    resolve_categories, resolve_category,
)

def test_resolves_common_phrasings_to_canonical_sub_categories():
    resolved, unresolved = resolve_categories(["large cap", "midcap"])
    assert resolved == ["Large Cap Fund", "Mid Cap Fund"]
    assert unresolved == []

def test_unknown_word_reported_not_guessed():
    resolved, unresolved = resolve_categories(["crypto"])
    assert resolved == [] and unresolved == ["crypto"]

def test_single_resolve_matches_moved_behavior():
    assert resolve_category("bluechip") == "Large Cap Fund"
```

- [ ] **Step 2: Run to verify fail** → module not found.

- [ ] **Step 3: Move the logic + add the wrapper.** Create the shared module by moving `_CATEGORY_SYNONYMS`, `_ranking_categories`, `resolve_category` verbatim from `category.py`; add:

```python
def resolve_categories(texts: list[str]) -> tuple[list[str], list[str]]:
    resolved: list[str] = []
    unresolved: list[str] = []
    for t in texts:
        hit = resolve_category(t)
        if hit is not None and hit not in resolved:
            resolved.append(hit)
        elif hit is None:
            unresolved.append(t)
    return resolved, unresolved
```

In ainv's `category.py`, replace the moved code with `from app.domains.mutual_funds.services.category_resolver import resolve_category  # re-export` (its other helpers — `top_funds_for_category`, `category_subgroup`, `category_status` — stay put).

- [ ] **Step 4: Run to verify pass + no ainv regression**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/tests/test_category_resolver.py app/domains/additional_investment/ -v`
Expected: PASS, including all existing additional_investment tests untouched (the move must be behavior-preserving).

- [ ] **Step 5: Commit** (user)

```bash
git add app/domains/mutual_funds/services/category_resolver.py app/domains/mutual_funds/tests/test_category_resolver.py app/domains/additional_investment/services/ainv_engine/category.py
git commit -m "refactor(mf): move category resolver to mutual_funds as the shared mapping"
```

---

## Task 5: `RebalanceAction` gains `consolidate` + history-fill extraction

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (`RebalanceAction` :49-67, `_DETECT_REBAL_SYSTEM` :83-265)
- Modify: `app/domains/ai_engine/tests/eval_gate/golden_cases.py`
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_detect_consolidate.py`

**Interfaces:**
- Produces: `RebalanceAction.mode` includes `"consolidate"`; new fields `target_fund_count: int | None`, `allowed_categories: list[str] | None`. (No reset field — "back to the full plan" routes to the existing `narrate` mode.)
- **No code change to `_detect_rebal_action`'s user block** — `history_section` (from `build_detect_history_block`) is already in it; the prompt bullet does the history-fill work. `_detect_rebal_action` stays a pure read (stateless constraint).

- [ ] **Step 1: Add the mode + fields**

```python
# chat.py RebalanceAction (:49-67) — extend Literal and add fields
    mode: Literal[
        "narrate", "educate", "counterfactual_explore",
        "recompute", "clarify", "redirect", "consolidate",
    ]
    target_fund_count: Optional[int] = Field(default=None,
        description="For consolidate. Max number of NEW-BUY funds (not portfolio total).")
    allowed_categories: Optional[list[str]] = Field(default=None,
        description="For consolidate. Redeploy the whole buy budget into only these categories.")
```

- [ ] **Step 2: Add the `consolidate` bullet to `_DETECT_REBAL_SYSTEM`**

Add alongside the existing mode bullets (`chat.py:83-265`), covering:
(a) "fewer trades / consolidate / max N funds" → `consolidate` + `target_fund_count`;
(b) "only largecap / only midcap+smallcap" → `consolidate` + `allowed_categories` — extract **the customer's words as-is** ("large cap", "bluechip"); do NOT ask the model to guess internal keys (the handler canonicalizes via the Task 4 shared resolver);
(c) "go back to the full plan / show the original plan / remove that" → the existing `narrate` mode (NOT consolidate — there is no stored constraint to remove);
(d) **history-fill rule:** "When the recent conversation shows the assistant just asked how many funds (or which categories) and the customer's message supplies it (e.g. '5 funds', 'largecap only'), emit `consolidate` with that field filled — do NOT ask again.";
(e) **N-total disambiguation:** "'exactly N funds *total* for my whole portfolio' is NOT supported — still emit `consolidate` with `target_fund_count=N` (the handler narrates the buys-only honesty note)."
If the extra fields risk truncation, raise `max_tokens=300` → `400` in the `classify_action` call (`chat.py:641-646`).

- [ ] **Step 3: Wiring unit test (mocked LLM)**

```python
# test_detect_consolidate.py — patch classify_action (the symbol imported in
# chat.py) with an AsyncMock returning
# RebalanceAction(mode="consolidate", target_fund_count=5); call
# _detect_rebal_action(last_run, ctx) with a minimal AgentRunRecord + TurnContext
# (mirror test_input_builder.py's _ctx_for); assert the returned action has
# mode == "consolidate" and target_fund_count == 5, and that classify_action was
# called with system_prompt=_DETECT_REBAL_SYSTEM.
```

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_detect_consolidate.py -v` → PASS.

- [ ] **Step 4: Add a golden case + run the prompt eval gate**

Add to `app/domains/ai_engine/tests/eval_gate/golden_cases.py`: input "reduce my trades, keep it to 5 funds" → expect mode `consolidate`, `target_fund_count=5`. Then:

Run: `scripts/run_prompt_eval_gate.sh`
Expected: PASS (live LLM). If mis-routed, tighten the bullet until green — REQUIRED before the prompt change merges.

- [ ] **Step 5: Commit** (user)

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py app/domains/ai_engine/tests/eval_gate/golden_cases.py app/domains/rebalancing/services/rebal_engine/tests/test_detect_consolidate.py
git commit -m "feat(rebal): consolidate action mode with history-fill extraction"
```

---

## Task 6: `constraint_impact` into the facts pack + formatter caution

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/service.py` (`build_rebal_facts_pack` :209-474)
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (`_REBAL_FORMATTER_BODY` :165-265)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_facts_pack_constraint.py`

**Interfaces:**
- Produces: `build_rebal_facts_pack(response, *, goal_buckets=None, constraint_impact=None)` emits a top-level `constraint_impact` key when provided (backward-compatible keyword-only param — zero impact on existing callers).

- [ ] **Step 1: Failing test**

```python
# test_facts_pack_constraint.py
from app.domains.rebalancing.services.rebal_engine.service import build_rebal_facts_pack
from Rebalancing.Testing.consolidation_helpers import minimal_response_with_buys

def test_facts_pack_carries_constraint_impact():
    resp = minimal_response_with_buys(buys=[("A", "largecap", 1, 100)], sells=[])
    pack = build_rebal_facts_pack(resp, constraint_impact={"risk_profile": "Moderate"})
    assert pack["constraint_impact"] == {"risk_profile": "Moderate"}
    pack2 = build_rebal_facts_pack(resp)
    assert "constraint_impact" not in pack2
```

- [ ] **Step 2: Run to verify fail** → `TypeError: unexpected keyword argument 'constraint_impact'`.

- [ ] **Step 3: Add the param + conditional key**

```python
# service.py build_rebal_facts_pack signature (:209-213)
def build_rebal_facts_pack(
    response, *, goal_buckets=None, constraint_impact=None,
):
```

```python
# in the conditional-key block (:468-473), same pattern as goal_buckets
    if constraint_impact is not None:
        pack["constraint_impact"] = constraint_impact
```

- [ ] **Step 4: Run to verify pass** → PASS. Also run the existing service tests: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_service.py -v` (no regression).

- [ ] **Step 5: Add the comply-and-caution instruction to `_REBAL_FORMATTER_BODY`**

In the FACTS_PACK-field docs (`chat.py:168-232`) add a `constraint_impact` description (including `buy_mix_by_category`); in the per-mode section (`chat.py:234-265`) add a `consolidate` ACTION_MODE paragraph: *"FIRST confirm you did exactly what they asked (the reshaped buys), THEN add one plain caution — pick the lens that actually moved: if `largest_deviations` shows a real asset-class shift, use it ('this moves you ~X% further from your target debt allocation'); if the asset-class mix is flat, use `buy_mix_by_category` ('your new investments now go 100% into large-cap, where the plan spread them across N categories'). Never refuse. Never invent a percentage not in the block. Sells and tax are unchanged from the plan — say so if asked."*

- [ ] **Step 6: Commit** (user)

```bash
git add app/domains/rebalancing/services/rebal_engine/service.py app/domains/rebalancing/services/rebal_engine/chat.py app/domains/rebalancing/services/rebal_engine/tests/test_facts_pack_constraint.py
git commit -m "feat(rebal): constraint_impact in facts pack + caution narration"
```

---

## Task 7: `consolidate` handler branch (stateless)

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (`handle` :353-480)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_handle_consolidate.py`

**Interfaces:**
- Consumes: `reshape_response`, `ConsolidationConstraints`, `constraints_active` (Tasks 1-2, imported top-level from `Rebalancing.consolidation` after `ensure_ai_agents_path()` — already called at chat.py import time via service.py); `build_constraint_impact` (Task 3); `resolve_categories` (Task 4); `build_rebal_facts_pack(constraint_impact=...)` (Task 6); `compute_rebalancing_result(persist=False)` (exists); `_counterfactual_explore` (`chat.py:493-549`) as the non-persisting template.
- Produces: `_consolidate(ctx, action, last_run) -> ChatHandlerResult`; dispatch line in `handle`.

- [ ] **Step 1: Failing test — completed consolidate reshapes and persists nothing**

```python
# test_handle_consolidate.py — sketch (wire against real conftest fixtures):
# 1. Build a TurnContext via the _ctx_for pattern (test_input_builder.py:11) with
#    last_agent_runs={"rebalancing": AgentRunRecord(...)} so handle() takes the
#    follow-up path.
# 2. Patch _detect_rebal_action / consume_speculative_detect to return
#    RebalanceAction(mode="consolidate", target_fund_count=2).
# 3. Patch compute_rebalancing_result with AsyncMock returning a
#    RebalancingRunOutcome wrapping minimal_response_with_buys(3 buys, 1 sell)
#    (see test_service.py:23-75 _build_min_response pattern).
# 4. Patch format_with_telemetry to capture the facts_pack it receives.
# Assert:
#  - compute_rebalancing_result was called with persist=False
#  - captured facts_pack has exactly 2 funds with buy_inr > 0 and a
#    constraint_impact key
#  - sells in the facts pack equal the original sell amounts
#  - result.rebalancing_recommendation_id is None (nothing persisted)
```

- [ ] **Step 2: Run to verify fail** (no consolidate branch → falls through to narrate).

- [ ] **Step 3: Implement the branch + helper**

Dispatch, added after the `counterfactual_explore` check (~`chat.py:417`):

```python
    if action.mode == "consolidate":
        return await _consolidate(ctx, action)
```

Handler, modeled on `_counterfactual_explore` (`chat.py:493-549`):

```python
async def _consolidate(ctx: TurnContext, action: RebalanceAction) -> ChatHandlerResult:
    from Rebalancing.consolidation import (  # type: ignore[import-not-found]
        ConsolidationConstraints, constraints_active, reshape_response,
    )
    from app.domains.rebalancing.services.rebal_engine.constraint_impact import (
        build_constraint_impact,
    )

    # canonicalize the customer's category words via the SHARED resolver (Task 4)
    allowed: tuple[str, ...] | None = None
    if action.allowed_categories:
        from app.domains.mutual_funds.services.category_resolver import (
            resolve_categories,
        )
        resolved, unresolved = resolve_categories(action.allowed_categories)
        if unresolved and not resolved:
            return ChatHandlerResult(
                text=(f"I couldn't match {', '.join(unresolved)} to a fund "
                      "category we invest in. Did you mean large-cap, mid-cap, "
                      "small-cap, hybrid, gold, or overseas equity?"),
                snapshot_id=None, rebalancing_recommendation_id=None,
            )
        allowed = tuple(resolved) if resolved else None

    constraints = ConsolidationConstraints(
        target_fund_count=action.target_fund_count,
        allowed_categories=allowed,
    )

    # incomplete ask ("fewer funds", no count) → ask ONCE, stateless
    if not constraints_active(constraints):
        return ChatHandlerResult(
            text=_CONSOLIDATE_CLARIFY,
            snapshot_id=None, rebalancing_recommendation_id=None,
        )

    # run engine ONCE (no persist), reshape, narrate
    outcome = await compute_rebalancing_result(
        user=ctx.user_ctx, user_question=ctx.user_question, db=ctx.db,
        acting_user_id=ctx.effective_user_id, chat_session_id=ctx.session_id,
        persist=False, chat_ctx=ctx,
    )
    if outcome.response is None:
        return ChatHandlerResult(text=_NARRATE_DEGRADED_FALLBACK,
                                 snapshot_id=None, rebalancing_recommendation_id=None)

    reshaped, err = reshape_response(outcome.response, constraints)
    if err == "category_not_in_plan":
        cats = ", ".join(action.allowed_categories or [])
        return ChatHandlerResult(
            text=(f"None of the funds in your current plan fall under {cats}, "
                  "so I can't redirect the buys there. Want to see the plan as is, "
                  "or pick a different category?"),
            snapshot_id=None, rebalancing_recommendation_id=None)

    impact = build_constraint_impact(
        outcome.response, reshaped,
        risk_profile=getattr(ctx.user_ctx, "risk_profile", None),
    )
    text = await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_rebal_facts_pack(reshaped, goal_buckets=outcome.goal_buckets,
                                          constraint_impact=impact),
        body_prompt=_REBAL_FORMATTER_BODY, module_name="rebalancing",
        action_mode="consolidate",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: outcome.formatted_text or "",
    )
    return ChatHandlerResult(text=text, snapshot_id=None,
                             rebalancing_recommendation_id=None)
```

Module constant:

```python
_CONSOLIDATE_CLARIFY = (
    "Happy to consolidate. How many funds would you like the new "
    "investments spread across — for example, up to 3 or up to 5?"
)
```

(No narrate-path refactor needed — the reset branch was cut with `reset_constraints`; "back to the full plan" never reaches `_consolidate`, it routes to the existing narrate fall-through.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_handle_consolidate.py -v` → PASS.

- [ ] **Step 5: Stateless-clarify test**

```python
# append: drive action=consolidate with no constraint fields set;
# assert the reply text is _CONSOLIDATE_CLARIFY, compute_rebalancing_result was
# NOT called, and no DB rows of any kind were written.
```

Run + PASS. Then run the whole suite: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/ -v` — no regression.

- [ ] **Step 6: Commit** (user)

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py app/domains/rebalancing/services/rebal_engine/tests/test_handle_consolidate.py
git commit -m "feat(rebal): stateless consolidate handler branch"
```

---

## Task 8: Logic doc + eval questions + Sourbach regression (the loop tripwire)

**Files:**
- Modify: `AI_Agents/Reference_docs/Logics_reference_docs/Rebalancing.md`
- Create: `AI_Agents/src/chat_eval/questions_consolidation.yaml`
- Modify: `AI_Agents/src/chat_eval/questions.yaml`

- [ ] **Step 1: Update + version-bump the logic doc**

Add a "Constraint-aware consolidation" section: run-once + deterministic buy-reshape; buy-side only (sells/tax frozen); `target_fund_count` = new-buy count, not portfolio total; `allowed_categories` = redeploy whole budget (canonical sub_category, category must be in plan); cap-overflow pro-rata; comply-and-caution via `constraint_impact`; stateless history-based ask-once; chat-only (no persistence); deferrals (whole-portfolio-to-N, first-turn constraints, session-state pad/sticky frame, durable prefs). Bump line 3 `Thesis version 1.2` → `1.3` + "Last updated" date.

- [ ] **Step 2: Add eval cases (mirror `questions_mf.yaml`)**

```yaml
# questions_consolidation.yaml
- id: cons_01
  question: "Reduce my trades, it looks too many."
  expected_intent: rebalancing
  must_not: ["exactly 5 funds"]          # no fabricated counts pre-clarify
  rubric: Asks ONCE how few funds they want (or applies a sensible consolidation); never repeats the same clarifying question.
- id: cons_02
  question: "Consolidate my rebalancing into 5 funds."
  expected_intent: rebalancing
  must_not: ["live data"]
  rubric: Reshapes the BUY list to <=5 funds using real engine amounts; sells unchanged; includes a grounded caution about mix deviation.
- id: cons_03
  question: "Only invest in largecap and midcap for the rebalance."
  expected_intent: rebalancing
  rubric: Redeploys the whole buy budget into largecap+midcap recommended funds; states the impact on target mix; never leaves cash idle.
```

- [ ] **Step 3: Add the Sourbach regression sequence — THE tripwire**

Multi-turn case replaying "reduce my trades" → "5 funds": assert exactly one clarifying question then a grounded consolidated buy list, with **no repeat** of the same clarify prompt. **This eval is the decision gate for the deferred session-state pad (Spec §7):** if the history-aware classifier loops here, revive the pad; if green, the lean design stands.

- [ ] **Step 4: Run gates**

Run: `scripts/run_prompt_eval_gate.sh` → green.
Run: the `chat_eval` live subset for `questions_consolidation.yaml` (per `run_eval.py` usage) → routing correct, no loop, no fabricated numbers.

- [ ] **Step 5: Commit** (user)

```bash
git add AI_Agents/Reference_docs/Logics_reference_docs/Rebalancing.md AI_Agents/src/chat_eval/questions_consolidation.yaml AI_Agents/src/chat_eval/questions.yaml
git commit -m "docs(rebal): consolidation logic doc v1.3 + eval cases"
```

---

## Final verification

- [ ] `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/ app/domains/rebalancing/services/rebal_engine/tests/ app/domains/ai_engine/tests/ -v` — all green.
- [ ] `scripts/run_prompt_eval_gate.sh` — green.
- [ ] Manual/live: replay the Sourbach turns against a dev profile; confirm one clarify → grounded 5-fund reshape with a caution, sells unchanged, nothing written to `rebalancing_runs`, `chat_session_state` untouched.
- [ ] Confirm the Spec §7 deferrals are untouched: no whole-portfolio-to-N, no first-turn extraction, no persistence, **no session state** (no new columns/migrations, no `TurnContext` changes).
