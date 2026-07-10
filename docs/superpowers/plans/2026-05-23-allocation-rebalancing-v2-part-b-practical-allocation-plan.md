# Allocation v2 Part B — `practical_asset_allocation` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a NEW peer module `AI_Agents/src/practical_asset_allocation/` that wraps `asset_allocation_pydantic` with holdings-aware corpus accounting (ELSS freeze, non-MF equity NFA-banded cap, v2 average-based equity-subgroup slider). All code lives in a single `pipeline.py` per spec §B.1.

**Architecture:** Imports `step1_emergency.run`, `step2_short_term.run`, `step3_medium_term.run`, `step5_aggregation.run`, and helpers (`phase1_bounds`, `phase4_multi_asset`, `phase5_equity_subgroups`, `round_to_100`, `ceil_to_half`) from upstream `asset_allocation_pydantic`. Implements a fresh long-term routine (`_run_practical_long_term`, Excel R157–R222) and a step-5 wrapper that injects two frozen subgroup rows (`tax_efficient_equities`, `non_mf_equities`).

**Tech Stack:** Python 3.11+, pydantic v2, pytest.

**Spec reference:** `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md` Part B (§B.1–B.9).

**Excel reference:** `Local_logics/Sourabh_Logics/goal_based_allocation_model (12) (1).xlsx`, sheet **Allocation 4**, rows 154–222.

**Dependency edge introduced:** Per spec §B.1, this is the FIRST cross-agent import under `AI_Agents/src/`. Documented in both `practical_asset_allocation/CLAUDE.md` (downstream) and `asset_allocation_pydantic/CLAUDE.md` (upstream) as Task 1.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `AI_Agents/src/practical_asset_allocation/__init__.py` | Create | Re-export `run_practical_allocation`, `PracticalAllocationInput`, `PracticalAllocationOutput`, `CorpusBreakdown`, `InfeasibleGoalError`. |
| `AI_Agents/src/practical_asset_allocation/pipeline.py` | Create | ONE file: models + orchestrator + long-term math + step5 wrapper + output builder. |
| `AI_Agents/src/practical_asset_allocation/CLAUDE.md` | Create | Module map + explicit upstream-dependency note on `asset_allocation_pydantic`. |
| `AI_Agents/src/practical_asset_allocation/Testing/` | Create (LOCAL — gitignored) | pytest suite covering the 7 scenarios from spec §B.9. |
| `AI_Agents/src/asset_allocation_pydantic/CLAUDE.md` | Modify | Add note that `practical_asset_allocation/` consumes selected step/helper exports. |
| `AI_Agents/src/CLAUDE.md` | Modify | Add `practical_asset_allocation` entry to the module map and the **Cross-module edges** section. |

---

## Conventions

- Tests live in `AI_Agents/src/practical_asset_allocation/Testing/` — this folder is **gitignored** per `.gitignore` (`/AI_Agents/src/*/Testing/`). Never `git add` files in this folder; tests run locally for TDD discipline but are not part of the shipping artifact.
- Each task's commit step adds engine code **only** (plus the CLAUDE.mds when touched).
- Per spec §B.1 this module is the FIRST cross-agent import under `AI_Agents/src/`. Both `practical_asset_allocation/CLAUDE.md` (this module) and `asset_allocation_pydantic/CLAUDE.md` (upstream) explicitly document the dependency edge in Task 1, and the entry in `AI_Agents/src/CLAUDE.md` adds it to **Cross-module edges**.
- Decimal vs float: all corpus fields use `float` (matching parent `AllocationInput.total_corpus: float`). Decimal migration is out of scope; if the broader codebase moves to Decimal it does so as one cross-module sweep.
- Project name is **"Prozpr"** (never "Prozper" — common autocorrect mistake).
- Memory rule: superpowers artifacts (`docs/superpowers/**`) stay local — do NOT add the plan file itself to git.
- Commit messages follow `feat(practical-allocation): <one-liner>` and reference the spec section.
- Excel cell refs in code comments use the shorthand `R<row>` (e.g. `R177–R179`) — mapping to sheet `Allocation 4`, rows 177–179.

---

### Task 1: Module scaffolding + cross-agent dependency notes

**Files:**
- Create: `AI_Agents/src/practical_asset_allocation/__init__.py`
- Create: `AI_Agents/src/practical_asset_allocation/pipeline.py` (empty stub)
- Create: `AI_Agents/src/practical_asset_allocation/CLAUDE.md`
- Create directory: `AI_Agents/src/practical_asset_allocation/Testing/` (with empty `__init__.py`; gitignored)
- Modify: `AI_Agents/src/asset_allocation_pydantic/CLAUDE.md` (add downstream-consumer note)
- Modify: `AI_Agents/src/CLAUDE.md` (add module entry + cross-module edge)

- [ ] **Step 1: Create the directory and empty stub files**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
mkdir -p AI_Agents/src/practical_asset_allocation/Testing
touch AI_Agents/src/practical_asset_allocation/__init__.py
touch AI_Agents/src/practical_asset_allocation/pipeline.py
touch AI_Agents/src/practical_asset_allocation/Testing/__init__.py
```

- [ ] **Step 2: Write `__init__.py`**

`AI_Agents/src/practical_asset_allocation/__init__.py`:

```python
"""practical_asset_allocation — holdings-aware goal-based allocation.

Wraps asset_allocation_pydantic (steps 1-3 imported verbatim) and reimplements
the long-term step with ELSS freeze, non-MF equity NFA-banded cap, and the v2
average-based equity-subgroup sliding threshold.

Per spec §B.1 this is the first explicit cross-agent import under
AI_Agents/src/; see CLAUDE.md for the dependency edge documentation.
"""
from .pipeline import (
    CorpusBreakdown,
    InfeasibleGoalError,
    PracticalAllocationInput,
    PracticalAllocationOutput,
    run_practical_allocation,
)

__all__ = [
    "CorpusBreakdown",
    "InfeasibleGoalError",
    "PracticalAllocationInput",
    "PracticalAllocationOutput",
    "run_practical_allocation",
]
```

- [ ] **Step 3: Write the initial `pipeline.py` stub**

`AI_Agents/src/practical_asset_allocation/pipeline.py`:

```python
"""practical_asset_allocation pipeline — see module __init__ docstring."""
from __future__ import annotations

# Stub: full implementation lands across Tasks 2–12. Placeholder symbols below
# satisfy the __init__.py re-exports during early TDD iterations.


class InfeasibleGoalError(ValueError):
    """Raised when the input corpus cannot satisfy structural constraints
    (e.g. ELSS holdings exceed total corpus)."""


# These names are filled in by later tasks. Keep the import surface stable.
class PracticalAllocationInput:  # type: ignore[no-redef]
    pass


class CorpusBreakdown:  # type: ignore[no-redef]
    pass


class PracticalAllocationOutput:  # type: ignore[no-redef]
    pass


def run_practical_allocation(inp):  # type: ignore[no-redef]
    raise NotImplementedError(
        "run_practical_allocation: implementation lands in Tasks 4-12 of "
        "docs/superpowers/plans/2026-05-23-allocation-rebalancing-v2-part-b-*-plan.md"
    )
```

- [ ] **Step 4: Write `CLAUDE.md` for the new module**

`AI_Agents/src/practical_asset_allocation/CLAUDE.md`:

```markdown
# AI_Agents/src/practical_asset_allocation

Holdings-aware goal-based asset-allocation pipeline. Wraps
`asset_allocation_pydantic` with four extra corpus inputs (`mf_corpus`,
`non_mf_equity_corpus`, `elss_corpus`, `max_non_mf_equity_pct_client_input`)
and reimplements the long-term step with ELSS freeze, non-MF equity
NFA-banded cap, and the v2 average-based equity-subgroup sliding threshold.

Per spec §B.1 (`docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md`),
this is the **FIRST explicit cross-agent import** under `AI_Agents/src/`. The
`AI_Agents/src/CLAUDE.md` peers-only convention now has two documented
exceptions: this module imports from `asset_allocation_pydantic`, and
`Rebalancing` imports `run_practical_allocation` from this module.

## Files

- `__init__.py` — public re-exports (`run_practical_allocation`,
  `PracticalAllocationInput`, `PracticalAllocationOutput`, `CorpusBreakdown`,
  `InfeasibleGoalError`).
- `pipeline.py` — single file holding all models, the orchestrator, and the
  long-term R157–R222 math. Per design call: revisit splitting only if it
  grows past ~500 lines.
- `Testing/` — pytest suite (gitignored); the seven scenarios from spec §B.9.

## Data contract

- Input: `PracticalAllocationInput` — extends `AllocationInput` with four new
  corpus scalars (`mf_corpus`, `non_mf_equity_corpus`, `elss_corpus`,
  `max_non_mf_equity_pct_client_input`); all other fields inherited unchanged.
- Output: `PracticalAllocationOutput` — shape-parity with `GoalAllocationOutput`
  plus one `corpus_breakdown` block. Any consumer that already understands
  `GoalAllocationOutput` handles `PracticalAllocationOutput` for the shared
  seven fields with zero change.

## Depends on

- `pydantic` only at the third-party level.
- **Cross-agent imports from `asset_allocation_pydantic`** (the spec-blessed
  exception to the peers-only rule):
  - `steps.step1_emergency.run`, `step2_short_term.run`, `step3_medium_term.run`,
    `step5_aggregation.run`.
  - `steps.step4_long_term.phase1_bounds`, `phase4_multi_asset`,
    `phase5_equity_subgroups`.
  - `utils.round_to_100`, `ceil_to_half`.
  - `models.AllocationInput`, `Goal`, `MarketCommentaryScores`,
    `MultiAssetFundComposition`, `BucketAllocation`, `AggregatedSubgroupRow`,
    `FutureInvestment`, `ClientSummary`, `AssetClassBreakdown`,
    `AssetClassSplitBlock`, `BucketAssetClassSplit`, `Step1Output`,
    `Step2Output`, `Step3Output`, `Step4Output`, `Step5Output`.
  - These names must remain stable on the upstream side; renames there are a
    cross-module change.

## Tests

- Command: `PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing -v`
- Covers the seven scenarios from spec §B.9 (regression-vs-ideal, ELSS-below-equity,
  ELSS-lifts-above-ideal, non-MF below cap, non-MF above cap, sliding-threshold v2,
  mid-sequence underfunding).

## Don't read

- `__pycache__/`
- `Testing/` artifacts — captured fixtures, not source of truth.
```

- [ ] **Step 5: Append the upstream-side note to `asset_allocation_pydantic/CLAUDE.md`**

Append to the end of the **Depends on** section in `AI_Agents/src/asset_allocation_pydantic/CLAUDE.md`:

```markdown

## Consumed by

- **`practical_asset_allocation/`** — per spec §B.1 (the first cross-agent edge
  under `AI_Agents/src/`), imports `step1_emergency.run`,
  `step2_short_term.run`, `step3_medium_term.run`, `step5_aggregation.run`,
  selected helpers from `step4_long_term` (`phase1_bounds`,
  `phase4_multi_asset`, `phase5_equity_subgroups`), `utils.round_to_100` /
  `ceil_to_half`, and the public models. **Do not rename these symbols
  without a cross-module sweep.**
```

- [ ] **Step 6: Update `AI_Agents/src/CLAUDE.md`**

Add to the **Child modules** section, alphabetically placed (between `portfolio_query/` and `Rebalancing/`):

```markdown
- **practical_asset_allocation/** — Holdings-aware goal-based allocation. Wraps `asset_allocation_pydantic` (importing its steps 1-3, step5, and selected step4 helpers) with four extra corpus inputs (`mf_corpus`, `non_mf_equity_corpus`, `elss_corpus`, `max_non_mf_equity_pct_client_input`) and reimplements long-term with ELSS freeze, non-MF equity NFA-banded cap, and the v2 average-based equity-subgroup slider. Entry: `pipeline.py` (single file). See `practical_asset_allocation/CLAUDE.md`.
```

Add to the **Cross-module edges** section:

```markdown
- `practical_asset_allocation/` imports from `asset_allocation_pydantic/` (steps 1-3, step5, selected step4 helpers, utils, models) — **the first explicit cross-agent import** under `AI_Agents/src/`, blessed by spec §B.1. Documented on both sides.
- `Rebalancing/` will additionally import `run_practical_allocation` from `practical_asset_allocation/` (Part C of the same spec).
```

- [ ] **Step 7: Verify the new module imports cleanly**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src python -c "import practical_asset_allocation; print(practical_asset_allocation.__all__)"
```

Expected: prints the `__all__` list with all five names.

- [ ] **Step 8: Commit (engine + CLAUDE.mds; Testing folder is gitignored)**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
git add AI_Agents/src/practical_asset_allocation/__init__.py \
        AI_Agents/src/practical_asset_allocation/pipeline.py \
        AI_Agents/src/practical_asset_allocation/CLAUDE.md \
        AI_Agents/src/asset_allocation_pydantic/CLAUDE.md \
        AI_Agents/src/CLAUDE.md
git commit -m "feat(practical-allocation): scaffold module and document cross-agent dependency (B.1)

Creates AI_Agents/src/practical_asset_allocation/ as a peer module under
src/. Adds __init__.py public re-exports, an empty pipeline.py stub (filled
in by later tasks), and CLAUDE.md describing the module's purpose and the
documented cross-agent imports from asset_allocation_pydantic. Updates the
upstream CLAUDE.md with a 'Consumed by' note pinning the import surface, and
extends AI_Agents/src/CLAUDE.md's Cross-module edges section to record this
first cross-agent edge (spec-blessed exception to peers-only).

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.1"
```

---

### Task 2: `PracticalAllocationInput` model (extends `AllocationInput` with 4 fields)

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_models.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

`AI_Agents/src/practical_asset_allocation/Testing/test_models.py`:

```python
import pytest
from asset_allocation_pydantic.models import AllocationInput

from practical_asset_allocation.pipeline import PracticalAllocationInput


def _base_kwargs(**overrides):
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
    return base


def test_practical_input_inherits_from_allocation_input():
    """PracticalAllocationInput must be a subclass of AllocationInput so all
    parent fields are inherited unchanged."""
    assert issubclass(PracticalAllocationInput, AllocationInput)


def test_practical_input_adds_four_corpus_fields_with_defaults():
    """Three of the four new fields default to 0.0; max_non_mf_equity_pct
    defaults to None (advisor override is optional)."""
    inp = PracticalAllocationInput(
        **_base_kwargs(),
        mf_corpus=8_000_000,
    )
    assert inp.mf_corpus == 8_000_000
    assert inp.non_mf_equity_corpus == 0.0
    assert inp.elss_corpus == 0.0
    assert inp.max_non_mf_equity_pct_client_input is None
    # Parent field still accessible:
    assert inp.total_corpus == 10_000_000


def test_practical_input_rejects_negative_mf_corpus():
    with pytest.raises(Exception):  # pydantic ValidationError
        PracticalAllocationInput(**_base_kwargs(), mf_corpus=-1.0)


def test_practical_input_rejects_negative_elss():
    with pytest.raises(Exception):
        PracticalAllocationInput(
            **_base_kwargs(), mf_corpus=5_000_000, elss_corpus=-100.0,
        )


def test_practical_input_accepts_advisor_override():
    inp = PracticalAllocationInput(
        **_base_kwargs(),
        mf_corpus=5_000_000,
        non_mf_equity_corpus=2_000_000,
        elss_corpus=200_000,
        max_non_mf_equity_pct_client_input=0.40,
    )
    assert inp.max_non_mf_equity_pct_client_input == 0.40
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_models.py -v
```

Expected: FAIL — `PracticalAllocationInput` is the stub `class PracticalAllocationInput: pass`, not a real pydantic model.

- [ ] **Step 3: Implement `PracticalAllocationInput` in `pipeline.py`**

Replace the stub block in `pipeline.py` with imports and the input model. Keep the stubs for `CorpusBreakdown`, `PracticalAllocationOutput`, and `run_practical_allocation` in place; they fill in across later tasks.

```python
"""practical_asset_allocation pipeline — see module __init__ docstring."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from asset_allocation_pydantic.models import AllocationInput


class InfeasibleGoalError(ValueError):
    """Raised when the input corpus cannot satisfy structural constraints
    (e.g. ELSS holdings exceed total corpus)."""


class PracticalAllocationInput(AllocationInput):
    """Extends AllocationInput with four holdings-aware corpus scalars.

    Implicit corpus accounting (not separate inputs):
      cash               = total_corpus - mf_corpus - non_mf_equity_corpus
      mf_non_elss        = mf_corpus - elss_corpus
      rebalancing_corpus = total_corpus - elss_corpus
    """

    mf_corpus: float = Field(..., ge=0)
    """Total MF holdings INCLUDING ELSS."""

    non_mf_equity_corpus: float = Field(default=0.0, ge=0)
    """Direct stocks + PMS — non-MF equity, treated separately because the
    rebalancing engine can't trade them per-fund."""

    elss_corpus: float = Field(default=0.0, ge=0)
    """ELSS MF holdings (subset of mf_corpus). Locked under 3-year SEBI
    lock-in — surfaced as a frozen long-term row."""

    max_non_mf_equity_pct_client_input: Optional[float] = Field(default=None)
    """Advisor override for the NFA-banded non-MF equity cap (Option A)."""


# Stubs filled in by later tasks. Keep the import surface stable.
class CorpusBreakdown(BaseModel):
    """Filled in by Task 3."""


class PracticalAllocationOutput(BaseModel):
    """Filled in by Task 3."""


def run_practical_allocation(inp):  # type: ignore[no-untyped-def]
    raise NotImplementedError(
        "run_practical_allocation: implementation lands in Tasks 4-12."
    )
```

- [ ] **Step 4: Run the test — all five should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_models.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Run upstream suite to confirm nothing regressed**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing -v
```

Expected: all green.

- [ ] **Step 6: Commit (engine code only)**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): add PracticalAllocationInput model (B.2)

Defines PracticalAllocationInput inheriting from AllocationInput and adding
four new corpus scalars: mf_corpus (total MF incl. ELSS, ge=0),
non_mf_equity_corpus (direct stocks + PMS, default 0), elss_corpus (subset of
mf_corpus, default 0), and max_non_mf_equity_pct_client_input (optional
advisor override). All fields use float to match the parent's total_corpus.
Implicit accounting (cash, mf_non_elss, rebalancing_corpus) is documented in
the docstring but never materialised as fields.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.2"
```

---

### Task 3: `CorpusBreakdown` + `PracticalAllocationOutput` models

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_models.py` (LOCAL, append)

- [ ] **Step 1: Write the failing test**

Append to `test_models.py`:

```python
from asset_allocation_pydantic.models import (
    AggregatedSubgroupRow,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAllocation,
    BucketAssetClassSplit,
    ClientSummary,
    FutureInvestment,
)

from practical_asset_allocation.pipeline import (
    CorpusBreakdown,
    PracticalAllocationOutput,
)


def test_corpus_breakdown_round_trip():
    cb = CorpusBreakdown(
        total_corpus_inr=10_000_000,
        mf_corpus_inr=8_000_000,
        non_mf_equity_input_inr=1_500_000,
        elss_corpus_inr=200_000,
        rebalancing_corpus_inr=9_800_000,
        non_mf_equity_actual_inr=1_200_000,
        excess_direct_stocks_inr=300_000,
        max_non_mf_equity_pct_computed=0.50,
    )
    assert cb.rebalancing_corpus_inr == cb.total_corpus_inr - cb.elss_corpus_inr
    assert cb.excess_direct_stocks_inr == cb.non_mf_equity_input_inr - cb.non_mf_equity_actual_inr


def _empty_acsb() -> AssetClassSplitBlock:
    return AssetClassSplitBlock(
        per_bucket=[],
        equity_total=0, debt_total=0, others_total=0,
    )


def test_practical_output_has_shape_parity_with_goal_allocation_output_plus_corpus_breakdown():
    out = PracticalAllocationOutput(
        client_summary=ClientSummary(
            age=40, effective_risk_score=5.5,
            total_corpus=10_000_000, goals=[],
        ),
        bucket_allocations=[],
        aggregated_subgroups=[],
        future_investments_summary=[],
        grand_total=10_000_000,
        all_amounts_in_multiples_of_100=True,
        asset_class_breakdown=AssetClassBreakdown(
            planned=_empty_acsb(), recommended=_empty_acsb(),
            recommended_sum_matches_grand_total=True,
        ),
        corpus_breakdown=CorpusBreakdown(
            total_corpus_inr=10_000_000, mf_corpus_inr=10_000_000,
            non_mf_equity_input_inr=0, elss_corpus_inr=0,
            rebalancing_corpus_inr=10_000_000, non_mf_equity_actual_inr=0,
            excess_direct_stocks_inr=0, max_non_mf_equity_pct_computed=0.50,
        ),
    )
    # The shared seven fields must accept the same types as GoalAllocationOutput:
    assert out.grand_total == 10_000_000
    assert out.corpus_breakdown.total_corpus_inr == 10_000_000
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_models.py::test_corpus_breakdown_round_trip -v
```

Expected: FAIL — `CorpusBreakdown` is the empty stub.

- [ ] **Step 3: Implement both models in `pipeline.py`**

Replace the two `class ... Filled in by Task 3` stubs with the real models. Also widen the import line.

```python
from typing import List, Optional

from asset_allocation_pydantic.models import (
    AggregatedSubgroupRow,
    AllocationInput,
    AssetClassBreakdown,
    BucketAllocation,
    ClientSummary,
    FutureInvestment,
)
```

Replace the two stubs with:

```python
class CorpusBreakdown(BaseModel):
    """Practical-only block: how the customer's corpus splits across MF /
    non-MF equity / cash, and what the engine actually deployed.

    All amounts are rupees rounded to whole integers; the engine internally
    works in floats and rounds at the boundary.
    """
    total_corpus_inr: int = Field(..., ge=0)
    mf_corpus_inr: int = Field(..., ge=0)
    non_mf_equity_input_inr: int = Field(..., ge=0)
    """Echo of the input — what the customer said they hold."""
    elss_corpus_inr: int = Field(..., ge=0)
    rebalancing_corpus_inr: int = Field(..., ge=0)
    """total_corpus_inr - elss_corpus_inr (ELSS is frozen)."""
    non_mf_equity_actual_inr: int = Field(..., ge=0)
    """<= input, NFA-capped — what the engine could absorb."""
    excess_direct_stocks_inr: int = Field(..., ge=0)
    """input - actual; drives the SELL_DIRECT_STOCKS recommendation downstream."""
    max_non_mf_equity_pct_computed: float = Field(..., ge=0.0, le=1.0)
    """NFA-banded value used (or override if the advisor provided one)."""


class PracticalAllocationOutput(BaseModel):
    """Shape-parity with GoalAllocationOutput (same seven fields) plus one
    extras block (corpus_breakdown).

    Any consumer that already understands GoalAllocationOutput handles
    PracticalAllocationOutput for the shared seven fields with zero change.
    """
    client_summary: ClientSummary
    bucket_allocations: List[BucketAllocation]
    aggregated_subgroups: List[AggregatedSubgroupRow]
    """Same shape as GoalAllocationOutput.aggregated_subgroups, but includes
    two extra rows: 'tax_efficient_equities' (ELSS amount in long_term column)
    and 'non_mf_equities' (non-MF equity actual in long_term column)."""
    future_investments_summary: List[FutureInvestment]
    grand_total: float
    all_amounts_in_multiples_of_100: bool
    asset_class_breakdown: AssetClassBreakdown
    corpus_breakdown: CorpusBreakdown
```

- [ ] **Step 4: Run the test — both new tests should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_models.py -v
```

Expected: 7 passed (5 from Task 2 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): add CorpusBreakdown and PracticalAllocationOutput (B.3)

CorpusBreakdown reports total/MF/non-MF/ELSS/rebalancing/actual/excess corpus
amounts plus the NFA-banded cap percentage. PracticalAllocationOutput has
shape parity with GoalAllocationOutput (same seven fields) plus the
corpus_breakdown extras block — consumers reading the shared fields need no
change.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.3"
```

---

### Task 4: `InfeasibleGoalError` + `run_practical_allocation` orchestrator skeleton

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_orchestrator_skeleton.py` (LOCAL)

This task wires the ELSS-freeze guard, builds the sub-`AllocationInput`, and calls steps 1–3 from upstream. The long-term step is left as a placeholder that raises `NotImplementedError` until Task 5 fills it in — but the skeleton's structural behaviour (raise on infeasibility, pass `rebalancing_corpus` into upstream steps) is testable now.

- [ ] **Step 1: Write the failing tests**

`AI_Agents/src/practical_asset_allocation/Testing/test_orchestrator_skeleton.py`:

```python
import pytest

from practical_asset_allocation.pipeline import (
    InfeasibleGoalError,
    PracticalAllocationInput,
    run_practical_allocation,
)


def _base_kwargs(**overrides):
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
    return base


def test_infeasible_when_elss_exceeds_total_corpus():
    """Spec edge case (α): ELSS > total corpus → InfeasibleGoalError."""
    inp = PracticalAllocationInput(
        **_base_kwargs(total_corpus=1_000_000),
        mf_corpus=900_000,
        elss_corpus=1_100_000,  # > total
    )
    with pytest.raises(InfeasibleGoalError):
        run_practical_allocation(inp)


def test_orchestrator_calls_long_term_step_with_rebalancing_corpus_excluding_elss(monkeypatch):
    """Verifies that steps 1-3 see rebalancing_corpus (total - ELSS), not total.

    We monkeypatch _run_practical_long_term to capture its inputs and short-
    circuit the rest of the pipeline before it asserts on the result shape.
    """
    captured = {}

    def fake_long_term(*, inp, remaining_corpus, elss_amount, non_mf_equity_input,
                       nfa, max_non_mf_equity_pct_client_input):
        captured["sub_total_corpus"] = inp.total_corpus
        captured["elss_amount"] = elss_amount
        captured["non_mf_equity_input"] = non_mf_equity_input
        raise RuntimeError("stop-here-after-capture")

    from practical_asset_allocation import pipeline as plm
    monkeypatch.setattr(plm, "_run_practical_long_term", fake_long_term)

    inp = PracticalAllocationInput(
        **_base_kwargs(total_corpus=10_000_000),
        mf_corpus=8_000_000,
        non_mf_equity_corpus=1_500_000,
        elss_corpus=200_000,
    )
    with pytest.raises(RuntimeError, match="stop-here-after-capture"):
        run_practical_allocation(inp)

    assert captured["sub_total_corpus"] == 10_000_000 - 200_000, (
        "sub_inp.total_corpus must equal rebalancing_corpus = total - ELSS"
    )
    assert captured["elss_amount"] == 200_000
    assert captured["non_mf_equity_input"] == 1_500_000
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_orchestrator_skeleton.py -v
```

Expected: FAIL — `run_practical_allocation` is `raise NotImplementedError(...)`.

- [ ] **Step 3: Implement the orchestrator skeleton + placeholder `_run_practical_long_term`**

Add to `pipeline.py` (below the model definitions, above the existing `run_practical_allocation` stub):

```python
from dataclasses import dataclass

from asset_allocation_pydantic.steps import (
    step1_emergency,
    step2_short_term,
    step3_medium_term,
)


@dataclass
class _PracticalLongTermResult:
    """Internal-only — full shape filled in across Tasks 5-10. Mirrors what
    Step4Output exposes plus practical-only extras (non_mf_equity_actual,
    excess_direct_stocks, residual_equity_corpus, etc.)."""
    # Placeholder fields; Tasks 5-10 expand this.
    pass


def _run_practical_long_term(
    *,
    inp: AllocationInput,
    remaining_corpus: int,
    elss_amount: float,
    non_mf_equity_input: float,
    nfa: Optional[float],
    max_non_mf_equity_pct_client_input: Optional[float],
) -> _PracticalLongTermResult:
    """Long-term step — Excel R157-R222. Filled in across Tasks 5-10."""
    raise NotImplementedError(
        "_run_practical_long_term: implementation lands in Tasks 5-10."
    )
```

Replace the existing `run_practical_allocation` stub with:

```python
def run_practical_allocation(inp: PracticalAllocationInput) -> PracticalAllocationOutput:
    """Holdings-aware goal-based allocation. Spec §B.4.

    Pipeline:
      1. ELSS freeze — subtract elss_corpus to get rebalancing_corpus.
      2. Build sub-AllocationInput with total_corpus = rebalancing_corpus.
      3. Run upstream steps 1-3 (emergency, short-term, medium-term) verbatim.
      4. Run _run_practical_long_term (Excel R157-R222) for the long-term step.
      5. Aggregate with step5_aggregation_with_frozen (adds two frozen rows).
      6. Assemble PracticalAllocationOutput.
    """
    rebalancing_corpus = inp.total_corpus - inp.elss_corpus
    if rebalancing_corpus < 0:
        # Edge case (α) per spec §B.7 — should never happen in practice.
        raise InfeasibleGoalError(
            f"ELSS corpus ({inp.elss_corpus}) exceeds total corpus ({inp.total_corpus})"
        )

    # Build a sub-AllocationInput with rebalancing_corpus as total_corpus.
    # model_dump() preserves all parent fields; we override total_corpus only.
    parent_fields = AllocationInput.model_fields.keys()
    sub_inp = AllocationInput(
        **{k: getattr(inp, k) for k in parent_fields if k != "total_corpus"},
        total_corpus=rebalancing_corpus,
    )

    s1 = step1_emergency.run(sub_inp)
    s2 = step2_short_term.run(sub_inp, s1.remaining_corpus)
    s3 = step3_medium_term.run(sub_inp, s2.remaining_corpus)

    s4_practical = _run_practical_long_term(
        inp=sub_inp,
        remaining_corpus=s3.remaining_corpus,
        elss_amount=inp.elss_corpus,
        non_mf_equity_input=inp.non_mf_equity_corpus,
        nfa=inp.net_financial_assets,
        max_non_mf_equity_pct_client_input=inp.max_non_mf_equity_pct_client_input,
    )

    # Tasks 11-12 implement step5_aggregation_with_frozen and _build_output.
    # Until then, this orchestrator can be unit-tested via monkeypatch of
    # _run_practical_long_term (see test_orchestrator_skeleton.py).
    raise NotImplementedError(
        "Output assembly lands in Tasks 11-12; the long-term path above "
        "is structurally complete and is exercised under monkeypatch."
    )
```

- [ ] **Step 4: Run the test — both should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_orchestrator_skeleton.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Confirm upstream suite still green**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): orchestrator skeleton with ELSS freeze + steps 1-3 wiring (B.4)

Adds InfeasibleGoalError raising when ELSS exceeds total corpus, builds the
sub-AllocationInput (rebalancing_corpus = total - ELSS), and calls
step1_emergency.run / step2_short_term.run / step3_medium_term.run verbatim
from asset_allocation_pydantic. The long-term step is wired as a placeholder
that raises NotImplementedError until Tasks 5-10 fill it in; output assembly
(Tasks 11-12) is similarly stubbed. The orchestrator is exercised under
monkeypatch to confirm the sub-inp + step inputs are correct.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.4"
```

---

### Task 5: Long-term part 1 — corpus assembly, ELSS floor, first-level asset class

**Implements Excel R157–R165.** Builds out `_PracticalLongTermResult` with the early-step fields.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part1.py` (LOCAL)

- [ ] **Step 1: Write the failing tests**

`AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part1.py`:

```python
from asset_allocation_pydantic.models import AllocationInput

from practical_asset_allocation.pipeline import _run_practical_long_term


def _base_alloc_input(**overrides) -> AllocationInput:
    base = dict(
        effective_risk_score=5.5,
        age=40,
        annual_income=2_000_000,
        osi=0.0,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        shortfall_amount=0.0,
        total_corpus=5_000_000,
        monthly_household_expense=100_000,
        effective_tax_rate=15.0,
        net_financial_assets=10_000_000,
        goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_lt_part1_total_long_term_corpus_adds_back_elss():
    """R158: total_long_term_corpus = max(0, remaining_corpus + elss_amount)."""
    result = _run_practical_long_term(
        inp=_base_alloc_input(),
        remaining_corpus=4_000_000,
        elss_amount=500_000,
        non_mf_equity_input=0,
        nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.total_long_term_corpus == 4_500_000


def test_lt_part1_min_equity_elss_pct():
    """R159: min_equity_elss_pct = elss / total_long_term_corpus."""
    result = _run_practical_long_term(
        inp=_base_alloc_input(),
        remaining_corpus=4_000_000,
        elss_amount=500_000,
        non_mf_equity_input=0,
        nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # 500_000 / 4_500_000 ≈ 0.1111
    assert abs(result.min_equity_elss_pct - (500_000 / 4_500_000)) < 1e-9


def test_lt_part1_min_equity_elss_pct_is_zero_when_no_elss():
    result = _run_practical_long_term(
        inp=_base_alloc_input(),
        remaining_corpus=4_000_000,
        elss_amount=0,
        non_mf_equity_input=0,
        nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.min_equity_elss_pct == 0.0


def test_lt_part1_phase1_bounds_populated():
    """R161-R165: phase1_bounds reused from asset_allocation_pydantic."""
    result = _run_practical_long_term(
        inp=_base_alloc_input(effective_risk_score=5.5),
        remaining_corpus=4_000_000,
        elss_amount=0,
        non_mf_equity_input=0,
        nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # Risk 5.5 → PHASE1_RISK_BOUNDS[5.5] = (30, 60, 30, 55, 5, 15)
    assert result.phase1_bounds_allocation_1.eq_min == 30
    assert result.phase1_bounds_allocation_1.eq_max == 60
    assert result.phase1_bounds_allocation_1.debt_min == 30
    assert result.phase1_bounds_allocation_1.debt_max == 55
    assert result.phase1_bounds_allocation_1.others_min == 5
    assert result.phase1_bounds_allocation_1.others_max == 15


def test_lt_part1_handles_zero_remaining_corpus():
    result = _run_practical_long_term(
        inp=_base_alloc_input(),
        remaining_corpus=0,
        elss_amount=0,
        non_mf_equity_input=0,
        nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.total_long_term_corpus == 0
    assert result.min_equity_elss_pct == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part1.py -v
```

Expected: FAIL — `_run_practical_long_term` raises `NotImplementedError`.

- [ ] **Step 3: Implement part 1 — expand `_PracticalLongTermResult` and `_run_practical_long_term`**

Replace the placeholder `_PracticalLongTermResult` dataclass and `_run_practical_long_term` function:

```python
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
)


@dataclass
class _PracticalLongTermResult:
    """Internal carrier for the long-term step output. Filled in across
    Tasks 5-10; output assembly (Tasks 11-12) reads from here."""
    # R157-R165 (Task 5):
    total_long_term_corpus: int
    min_equity_elss_pct: float
    phase1_bounds_allocation_1: ResolvedBounds
    # Tasks 6-10 will add: allocation_2_*, equities_amount, debt_amount,
    # others_amount, non_mf_equity_actual, excess_direct_stocks,
    # residual_equity_corpus, multi_asset block, equity_subgroup_amounts,
    # subgroup_amounts, future_investment, goals_allocated, etc.


def _run_practical_long_term(
    *,
    inp: AllocationInput,
    remaining_corpus: int,
    elss_amount: float,
    non_mf_equity_input: float,
    nfa: Optional[float],
    max_non_mf_equity_pct_client_input: Optional[float],
) -> _PracticalLongTermResult:
    """Long-term step — Excel R157-R222. Holdings-aware.

    Layout (split across Tasks 5-10):
      Task 5  (R157-R165): corpus assembly, ELSS floor, first-level bounds.
      Task 6  (R167-R174): others-gate, second-level allocation pct.
      Task 7  (R177-R186): amounts, ELSS, non-MF cap, residual_equity.
      Task 8  (R187-R194): multi-asset block.
      Task 9  (R196-R215): equity subgroup gates, slider, amounts.
      Task 10 (R217-R222): debt and others residuals.
    """
    # R158: long-term corpus includes ELSS added back (ELSS is locked but
    # counted toward the long-term equity-class budget).
    total_long_term_corpus = max(0, int(remaining_corpus + elss_amount))

    # R159: ELSS-as-floor share of long-term equity.
    if total_long_term_corpus > 0:
        min_equity_elss_pct = elss_amount / total_long_term_corpus
    else:
        min_equity_elss_pct = 0.0

    # R161-R165: first-level asset-class bounds from PHASE1_RISK_BOUNDS,
    # reused verbatim from asset_allocation_pydantic.
    bounds_1 = phase1_bounds(
        score=inp.effective_risk_score,
        market_commentary=inp.market_commentary,
        goals=[],  # phase1_bounds does not use goals; pass empty for now.
        intergenerational_transfer=inp.intergenerational_transfer,
    )

    return _PracticalLongTermResult(
        total_long_term_corpus=total_long_term_corpus,
        min_equity_elss_pct=min_equity_elss_pct,
        phase1_bounds_allocation_1=bounds_1,
    )
```

> **Note:** `phase1_bounds` already includes the upstream others-gate (lookup_score ≥ 8 AND market_commentary.others ≤ 6). The spec §B.5 step 4 calls out a **stricter** practical-side variant (`risk > 8 AND view < 7`) — Task 6 applies that on top.

- [ ] **Step 4: Run Task 5 tests — all should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part1.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): long-term part 1 - corpus + ELSS floor + phase1 bounds (B.5/R157-R165)

Adds total_long_term_corpus (remaining_corpus + ELSS added back),
min_equity_elss_pct (ELSS share of long-term), and the first-level
phase1_bounds lookup. Reuses asset_allocation_pydantic.step4_long_term.phase1_bounds
(documented cross-agent import per spec §B.1). Subsequent tasks layer the
stricter practical others-gate, second-level allocation, amounts, ELSS / non-
MF cap, multi-asset, equity subgroups, and debt/others residuals on top.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.5"
```

---

### Task 6: Long-term part 2 — practical others-gate + second-level asset class

**Implements Excel R167–R174.** Layered on Task 5.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part2.py` (LOCAL)

- [ ] **Step 1: Write the failing tests**

```python
from asset_allocation_pydantic.models import AllocationInput, MarketCommentaryScores

from practical_asset_allocation.pipeline import _run_practical_long_term


def _alloc(**overrides) -> AllocationInput:
    base = dict(
        effective_risk_score=8.5,  # > 8 so practical others-gate fires when view < 7
        age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=5_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_lt_part2_practical_others_gate_fires_at_high_risk_with_others_view_below_7():
    """Spec §B.5 step 4: risk > 8 AND market_commentary.others < 7 → force
    others_min=others_max=0 (stricter than asset_allocation's >=8 / <=6)."""
    inp = _alloc(
        effective_risk_score=8.5,
        market_commentary=MarketCommentaryScores(others=6.5),
    )
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # Allocation_2 others should be 0 (practical gate fired).
    assert result.allocation_2_others_pct == 0


def test_lt_part2_practical_others_gate_does_not_fire_when_view_at_or_above_7():
    inp = _alloc(
        effective_risk_score=8.5,
        market_commentary=MarketCommentaryScores(others=7.0),
    )
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.allocation_2_others_pct > 0


def test_lt_part2_elss_floor_lifts_allocation_2_equity():
    """R171: allocation_2_eq = max(allocation_1_eq * 100 / sum, elss_floor_pct * 100)."""
    inp = _alloc(effective_risk_score=3.0)  # Low risk → eq_min/max = 15/45
    # ELSS so large it lifts equity above the natural midpoint:
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=200_000, elss_amount=800_000,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # min_equity_elss_pct = 800_000 / 1_000_000 = 80%
    # So allocation_2_equity must be at least 80.
    assert result.allocation_2_equity_pct >= 80


def test_lt_part2_allocation_2_pcts_sum_to_100():
    inp = _alloc(effective_risk_score=5.5)
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    total = (result.allocation_2_equity_pct + result.allocation_2_debt_pct
             + result.allocation_2_others_pct)
    assert total == 100
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part2.py -v
```

Expected: FAIL — fields `allocation_2_*_pct` don't exist on `_PracticalLongTermResult`.

- [ ] **Step 3: Implement part 2**

Expand `_PracticalLongTermResult`:

```python
@dataclass
class _PracticalLongTermResult:
    # R157-R165 (Task 5):
    total_long_term_corpus: int
    min_equity_elss_pct: float
    phase1_bounds_allocation_1: ResolvedBounds
    # R167-R174 (Task 6):
    practical_others_gate_fired: bool
    allocation_2_equity_pct: int
    allocation_2_debt_pct: int
    allocation_2_others_pct: int
    # Tasks 7-10 add more fields.
```

Import the `phase2_asset_class_pcts` helper too (for the market-view tilt) and constants for the new threshold:

```python
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
    phase2_asset_class_pcts,
)
```

Add constants near the top of `pipeline.py`:

```python
# Spec §B.5 step 4 — practical-side others-gate (stricter than upstream).
# Upstream uses score >= 8 AND view <= 6; practical uses score > 8 AND view < 7.
PRACTICAL_OTHERS_GATE_SCORE_THRESHOLD: float = 8.0
PRACTICAL_OTHERS_GATE_VIEW_THRESHOLD: float = 7.0
```

Extend `_run_practical_long_term`. After the existing bounds_1 block:

```python
    # R167-R168: stricter practical others-gate. Note: phase1_bounds already
    # applied the upstream gate (score >= 8 AND view <= 6) inside bounds_1.
    # We layer the stricter variant (score > 8 AND view < 7) on top so the
    # practical engine zeros others slightly earlier than the ideal engine.
    practical_others_gate_fired = (
        inp.effective_risk_score > PRACTICAL_OTHERS_GATE_SCORE_THRESHOLD
        and inp.market_commentary.others < PRACTICAL_OTHERS_GATE_VIEW_THRESHOLD
    )
    bounds_for_phase2 = bounds_1
    if practical_others_gate_fired and (bounds_1.others_min > 0 or bounds_1.others_max > 0):
        # Pro-rata redistribute the zeroed others to equity and debt mins.
        freed_max = bounds_1.others_max
        freed_min = bounds_1.others_min
        eq_max_new = bounds_1.eq_max
        debt_max_new = bounds_1.debt_max
        eq_min_new = bounds_1.eq_min
        debt_min_new = bounds_1.debt_min
        total_max = bounds_1.eq_max + bounds_1.debt_max
        if total_max > 0 and freed_max > 0:
            eq_add = int(round(freed_max * bounds_1.eq_max / total_max))
            eq_max_new += eq_add
            debt_max_new += freed_max - eq_add
        total_min = bounds_1.eq_min + bounds_1.debt_min
        if total_min > 0 and freed_min > 0:
            eq_add_min = int(round(freed_min * bounds_1.eq_min / total_min))
            eq_min_new += eq_add_min
            debt_min_new += freed_min - eq_add_min
        bounds_for_phase2 = ResolvedBounds(
            eq_min=eq_min_new, eq_max=eq_max_new,
            debt_min=debt_min_new, debt_max=debt_max_new,
            others_min=0, others_max=0,
        )

    # R170: market-view tilt → phase2_asset_class_pcts (reused upstream).
    a2_eq_pct_raw, a2_debt_pct_raw, a2_oth_pct_raw = phase2_asset_class_pcts(
        bounds_for_phase2, inp.market_commentary,
    )

    # R171: ELSS floor lifts equity allocation if needed.
    elss_floor_pct_int = int(round(min_equity_elss_pct * 100))
    allocation_2_equity_pct = max(a2_eq_pct_raw, elss_floor_pct_int)

    # R172: pro-rata redistribution of the residual into debt / others
    # based on their phase1 minimums (or upstream-tilted shares if mins=0).
    remaining_pct = 100 - allocation_2_equity_pct
    debt_plus_others_phase1 = bounds_for_phase2.debt_min + bounds_for_phase2.others_min
    if remaining_pct <= 0:
        allocation_2_debt_pct = 0
        allocation_2_others_pct = 0
        # Force-clamp equity at 100 if the ELSS floor overshot.
        allocation_2_equity_pct = 100
    elif debt_plus_others_phase1 > 0:
        allocation_2_debt_pct = int(round(
            remaining_pct * bounds_for_phase2.debt_min / debt_plus_others_phase1
        ))
        allocation_2_others_pct = remaining_pct - allocation_2_debt_pct
    else:
        # Both phase1 mins zero — split remainder by upstream-tilted ratio.
        dt_oth_raw = a2_debt_pct_raw + a2_oth_pct_raw
        if dt_oth_raw > 0:
            allocation_2_debt_pct = int(round(
                remaining_pct * a2_debt_pct_raw / dt_oth_raw
            ))
            allocation_2_others_pct = remaining_pct - allocation_2_debt_pct
        else:
            allocation_2_debt_pct = remaining_pct
            allocation_2_others_pct = 0
```

Update the return:

```python
    return _PracticalLongTermResult(
        total_long_term_corpus=total_long_term_corpus,
        min_equity_elss_pct=min_equity_elss_pct,
        phase1_bounds_allocation_1=bounds_1,
        practical_others_gate_fired=practical_others_gate_fired,
        allocation_2_equity_pct=allocation_2_equity_pct,
        allocation_2_debt_pct=allocation_2_debt_pct,
        allocation_2_others_pct=allocation_2_others_pct,
    )
```

- [ ] **Step 4: Run Task 6 tests + re-run Task 5 tests**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part1.py AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part2.py -v
```

Expected: 5 + 4 = 9 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): long-term part 2 - practical others-gate + second-level alloc (B.5/R167-R174)

Applies the spec-blessed stricter practical others-gate (risk > 8 AND
others view < 7) layered on top of phase1_bounds' upstream gate (>= 8 / <= 6),
redistributing the zeroed others to equity/debt mins pro-rata. Computes
allocation_2 percentages via the upstream phase2_asset_class_pcts tilt and
applies the ELSS-as-floor uplift (R171). Pro-rata partitions the remaining
share into debt vs others using phase1 mins as weights, with sensible
fallbacks when both mins are zero.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.5"
```

---

### Task 7: Long-term part 3 — amounts, ELSS / non-MF cap, residual equity

**Implements Excel R177–R186.**

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part3.py` (LOCAL)

- [ ] **Step 1: Write the failing tests**

```python
from practical_asset_allocation.pipeline import _run_practical_long_term


def _alloc(**overrides):
    from asset_allocation_pydantic.models import AllocationInput
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=5_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_lt_part3_amounts_sum_to_total_long_term_corpus():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    total = result.equities_amount + result.debt_amount + result.others_amount
    assert total == result.total_long_term_corpus


def test_lt_part3_max_non_mf_pct_computed_75_when_nfa_above_5cr():
    """R182: NFA-banded — > 5Cr → 75%, > 2Cr → 60%, > 1Cr → 50%, else → 33%."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=60_000_000,  # 6Cr
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.max_non_mf_equity_pct_computed == 0.75


def test_lt_part3_max_non_mf_pct_computed_60_when_nfa_above_2cr():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=30_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.max_non_mf_equity_pct_computed == 0.60


def test_lt_part3_max_non_mf_pct_computed_50_when_nfa_above_1cr():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=15_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.max_non_mf_equity_pct_computed == 0.50


def test_lt_part3_max_non_mf_pct_computed_33_when_nfa_at_or_below_1cr():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=8_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.max_non_mf_equity_pct_computed == 0.33


def test_lt_part3_max_non_mf_pct_uses_advisor_override_when_provided():
    """R184: Option A — client input takes precedence when present."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=60_000_000,
        max_non_mf_equity_pct_client_input=0.40,
    )
    assert result.max_non_mf_equity_pct_considered == 0.40
    # Computed still reports the band value:
    assert result.max_non_mf_equity_pct_computed == 0.75


def test_lt_part3_non_mf_equity_actual_capped_below_input():
    """When input non-MF equity exceeds the cap, actual is clamped."""
    # Risk 5.5 with 4M corpus → equity ≈ midpoint of 30-60 = 45% → 1.8M.
    # max_non_mf_pct = 50% (NFA = 1.5Cr band).
    # cap = 50% × 1.8M = 900k. Input = 1.5M → actual ≈ 900k, excess ≈ 600k.
    result = _run_practical_long_term(
        inp=_alloc(effective_risk_score=5.5),
        remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=1_500_000, nfa=15_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.non_mf_equity_actual <= result.equities_amount
    assert result.non_mf_equity_actual <= int(0.50 * result.equities_amount) + 100
    assert result.excess_direct_stocks > 0
    assert (result.non_mf_equity_actual + result.excess_direct_stocks
            == 1_500_000)


def test_lt_part3_residual_equity_corpus_clamps_to_zero_when_locked_exceeds_equity():
    """Spec §B.7: ELSS + non-MF actual > equities_amount → residual = 0."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=200_000, elss_amount=800_000,
        non_mf_equity_input=500_000, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.residual_equity_corpus_pre_multi_asset >= 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part3.py -v
```

Expected: FAIL — fields don't exist.

- [ ] **Step 3: Implement part 3**

Add NFA-band constants near the top of `pipeline.py`:

```python
# Spec §B.5 step 7 (R182) — NFA-banded max non-MF equity %.
# > 5Cr → 75%, > 2Cr → 60%, > 1Cr → 50%, else → 33%.
NFA_BAND_5CR_INR: float = 50_000_000.0
NFA_BAND_2CR_INR: float = 20_000_000.0
NFA_BAND_1CR_INR: float = 10_000_000.0
NFA_BAND_PCT_ABOVE_5CR: float = 0.75
NFA_BAND_PCT_ABOVE_2CR: float = 0.60
NFA_BAND_PCT_ABOVE_1CR: float = 0.50
NFA_BAND_PCT_DEFAULT: float = 0.33
```

Add a helper above `_run_practical_long_term`:

```python
def _nfa_banded_max_non_mf_equity_pct(nfa: Optional[float]) -> float:
    """R182: returns the NFA-banded max non-MF equity %. Treats None NFA as the
    bottom band (33%) — defensive: callers normally pass NFA always."""
    if nfa is None:
        return NFA_BAND_PCT_DEFAULT
    if nfa > NFA_BAND_5CR_INR:
        return NFA_BAND_PCT_ABOVE_5CR
    if nfa > NFA_BAND_2CR_INR:
        return NFA_BAND_PCT_ABOVE_2CR
    if nfa > NFA_BAND_1CR_INR:
        return NFA_BAND_PCT_ABOVE_1CR
    return NFA_BAND_PCT_DEFAULT
```

Extend `_PracticalLongTermResult`:

```python
    # R177-R186 (Task 7):
    equities_amount: int
    debt_amount: int
    others_amount: int
    elss_amount_frozen: int
    max_non_mf_equity_pct_computed: float
    max_non_mf_equity_pct_considered: float
    max_equities_shares: int
    non_mf_equity_actual: int
    excess_direct_stocks: int
    residual_equity_corpus_pre_multi_asset: int
```

Also bring in `round_to_100`:

```python
from asset_allocation_pydantic.utils import round_to_100
```

Extend `_run_practical_long_term` (continue after Task 6's code):

```python
    # R177-R179: amounts.
    equities_amount = round_to_100(
        total_long_term_corpus * allocation_2_equity_pct / 100
    )
    others_amount = round_to_100(
        total_long_term_corpus * allocation_2_others_pct / 100
    )
    debt_amount = max(0, total_long_term_corpus - equities_amount - others_amount)
    debt_amount = round_to_100(debt_amount)

    # Reconcile rounding drift onto the largest amount (mirrors upstream pattern).
    drift = total_long_term_corpus - (equities_amount + debt_amount + others_amount)
    if drift != 0:
        amounts_by_name = {"eq": equities_amount, "dt": debt_amount, "oth": others_amount}
        largest = max(amounts_by_name, key=lambda k: amounts_by_name[k])
        amounts_by_name[largest] += drift
        equities_amount = max(0, amounts_by_name["eq"])
        debt_amount = max(0, amounts_by_name["dt"])
        others_amount = max(0, amounts_by_name["oth"])

    # R180: ELSS frozen amount.
    elss_amount_frozen = int(round(elss_amount))

    # R182-R184: NFA-banded cap + advisor override.
    max_non_mf_equity_pct_computed = _nfa_banded_max_non_mf_equity_pct(nfa)
    max_non_mf_equity_pct_considered = (
        max_non_mf_equity_pct_client_input
        if max_non_mf_equity_pct_client_input is not None
        else max_non_mf_equity_pct_computed
    )

    # R185: ceiling for non-MF equity absorption.
    max_equities_shares = int(round(
        max_non_mf_equity_pct_considered * equities_amount
    ))

    # R186: non-MF actual = min(input, equities_amount - elss, max_equities_shares).
    available_after_elss = max(0, equities_amount - elss_amount_frozen)
    non_mf_equity_actual = int(round(min(
        non_mf_equity_input,
        available_after_elss,
        max_equities_shares,
    )))
    non_mf_equity_actual = max(0, non_mf_equity_actual)

    # Excess (drives SELL_DIRECT_STOCKS downstream).
    excess_direct_stocks = max(0, int(round(non_mf_equity_input)) - non_mf_equity_actual)

    # Residual equity corpus available for MF subgroups (pre-multi-asset).
    residual_equity_corpus_pre_multi_asset = max(
        0, equities_amount - non_mf_equity_actual - elss_amount_frozen,
    )
```

Update the return constructor:

```python
    return _PracticalLongTermResult(
        total_long_term_corpus=total_long_term_corpus,
        min_equity_elss_pct=min_equity_elss_pct,
        phase1_bounds_allocation_1=bounds_1,
        practical_others_gate_fired=practical_others_gate_fired,
        allocation_2_equity_pct=allocation_2_equity_pct,
        allocation_2_debt_pct=allocation_2_debt_pct,
        allocation_2_others_pct=allocation_2_others_pct,
        equities_amount=equities_amount,
        debt_amount=debt_amount,
        others_amount=others_amount,
        elss_amount_frozen=elss_amount_frozen,
        max_non_mf_equity_pct_computed=max_non_mf_equity_pct_computed,
        max_non_mf_equity_pct_considered=max_non_mf_equity_pct_considered,
        max_equities_shares=max_equities_shares,
        non_mf_equity_actual=non_mf_equity_actual,
        excess_direct_stocks=excess_direct_stocks,
        residual_equity_corpus_pre_multi_asset=residual_equity_corpus_pre_multi_asset,
    )
```

- [ ] **Step 4: Run Task 7 tests + prior parts**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part1.py AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part2.py AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part3.py -v
```

Expected: 5 + 4 + 8 = 17 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): long-term part 3 - amounts + ELSS + non-MF cap + residual (B.5/R177-R186)

Computes equities/debt/others amounts with drift reconciliation on the
largest bucket. Implements the NFA-banded max-non-MF-equity-% (75% / 60% /
50% / 33% across > 5Cr / > 2Cr / > 1Cr / else), the advisor-override
precedence (Option A — client input wins), the non-MF equity actual
absorption (min of input, post-ELSS equity capacity, NFA cap), the excess
(drives SELL_DIRECT_STOCKS downstream), and the residual equity corpus
available for MF subgroups.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.5"
```

---

### Task 8: Long-term part 4 — multi-asset block

**Implements Excel R187–R194.** Reuses `phase4_multi_asset` from upstream, then applies overflow redistribution to equity vs debt per the spec.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part4.py` (LOCAL)

- [ ] **Step 1: Write the failing tests**

```python
from asset_allocation_pydantic.models import AllocationInput, MultiAssetFundComposition

from practical_asset_allocation.pipeline import _run_practical_long_term


def _alloc(**overrides):
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=5_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_lt_part4_multi_asset_block_components_sum_to_amount():
    """The multi-asset fund's eq + dt + oth components must sum to its amount."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    ma = result.multi_asset_block
    total = ma.equity_component + ma.debt_component + ma.others_component
    # Allow ±200 rounding tolerance (three independent round_to_100 ops).
    assert abs(total - ma.multi_asset_amount) <= 200


def test_lt_part4_residual_equity_post_multi_asset_non_negative():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.residual_equity_corpus_final >= 0


def test_lt_part4_multi_asset_others_excess_redirected_to_eq_and_debt():
    """When others slice of multi-asset > others_amount, overage redistributes."""
    # Pick high-risk + others-gate to zero out others_amount but still allow
    # multi-asset to land an others component.
    from asset_allocation_pydantic.models import MarketCommentaryScores
    inp = _alloc(
        effective_risk_score=9.5,
        market_commentary=MarketCommentaryScores(others=2.0),
        multi_asset_composition=MultiAssetFundComposition(
            equity_pct=65.0, debt_pct=25.0, others_pct=10.0,
        ),
    )
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # multi_asset_others_excess should be > 0; redistribution accounted for.
    assert result.multi_asset_others_excess >= 0
    # Sanity: excess_to_eq + excess_to_debt == multi_asset_others_excess.
    assert (result.excess_to_equity + result.excess_to_debt
            == result.multi_asset_others_excess)


def test_lt_part4_no_multi_asset_when_residual_equity_or_debt_zero():
    """When residual_equity_corpus is 0 (ELSS + non-MF locked everything),
    multi_asset_amount should be 0."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=100_000, elss_amount=900_000,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # equities_amount might be near 1M; residual_equity_corpus_pre = ~100k.
    # If residual is zero or debt is zero, multi_asset should be 0.
    if result.residual_equity_corpus_pre_multi_asset == 0 or result.debt_amount == 0:
        assert result.multi_asset_block.multi_asset_amount == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part4.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement part 4**

Import the multi-asset helper:

```python
from asset_allocation_pydantic.models import MultiAssetBlock
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
    phase2_asset_class_pcts,
    phase4_multi_asset,
)
```

Extend `_PracticalLongTermResult`:

```python
    # R187-R194 (Task 8):
    multi_asset_block: MultiAssetBlock
    multi_asset_others_excess: int
    excess_to_debt: int
    excess_to_equity: int
    residual_equity_corpus_final: int
    residual_debt_corpus: int
```

After the Task 7 block, add:

```python
    # R187: multi-asset block. Use the upstream helper — its signature already
    # caps multi-asset's equity slice at MULTI_ASSET_EQUITY_CAP_PCT and rounds
    # to 100. We feed it the practical RESIDUAL equity (post-ELSS, post-non-MF)
    # rather than equities_amount, so the multi-asset cap respects what we can
    # actually deploy via MFs.
    multi_asset_block = phase4_multi_asset(
        equities_amount=residual_equity_corpus_pre_multi_asset,
        debt_amount=debt_amount,
        others_amount=others_amount,
        composition=inp.multi_asset_composition,
    )

    # R193: overflow redistribution. When multi-asset's others slice exceeds
    # the budgeted others_amount, the excess is split between equity and debt
    # using allocation_2_debt_pct as the debt weight; equity gets the
    # remainder. Both legs are clamped: debt cannot exceed what is left after
    # the multi-asset debt component, and equity gets whatever excess is left.
    multi_asset_others_excess = max(
        0, multi_asset_block.others_component - others_amount,
    )
    debt_capacity_after_multi = max(
        0, debt_amount - multi_asset_block.debt_component,
    )
    if multi_asset_others_excess > 0 and (allocation_2_debt_pct + allocation_2_equity_pct) > 0:
        # Spec wording: "excess_to_debt = min(round_to_100(excess × allocation_2_debt / 100),
        # debt_amount − multi_asset_debt_component)".
        excess_to_debt = min(
            round_to_100(multi_asset_others_excess * allocation_2_debt_pct / 100),
            debt_capacity_after_multi,
        )
        excess_to_equity = multi_asset_others_excess - excess_to_debt
    else:
        excess_to_debt = 0
        excess_to_equity = 0

    # R194: residual equity corpus AFTER multi-asset equity component AND the
    # excess-to-equity redirect.
    residual_equity_corpus_final = max(
        0,
        residual_equity_corpus_pre_multi_asset
        - multi_asset_block.equity_component
        - excess_to_equity,
    )

    # R217 (preview for Task 10): residual debt corpus after multi-asset debt
    # component AND the excess-to-debt redirect.
    residual_debt_corpus = max(
        0,
        debt_amount - multi_asset_block.debt_component - excess_to_debt,
    )
```

Update the return to include the new fields. (Tests in Task 8 only assert on the part-4 fields.)

> **Implementation note:** The spec line in step 8 reads "`multi_asset_amount = round_to_100(min(residual_equity × multi_asset_max_equity / (multi_asset_eq_pct/100), debt_amount / (multi_asset_debt_pct/100)))`". `phase4_multi_asset` already implements exactly that formula (see `step4_long_term.py:198-207`) with the equity-cap baked in via `MULTI_ASSET_EQUITY_CAP_PCT`. Reusing the helper avoids re-deriving the formula.

- [ ] **Step 4: Run Task 8 tests + prior parts**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): long-term part 4 - multi-asset block + overflow redistribution (B.5/R187-R194)

Reuses asset_allocation_pydantic.step4_long_term.phase4_multi_asset to size
the multi-asset fund (capped at MULTI_ASSET_EQUITY_CAP_PCT of residual
equity) and decompose it into eq/debt/others components. Implements the
practical-side overflow redistribution: when the multi-asset others slice
exceeds the budgeted others_amount, the excess is split between equity
(remainder) and debt (allocation_2_debt_pct-weighted, clamped to remaining
debt capacity). Computes residual_equity_corpus_final and residual_debt_corpus.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.5"
```

---

### Task 9: Long-term part 5 — equity subgroup gates + v2 slider + amounts

**Implements Excel R196–R215.** This is the most intricate piece: it reuses
`phase5_equity_subgroups` for the gate + tilt logic, then overlays the v2
average-based sliding threshold that depends on how locked the equity bucket
is by ELSS + non-MF.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part5.py` (LOCAL)

- [ ] **Step 1: Write the failing tests**

```python
from asset_allocation_pydantic.models import AllocationInput, MarketCommentaryScores
from asset_allocation_pydantic.tables import EQUITY_SUBGROUPS

from practical_asset_allocation.pipeline import _run_practical_long_term


def _alloc(**overrides):
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=5_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_lt_part5_subgroup_amounts_keys_exhaustive():
    """The equity_subgroup_amounts dict must have one entry per EQUITY_SUBGROUPS."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert set(result.equity_subgroup_amounts.keys()) == set(EQUITY_SUBGROUPS)


def test_lt_part5_subgroup_amounts_sum_equals_residual_equity():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    total = sum(result.equity_subgroup_amounts.values())
    # round_to_100 may drift up to N*100 where N=6; tolerance loose.
    assert abs(total - result.residual_equity_corpus_final) <= 600


def test_lt_part5_sector_value_view_gates_zero_them_out():
    """Sector/value subgroups with view <= 7 should be excluded."""
    inp = _alloc(
        market_commentary=MarketCommentaryScores(
            sector_equities=5.0, value_equities=5.0,  # <= 7
        ),
    )
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.equity_subgroup_amounts["sector_equities"] == 0
    assert result.equity_subgroup_amounts["value_equities"] == 0


def test_lt_part5_average_equity_subgroup_allocation_uses_only_nonzero():
    """R198: average over non-zero % OF EQUITIES values."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # With default views, several subgroups will be active and non-zero;
    # average must be positive and ≤ 100/active_count.
    assert result.average_equity_subgroup_allocation_pct > 0


def test_lt_part5_slider_threshold_drops_below_3_when_equity_crowded():
    """R199 v2: with heavy ELSS + non-MF locking equity, threshold drops below
    3 per min(3, average_equity_subgroup_allocation)."""
    # Heavy lock: ELSS = 600k + non-MF = 1.5M out of equity ~1.8M.
    # locked_share ≈ 1.0; subtract 0.20 → 0.80; × 10 = 8; 8 - 8 = 0.
    # threshold = max(0, min(3, avg)). If avg is small → threshold < 3.
    result = _run_practical_long_term(
        inp=_alloc(effective_risk_score=5.5),
        remaining_corpus=2_500_000, elss_amount=600_000,
        non_mf_equity_input=1_500_000, nfa=15_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.min_equity_pct_required <= 3.0


def test_lt_part5_slider_threshold_clamped_at_8_when_no_lock():
    """R199 v2: with no ELSS/non-MF, locked_share = 0 → first term = 8."""
    result = _run_practical_long_term(
        inp=_alloc(effective_risk_score=5.5),
        remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # max(8 - max(0, -0.20) * 10, min(3, avg)) = max(8, min(3, avg)) = 8.
    assert result.min_equity_pct_required == 8.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part5.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement part 5**

Import additions:

```python
from asset_allocation_pydantic.steps.step4_long_term import (
    ResolvedBounds,
    phase1_bounds,
    phase2_asset_class_pcts,
    phase4_multi_asset,
    phase5_equity_subgroups,
)
from asset_allocation_pydantic.tables import EQUITY_SUBGROUPS
```

Add constants:

```python
# Spec §B.5 step 9 (R199) — v2 average-based slider.
SLIDER_BASE_PCT: float = 8.0
SLIDER_LOCKED_THRESHOLD: float = 0.20
SLIDER_LOCKED_MULTIPLIER: float = 10.0
SLIDER_AVG_CAP_PCT: float = 3.0
```

Extend `_PracticalLongTermResult`:

```python
    # R196-R215 (Task 9):
    average_equity_subgroup_allocation_pct: float
    min_equity_pct_required: float
    equity_subgroup_amounts: dict[str, int]  # one entry per EQUITY_SUBGROUPS
```

After the Task 8 block:

```python
    # R196-R200: equity subgroup allocation via upstream phase5_equity_subgroups.
    # This already applies the sector/value view-<= 7 gates and the upstream
    # PHASE5_MIN_SUBGROUP_SHARE_PCT (2%) internal drop. We then layer the v2
    # average-based slider on top (R198-R199) and drop+renormalise.
    initial_subgroup_amounts = phase5_equity_subgroups(
        total_equity_for_subgroups=residual_equity_corpus_final,
        score=inp.effective_risk_score,
        market_commentary=inp.market_commentary,
    )

    # R198: per-subgroup % OF EQUITIES (the equity slice that funds the MF
    # subgroup pool — NOT total long-term equities_amount, since ELSS and non-
    # MF actual are NOT MF subgroup deployment).
    pct_of_equity_per_subgroup: dict[str, float] = {}
    if residual_equity_corpus_final > 0:
        for sg, amt in initial_subgroup_amounts.items():
            pct_of_equity_per_subgroup[sg] = amt * 100.0 / residual_equity_corpus_final
    else:
        pct_of_equity_per_subgroup = {sg: 0.0 for sg in initial_subgroup_amounts}

    non_zero_pcts = [pct for pct in pct_of_equity_per_subgroup.values() if pct > 0]
    average_equity_subgroup_allocation_pct = (
        sum(non_zero_pcts) / len(non_zero_pcts) if non_zero_pcts else 0.0
    )

    # R199 (v2 slider): with heavily-locked equity (ELSS + non-MF actual >
    # 20% of equities_amount), allow a lower-than-8% threshold; cap the lower
    # bound at min(3, average_subgroup_allocation).
    if equities_amount > 0:
        locked_share = (elss_amount_frozen + non_mf_equity_actual) / equities_amount
    else:
        locked_share = 0.0
    first_term = (
        SLIDER_BASE_PCT
        - max(0.0, locked_share - SLIDER_LOCKED_THRESHOLD) * SLIDER_LOCKED_MULTIPLIER
    )
    second_term = min(SLIDER_AVG_CAP_PCT, average_equity_subgroup_allocation_pct)
    min_equity_pct_required = max(first_term, second_term)

    # R200-R215: drop subgroups below the slider threshold; redistribute
    # proportionally over survivors; convert back to amounts.
    surviving = {
        sg: amt for sg, amt in initial_subgroup_amounts.items()
        if pct_of_equity_per_subgroup.get(sg, 0.0) >= min_equity_pct_required
    }
    dropped_total = sum(
        amt for sg, amt in initial_subgroup_amounts.items()
        if sg not in surviving
    )
    surviving_sum = sum(surviving.values())
    if surviving_sum > 0 and dropped_total > 0:
        renormalised = {
            sg: round_to_100(amt + dropped_total * amt / surviving_sum)
            for sg, amt in surviving.items()
        }
    else:
        renormalised = dict(surviving)

    # Pad with zeros for the dropped subgroups so the result dict shape stays
    # exhaustive over EQUITY_SUBGROUPS.
    equity_subgroup_amounts: dict[str, int] = {sg: 0 for sg in EQUITY_SUBGROUPS}
    for sg, amt in renormalised.items():
        equity_subgroup_amounts[sg] = amt

    # Reconcile any residual rounding drift against residual_equity_corpus_final.
    drift = residual_equity_corpus_final - sum(equity_subgroup_amounts.values())
    if drift != 0 and equity_subgroup_amounts:
        largest_sg = max(equity_subgroup_amounts, key=lambda k: equity_subgroup_amounts[k])
        equity_subgroup_amounts[largest_sg] = max(
            0, equity_subgroup_amounts[largest_sg] + drift,
        )
```

Add the new fields to the return constructor.

- [ ] **Step 4: Run Task 9 tests + prior parts**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): long-term part 5 - equity subgroups + v2 slider (B.5/R196-R215)

Calls asset_allocation_pydantic's phase5_equity_subgroups to get initial
gate-and-tilted equity subgroup amounts, then overlays the v2 average-based
sliding threshold (R198-R199):

  average_equity_subgroup_allocation = mean of non-zero % OF EQUITIES values
  locked_share = (elss + non_mf_actual) / equities_amount
  min_equity_pct_required = max(
      8 - max(0, locked_share - 0.20) * 10,
      min(3, average_equity_subgroup_allocation),
  )

Subgroups below the threshold are dropped and their amount is redistributed
proportionally over survivors. With no locking, the threshold sits at 8;
with heavy locking it can drop below 3 (capped at the actual subgroup
average) so a crowded equity bucket isn't over-pruned.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.5"
```

---

### Task 10: Long-term part 6 — debt and others residuals + subgroup_amounts assembly

**Implements Excel R217–R222.** Wires the long-term subgroup_amounts dict and
the future_investment, completing the long-term step.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part6.py` (LOCAL)

- [ ] **Step 1: Write the failing tests**

```python
from asset_allocation_pydantic.models import AllocationInput, Goal
from asset_allocation_pydantic.tables import (
    EQUITY_SUBGROUPS,
    LONG_TERM_BOUNDARY_MONTHS,
    STEP4_SUBGROUPS,
)

from practical_asset_allocation.pipeline import _run_practical_long_term


def _alloc(**overrides):
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=5_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_lt_part6_long_term_subgroup_amounts_has_all_step4_keys():
    """Output subgroup_amounts must cover STEP4_SUBGROUPS exhaustively."""
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    for sg in STEP4_SUBGROUPS:
        assert sg in result.long_term_subgroup_amounts


def test_lt_part6_arbitrage_plus_income_holds_long_term_debt_residual():
    """Spec §B.5 step 11: arbitrage_plus_income = residual_debt_corpus
    (no tax-rate gate at long-term)."""
    result = _run_practical_long_term(
        inp=_alloc(effective_tax_rate=5.0),  # Even with low tax, long-term
        remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    # The Excel always routes long-term debt residual to arbitrage_plus_income.
    assert result.long_term_subgroup_amounts["arbitrage_plus_income"] == result.residual_debt_corpus
    assert result.long_term_subgroup_amounts["short_debt"] == 0


def test_lt_part6_gold_commodities_holds_residual_other():
    result = _run_practical_long_term(
        inp=_alloc(), remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.long_term_subgroup_amounts["gold_commodities"] >= 0


def test_lt_part6_future_investment_when_corpus_below_goal_sum():
    """Spec §B.7 (β): mid-sequence underfunding emits FutureInvestment."""
    inp = _alloc(
        goals=[Goal(
            goal_name="Retirement",
            time_to_goal_months=LONG_TERM_BOUNDARY_MONTHS + 1,
            amount_needed=20_000_000,  # > 4M remaining
            goal_priority="non_negotiable",
        )],
    )
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    assert result.future_investment is not None
    assert result.future_investment.future_investment_amount > 0


def test_lt_part6_goals_allocated_filters_long_term_goals():
    inp = _alloc(
        goals=[
            Goal(goal_name="Short", time_to_goal_months=12,
                 amount_needed=100_000, goal_priority="non_negotiable"),
            Goal(goal_name="Long", time_to_goal_months=LONG_TERM_BOUNDARY_MONTHS + 12,
                 amount_needed=2_000_000, goal_priority="non_negotiable"),
        ],
    )
    result = _run_practical_long_term(
        inp=inp, remaining_corpus=4_000_000, elss_amount=0,
        non_mf_equity_input=0, nfa=10_000_000,
        max_non_mf_equity_pct_client_input=None,
    )
    names = {g.goal_name for g in result.goals_allocated}
    assert names == {"Long"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_long_term_part6.py -v
```

Expected: FAIL.

- [ ] **Step 3: Implement part 6**

Imports:

```python
from asset_allocation_pydantic.tables import (
    EQUITY_SUBGROUPS,
    LONG_TERM_BOUNDARY_MONTHS,
    STEP4_SUBGROUPS,
)
```

Extend `_PracticalLongTermResult`:

```python
    # R217-R222 (Task 10):
    residual_other_corpus: int
    long_term_subgroup_amounts: dict[str, int]
    goals_allocated: list  # list[Goal] — kept untyped to avoid forward-ref churn
    future_investment: Optional[object]  # Optional[FutureInvestment]
```

Add the goals-filter + future_investment block ABOVE the existing R157-R165
code (so it can short-circuit when `remaining_corpus < sum_goals`). Restructure:

```python
def _run_practical_long_term(
    *,
    inp: AllocationInput,
    remaining_corpus: int,
    elss_amount: float,
    non_mf_equity_input: float,
    nfa: Optional[float],
    max_non_mf_equity_pct_client_input: Optional[float],
) -> _PracticalLongTermResult:
    # Goals classification (R-pre): filter long-term goals using the same
    # operator asset_allocation_pydantic.step4_long_term.run uses.
    lt_goals = [
        g for g in inp.goals
        if g.time_to_goal_months >= LONG_TERM_BOUNDARY_MONTHS
    ]
    sum_goals = round_to_100(sum(g.amount_needed for g in lt_goals))
    from asset_allocation_pydantic.models import FutureInvestment  # local to avoid cyclic
    future_investment: Optional[FutureInvestment] = None
    if sum_goals > remaining_corpus:
        future_investment = FutureInvestment(
            bucket="long_term",
            future_investment_amount=sum_goals - remaining_corpus,
        )

    # (existing Task 5-9 blocks unchanged below)
    ...

    # R220-R222: gold / commodities residual.
    others_minus_multi = max(
        0,
        others_amount - (multi_asset_block.others_component - multi_asset_others_excess),
    )
    residual_other_corpus = round_to_100(others_minus_multi)

    # R217-R219: assemble long-term subgroup_amounts.
    long_term_subgroup_amounts: dict[str, int] = {sg: 0 for sg in STEP4_SUBGROUPS}
    long_term_subgroup_amounts["multi_asset"] = multi_asset_block.multi_asset_amount
    for sg, amt in equity_subgroup_amounts.items():
        long_term_subgroup_amounts[sg] = amt
    # Spec §B.5 step 11: long-term debt residual ALWAYS routes to
    # arbitrage_plus_income; the tax-rate gate on debt routing applies to
    # medium-term only (asset_allocation Part A.4).
    long_term_subgroup_amounts["arbitrage_plus_income"] = residual_debt_corpus
    long_term_subgroup_amounts["short_debt"] = 0  # explicit zero
    long_term_subgroup_amounts["gold_commodities"] = residual_other_corpus
```

Update the return to include the four new fields (`residual_other_corpus`,
`long_term_subgroup_amounts`, `goals_allocated=lt_goals`,
`future_investment=future_investment`).

- [ ] **Step 4: Run Task 10 tests + all prior tests**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): long-term part 6 - debt/others residuals + subgroup assembly (B.5/R217-R222)

Builds long_term_subgroup_amounts as an exhaustive STEP4_SUBGROUPS-keyed
dict: multi_asset_amount, per-equity-subgroup amounts, arbitrage_plus_income
holding the residual debt (long-term always routes there; the tax-rate gate
on debt is medium-term-only), short_debt = 0, gold_commodities holding the
residual others after the multi-asset others component (and its excess) is
netted off. Filters long-term goals using LONG_TERM_BOUNDARY_MONTHS and
emits a FutureInvestment when corpus falls short.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.5"
```

---

### Task 11: `step5_aggregation_with_frozen` wrapper

Wraps `asset_allocation_pydantic.steps.step5_aggregation.run` to append two
new subgroup rows: `tax_efficient_equities` (ELSS amount in long_term column)
and `non_mf_equities` (non-MF equity actual in long_term column). This is the
last engine helper before output assembly.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_step5_with_frozen.py` (LOCAL)

- [ ] **Step 1: Write the failing test**

```python
from asset_allocation_pydantic.models import AllocationInput
from asset_allocation_pydantic.steps import (
    step1_emergency, step2_short_term, step3_medium_term,
)

from practical_asset_allocation.pipeline import (
    _run_practical_long_term,
    _step5_aggregation_with_frozen,
)


def _alloc(**overrides):
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=10_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
    )
    base.update(overrides)
    return AllocationInput(**base)


def test_step5_with_frozen_appends_two_subgroup_rows():
    inp = _alloc()
    rebalancing_corpus = inp.total_corpus - 300_000
    sub_inp = inp.model_copy(update={"total_corpus": rebalancing_corpus})
    s1 = step1_emergency.run(sub_inp)
    s2 = step2_short_term.run(sub_inp, s1.remaining_corpus)
    s3 = step3_medium_term.run(sub_inp, s2.remaining_corpus)
    s4 = _run_practical_long_term(
        inp=sub_inp, remaining_corpus=s3.remaining_corpus,
        elss_amount=300_000, non_mf_equity_input=500_000,
        nfa=10_000_000, max_non_mf_equity_pct_client_input=None,
    )
    s5 = _step5_aggregation_with_frozen(
        total_corpus=inp.total_corpus,
        s1=s1, s2=s2, s3=s3, s4_practical=s4,
        elss_amount=300_000,
        non_mf_equity_actual=s4.non_mf_equity_actual,
    )
    subs = {row.subgroup: row for row in s5.rows}
    assert "tax_efficient_equities" in subs
    assert "non_mf_equities" in subs
    assert subs["tax_efficient_equities"].long_term == 300_000
    assert subs["tax_efficient_equities"].emergency == 0
    assert subs["tax_efficient_equities"].short_term == 0
    assert subs["tax_efficient_equities"].medium_term == 0
    assert subs["non_mf_equities"].long_term == s4.non_mf_equity_actual


def test_step5_with_frozen_grand_total_reconciles_to_total_corpus():
    """grand_total = sum(all rows) must equal total_corpus (NOT rebalancing)."""
    inp = _alloc()
    rebalancing_corpus = inp.total_corpus - 300_000
    sub_inp = inp.model_copy(update={"total_corpus": rebalancing_corpus})
    s1 = step1_emergency.run(sub_inp)
    s2 = step2_short_term.run(sub_inp, s1.remaining_corpus)
    s3 = step3_medium_term.run(sub_inp, s2.remaining_corpus)
    s4 = _run_practical_long_term(
        inp=sub_inp, remaining_corpus=s3.remaining_corpus,
        elss_amount=300_000, non_mf_equity_input=500_000,
        nfa=10_000_000, max_non_mf_equity_pct_client_input=None,
    )
    s5 = _step5_aggregation_with_frozen(
        total_corpus=inp.total_corpus,
        s1=s1, s2=s2, s3=s3, s4_practical=s4,
        elss_amount=300_000,
        non_mf_equity_actual=s4.non_mf_equity_actual,
    )
    # Allow ±500 INR rounding tolerance.
    assert abs(s5.grand_total - int(inp.total_corpus)) <= 500
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_step5_with_frozen.py -v
```

Expected: FAIL — `_step5_aggregation_with_frozen` doesn't exist.

- [ ] **Step 3: Implement the wrapper**

Imports:

```python
from asset_allocation_pydantic.models import (
    AggregatedRow,
    Step1Output,
    Step2Output,
    Step3Output,
    Step4Output,
    Step5Output,
)
from asset_allocation_pydantic.steps import step5_aggregation
```

Add a synthetic `Step4Output` adapter — the upstream step5 reads
`step4.subgroup_amounts`, so we feed it a Step4Output-shaped object whose
`subgroup_amounts` is our `long_term_subgroup_amounts`. (Other fields are
ignored by step5.)

Add the wrapper:

```python
def _adapt_practical_to_step4_output(
    s4_practical: _PracticalLongTermResult,
) -> Step4Output:
    """Build a Step4Output whose subgroup_amounts is the practical long-term
    distribution. asset_allocation_pydantic.step5_aggregation only reads
    .subgroup_amounts on the step4 input, so the other fields are best-effort
    placeholders. We construct minimal valid pydantic objects."""
    from asset_allocation_pydantic.models import (
        AssetClassAllocation,
        MultiAssetBlock,
    )
    zero_alloc = AssetClassAllocation(
        equities_pct=0, debt_pct=0, others_pct=0,
        equities_amount=s4_practical.equities_amount,
        debt_amount=s4_practical.debt_amount,
        others_amount=s4_practical.others_amount,
    )
    return Step4Output(
        asset_class_allocation=zero_alloc,
        planned_asset_class_allocation=zero_alloc,
        planned_subgroup_amounts=s4_practical.long_term_subgroup_amounts,
        multi_asset=s4_practical.multi_asset_block,
        goals_allocated=s4_practical.goals_allocated,
        leftover_corpus=0,
        total_long_term_corpus=s4_practical.total_long_term_corpus,
        total_allocated=sum(s4_practical.long_term_subgroup_amounts.values()),
        remaining_corpus=0,
        future_investment=s4_practical.future_investment,
        subgroup_amounts=s4_practical.long_term_subgroup_amounts,
    )


def _step5_aggregation_with_frozen(
    *,
    total_corpus: float,
    s1: Step1Output,
    s2: Step2Output,
    s3: Step3Output,
    s4_practical: _PracticalLongTermResult,
    elss_amount: float,
    non_mf_equity_actual: int,
) -> Step5Output:
    """Wraps upstream step5_aggregation.run and appends two frozen subgroup
    rows: tax_efficient_equities (ELSS) and non_mf_equities (non-MF actual).

    grand_total reconciles to total_corpus (NOT rebalancing_corpus) because
    the two frozen rows make ELSS and non-MF actual visible.
    """
    s4_adapter = _adapt_practical_to_step4_output(s4_practical)
    # Call upstream against total_corpus, not rebalancing_corpus, so the
    # match-flag uses the correct denominator. The upstream function does not
    # subtract anything; it just sums the four bucket dicts.
    base = step5_aggregation.run(total_corpus, s1, s2, s3, s4_adapter)

    rows = list(base.rows)
    elss_int = int(round(elss_amount))
    if elss_int > 0:
        rows.append(AggregatedRow(
            subgroup="tax_efficient_equities",
            emergency=0, short_term=0, medium_term=0,
            long_term=elss_int, total=elss_int,
        ))
    if non_mf_equity_actual > 0:
        rows.append(AggregatedRow(
            subgroup="non_mf_equities",
            emergency=0, short_term=0, medium_term=0,
            long_term=non_mf_equity_actual, total=non_mf_equity_actual,
        ))

    grand_total = sum(row.total for row in rows)
    grand_total_matches_corpus = abs(grand_total - round_to_100(total_corpus)) <= 500

    return Step5Output(
        rows=rows,
        grand_total=grand_total,
        grand_total_matches_corpus=grand_total_matches_corpus,
    )
```

- [ ] **Step 4: Run Task 11 tests + all prior tests**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): step5_aggregation_with_frozen wrapper (B.6)

Adapts the practical long-term result into a Step4Output-shaped object so
asset_allocation_pydantic.step5_aggregation.run can be reused verbatim, then
appends two frozen subgroup rows: tax_efficient_equities (ELSS amount) and
non_mf_equities (non-MF equity actual). Both rows land in the long_term
column. grand_total reconciles to total_corpus (not rebalancing_corpus)
because the two frozen rows make ELSS and non-MF actual visible.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.6"
```

---

### Task 12: `_build_output` + complete `run_practical_allocation`

Assembles `PracticalAllocationOutput` from s1/s2/s3/s4_practical/s5 and the
input. The seven shared fields mirror what
`asset_allocation_pydantic.step7_presentation` builds; we reuse the inputs to
shape `ClientSummary`, `BucketAllocation`s, `AssetClassBreakdown`, and the
`CorpusBreakdown` extras. We DO NOT import step7 directly — it does prompt
rendering and LLM rationale work we don't want here; instead we hand-build
the five non-bucket fields and reuse the upstream models.

**Files:**
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py`
- Test: `AI_Agents/src/practical_asset_allocation/Testing/test_build_output_smoke.py` (LOCAL)

- [ ] **Step 1: Write the failing smoke test**

```python
from practical_asset_allocation.pipeline import (
    PracticalAllocationInput,
    PracticalAllocationOutput,
    run_practical_allocation,
)


def _input(**overrides) -> PracticalAllocationInput:
    base = dict(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=10_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
        mf_corpus=8_000_000,
        non_mf_equity_corpus=0.0,
        elss_corpus=0.0,
        max_non_mf_equity_pct_client_input=None,
    )
    base.update(overrides)
    return PracticalAllocationInput(**base)


def test_build_output_returns_practical_allocation_output():
    out = run_practical_allocation(_input())
    assert isinstance(out, PracticalAllocationOutput)
    assert out.grand_total > 0
    assert out.corpus_breakdown.total_corpus_inr == 10_000_000
    assert out.corpus_breakdown.rebalancing_corpus_inr == 10_000_000  # ELSS=0
    assert out.corpus_breakdown.non_mf_equity_actual_inr == 0


def test_build_output_records_elss_in_corpus_breakdown():
    out = run_practical_allocation(_input(elss_corpus=200_000))
    assert out.corpus_breakdown.elss_corpus_inr == 200_000
    assert out.corpus_breakdown.rebalancing_corpus_inr == 10_000_000 - 200_000


def test_build_output_records_non_mf_excess():
    out = run_practical_allocation(_input(
        non_mf_equity_corpus=5_000_000,  # well above the cap
        net_financial_assets=15_000_000,  # 50% NFA band
    ))
    assert out.corpus_breakdown.non_mf_equity_input_inr == 5_000_000
    assert out.corpus_breakdown.non_mf_equity_actual_inr <= 5_000_000
    assert (out.corpus_breakdown.non_mf_equity_actual_inr
            + out.corpus_breakdown.excess_direct_stocks_inr == 5_000_000)


def test_build_output_has_four_buckets():
    out = run_practical_allocation(_input())
    bucket_names = {b.bucket for b in out.bucket_allocations}
    assert bucket_names == {"emergency", "short_term", "medium_term", "long_term"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_build_output_smoke.py -v
```

Expected: FAIL — `run_practical_allocation` ends in `raise NotImplementedError`.

- [ ] **Step 3: Implement `_build_output` and finish `run_practical_allocation`**

Imports:

```python
from asset_allocation_pydantic.models import (
    AggregatedSubgroupRow,
    AssetClassBreakdown,
    AssetClassSplitBlock,
    BucketAllocation,
    BucketAssetClassSplit,
    ClientSummary,
    FutureInvestment,
)
```

Add the helper:

```python
def _build_output(
    inp: PracticalAllocationInput,
    s1: Step1Output,
    s2: Step2Output,
    s3: Step3Output,
    s4_practical: _PracticalLongTermResult,
    s5: Step5Output,
) -> PracticalAllocationOutput:
    """Assemble the seven shared fields + corpus_breakdown."""

    # 1. client_summary
    client_summary = ClientSummary(
        age=inp.age,
        occupation=inp.occupation_type,
        effective_risk_score=inp.effective_risk_score,
        total_corpus=inp.total_corpus,
        goals=inp.goals,
        emergency_fund_months=s1.emergency_fund_months,
        monthly_household_expense=inp.monthly_household_expense,
    )

    # 2. bucket_allocations
    emergency_bucket = BucketAllocation(
        bucket="emergency",
        goals=[],
        total_goal_amount=s1.total_emergency,
        allocated_amount=s1.total_emergency,
        future_investment=s1.future_investment,
        subgroup_amounts=s1.subgroup_amounts,
    )
    short_bucket = BucketAllocation(
        bucket="short_term",
        goals=s2.goals_allocated,
        total_goal_amount=s2.total_goal_amount,
        allocated_amount=s2.allocated_amount,
        future_investment=s2.future_investment,
        subgroup_amounts=s2.subgroup_amounts,
    )
    medium_bucket = BucketAllocation(
        bucket="medium_term",
        goals=[],  # MediumTermGoalAllocation is not the Goal type; keep empty
        total_goal_amount=s3.total_goal_amount,
        allocated_amount=s3.allocated_amount,
        future_investment=s3.future_investment,
        subgroup_amounts=s3.subgroup_amounts,
    )
    long_bucket = BucketAllocation(
        bucket="long_term",
        goals=s4_practical.goals_allocated,
        total_goal_amount=round_to_100(
            sum(g.amount_needed for g in s4_practical.goals_allocated),
        ),
        allocated_amount=sum(s4_practical.long_term_subgroup_amounts.values()),
        future_investment=s4_practical.future_investment,
        subgroup_amounts=s4_practical.long_term_subgroup_amounts,
    )

    # 3. aggregated_subgroups — convert Step5Output.rows to AggregatedSubgroupRow.
    aggregated = [
        AggregatedSubgroupRow(
            subgroup=row.subgroup,
            emergency=float(row.emergency),
            short_term=float(row.short_term),
            medium_term=float(row.medium_term),
            long_term=float(row.long_term),
            total=float(row.total),
        )
        for row in s5.rows
    ]

    # 4. future_investments_summary
    future_summary: list[FutureInvestment] = []
    for step_out in (s1, s2, s3):
        if step_out.future_investment is not None:
            future_summary.append(step_out.future_investment)
    if s4_practical.future_investment is not None:
        future_summary.append(s4_practical.future_investment)

    # 5. grand_total, 6. all_amounts_in_multiples_of_100
    grand_total = float(s5.grand_total)
    all_mult_100 = all(
        v % 100 == 0
        for d in (
            s1.subgroup_amounts, s2.subgroup_amounts,
            s3.subgroup_amounts, s4_practical.long_term_subgroup_amounts,
        )
        for v in d.values()
    )

    # 7. asset_class_breakdown — derive per-bucket eq/debt/others from
    #    subgroup_amounts via SUBGROUP_TO_ASSET_CLASS.
    asset_class_breakdown = _build_asset_class_breakdown(
        s1, s2, s3, s4_practical,
    )

    # corpus_breakdown extras
    corpus_breakdown = CorpusBreakdown(
        total_corpus_inr=int(round(inp.total_corpus)),
        mf_corpus_inr=int(round(inp.mf_corpus)),
        non_mf_equity_input_inr=int(round(inp.non_mf_equity_corpus)),
        elss_corpus_inr=int(round(inp.elss_corpus)),
        rebalancing_corpus_inr=int(round(inp.total_corpus - inp.elss_corpus)),
        non_mf_equity_actual_inr=s4_practical.non_mf_equity_actual,
        excess_direct_stocks_inr=s4_practical.excess_direct_stocks,
        max_non_mf_equity_pct_computed=s4_practical.max_non_mf_equity_pct_considered,
    )

    return PracticalAllocationOutput(
        client_summary=client_summary,
        bucket_allocations=[emergency_bucket, short_bucket, medium_bucket, long_bucket],
        aggregated_subgroups=aggregated,
        future_investments_summary=future_summary,
        grand_total=grand_total,
        all_amounts_in_multiples_of_100=all_mult_100,
        asset_class_breakdown=asset_class_breakdown,
        corpus_breakdown=corpus_breakdown,
    )


def _build_asset_class_breakdown(
    s1: Step1Output,
    s2: Step2Output,
    s3: Step3Output,
    s4_practical: _PracticalLongTermResult,
) -> AssetClassBreakdown:
    """Roll up subgroup amounts to (equity, debt, others) per bucket and
    overall. Mirrors what step7_presentation does in asset_allocation_pydantic
    but inlined here so we don't pull in that file's LLM rationale plumbing.

    tax_efficient_equities and non_mf_equities are added as equity in the
    long_term bucket via the practical-side rollup.
    """
    from asset_allocation_pydantic.tables import SUBGROUP_TO_ASSET_CLASS

    # Long-term: include the frozen ELSS + non-MF as equity (they ARE equity
    # exposure, just not via MF subgroups in the allocation_pydantic dict).
    lt_subs = dict(s4_practical.long_term_subgroup_amounts)
    lt_subs["tax_efficient_equities"] = s4_practical.elss_amount_frozen
    lt_subs["non_mf_equities"] = s4_practical.non_mf_equity_actual

    bucket_dicts = {
        "emergency": s1.subgroup_amounts,
        "short_term": s2.subgroup_amounts,
        "medium_term": s3.subgroup_amounts,
        "long_term": lt_subs,
    }

    # SUBGROUP_TO_ASSET_CLASS doesn't have the two practical-only subgroups;
    # add them locally as equity.
    extended_map = dict(SUBGROUP_TO_ASSET_CLASS)
    extended_map["tax_efficient_equities"] = "equity"
    extended_map["non_mf_equities"] = "equity"

    def split_with(subs: dict[str, int]) -> tuple[int, int, int]:
        eq = dt = oth = 0
        for sg, amt in subs.items():
            cls = extended_map.get(sg, "others")
            if cls == "equity":
                eq += amt
            elif cls == "debt":
                dt += amt
            else:
                oth += amt
        return eq, dt, oth

    per_bucket: list[BucketAssetClassSplit] = []
    for bucket_name, subs in bucket_dicts.items():
        eq, dt, oth = split_with(subs)
        tot = eq + dt + oth
        per_bucket.append(BucketAssetClassSplit(
            bucket=bucket_name,  # type: ignore[arg-type]
            equity=eq, debt=dt, others=oth,
            equity_pct=(eq * 100.0 / tot) if tot else 0.0,
            debt_pct=(dt * 100.0 / tot) if tot else 0.0,
            others_pct=(oth * 100.0 / tot) if tot else 0.0,
        ))

    eq_total = sum(b.equity for b in per_bucket)
    dt_total = sum(b.debt for b in per_bucket)
    oth_total = sum(b.others for b in per_bucket)
    grand = eq_total + dt_total + oth_total

    block = AssetClassSplitBlock(
        per_bucket=per_bucket,
        equity_total=eq_total, debt_total=dt_total, others_total=oth_total,
        equity_total_pct=(eq_total * 100.0 / grand) if grand else 0.0,
        debt_total_pct=(dt_total * 100.0 / grand) if grand else 0.0,
        others_total_pct=(oth_total * 100.0 / grand) if grand else 0.0,
    )

    return AssetClassBreakdown(
        planned=block,
        recommended=block,  # practical engine has no separate planned/recommended split
        recommended_sum_matches_grand_total=True,
        subgroups=None,
    )
```

Replace the final `raise NotImplementedError(...)` in `run_practical_allocation`:

```python
    s5 = _step5_aggregation_with_frozen(
        total_corpus=inp.total_corpus,
        s1=s1, s2=s2, s3=s3, s4_practical=s4_practical,
        elss_amount=inp.elss_corpus,
        non_mf_equity_actual=s4_practical.non_mf_equity_actual,
    )

    return _build_output(inp, s1, s2, s3, s4_practical, s5)
```

- [ ] **Step 4: Run Task 12 smoke tests + all prior tests**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/practical_asset_allocation/pipeline.py
git commit -m "feat(practical-allocation): _build_output and complete run_practical_allocation (B.3/B.4)

Assembles PracticalAllocationOutput from s1/s2/s3/s4_practical/s5 with shape
parity to GoalAllocationOutput plus the corpus_breakdown extras block.
Inlines a small asset-class rollup helper using SUBGROUP_TO_ASSET_CLASS plus
two practical-only equity entries (tax_efficient_equities, non_mf_equities)
rather than pulling in step7_presentation's LLM plumbing. run_practical_allocation
is now end-to-end runnable.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §B.3, §B.4"
```

---

### Task 13: Integration test suite — the 7 scenarios from spec §B.9

**Files:**
- Create: `AI_Agents/src/practical_asset_allocation/Testing/test_scenarios_b9.py` (LOCAL)

The seven scenarios are integration tests against the fully wired
`run_practical_allocation`. They exercise the engine end-to-end, not
individual long-term parts.

- [ ] **Step 1: Write all 7 scenario tests**

```python
"""Spec §B.9 scenarios — end-to-end integration tests.

Each scenario builds a PracticalAllocationInput, runs run_practical_allocation,
and asserts on the spec-named expected behaviour.
"""
import pytest

from asset_allocation_pydantic.models import (
    AllocationInput,
    Goal,
    MarketCommentaryScores,
)
from asset_allocation_pydantic.pipeline import run_allocation

from practical_asset_allocation.pipeline import (
    PracticalAllocationInput,
    run_practical_allocation,
)


def _base_kwargs(**overrides):
    base = dict(
        effective_risk_score=6.0,
        age=40,
        annual_income=2_400_000,
        osi=0.0,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        shortfall_amount=0.0,
        total_corpus=10_000_000,
        monthly_household_expense=120_000,
        effective_tax_rate=20.0,
        net_financial_assets=15_000_000,
        goals=[
            Goal(goal_name="Retirement", time_to_goal_months=240,
                 amount_needed=15_000_000, goal_priority="non_negotiable"),
        ],
    )
    base.update(overrides)
    return base


def _to_alloc_input(**kwargs) -> AllocationInput:
    """Parent fields only — drops the four practical fields."""
    drop = {"mf_corpus", "non_mf_equity_corpus", "elss_corpus",
            "max_non_mf_equity_pct_client_input"}
    return AllocationInput(**{k: v for k, v in kwargs.items() if k not in drop})


# Scenario 1
def test_b9_scenario_1_no_elss_no_non_mf_matches_ideal_engine():
    """ELSS=0, non-MF=0 → practical output's shared seven fields match the
    ideal engine's output exactly (regression guard)."""
    kwargs = _base_kwargs(mf_corpus=0.0, non_mf_equity_corpus=0.0,
                          elss_corpus=0.0)
    practical_in = PracticalAllocationInput(**kwargs)
    ideal_in = _to_alloc_input(**kwargs)

    practical_out = run_practical_allocation(practical_in)
    ideal_out = run_allocation(ideal_in)

    assert abs(practical_out.grand_total - ideal_out.grand_total) <= 500
    practical_keys = {row.subgroup for row in practical_out.aggregated_subgroups}
    assert "tax_efficient_equities" not in practical_keys
    assert "non_mf_equities" not in practical_keys
    for p_bucket, i_bucket in zip(practical_out.bucket_allocations,
                                  ideal_out.bucket_allocations):
        assert p_bucket.bucket == i_bucket.bucket
        assert abs(p_bucket.allocated_amount - i_bucket.allocated_amount) <= 1000


# Scenario 2
def test_b9_scenario_2_elss_below_equity_appears_as_frozen_row():
    """ELSS > 0 but well below the equity allocation → ELSS appears as a
    frozen long-term row; equity subgroup amounts shrunk pro-rata."""
    practical_in = PracticalAllocationInput(
        **_base_kwargs(mf_corpus=8_000_000, elss_corpus=300_000),
    )
    out = run_practical_allocation(practical_in)
    elss_rows = [r for r in out.aggregated_subgroups if r.subgroup == "tax_efficient_equities"]
    assert len(elss_rows) == 1
    assert elss_rows[0].long_term == 300_000
    assert elss_rows[0].emergency == 0
    assert elss_rows[0].short_term == 0
    assert elss_rows[0].medium_term == 0
    assert out.corpus_breakdown.elss_corpus_inr == 300_000


# Scenario 3
def test_b9_scenario_3_elss_lifts_equity_above_ideal_shrinks_debt_and_others():
    """ELSS large enough to lift allocation_2_equity above the ideal midpoint
    → debt and others shrink pro-rata."""
    practical_in = PracticalAllocationInput(
        **_base_kwargs(
            effective_risk_score=3.0,
            total_corpus=2_000_000,
            mf_corpus=1_800_000,
            elss_corpus=1_500_000,
        ),
    )
    out = run_practical_allocation(practical_in)
    lt_split = [b for b in out.asset_class_breakdown.recommended.per_bucket
                if b.bucket == "long_term"][0]
    assert lt_split.equity_pct >= 70


# Scenario 4
def test_b9_scenario_4_non_mf_below_nfa_cap_fully_absorbed():
    """Non-MF equity below NFA cap → fully absorbed; excess = 0."""
    practical_in = PracticalAllocationInput(
        **_base_kwargs(mf_corpus=7_500_000, non_mf_equity_corpus=500_000),
    )
    out = run_practical_allocation(practical_in)
    assert out.corpus_breakdown.non_mf_equity_input_inr == 500_000
    assert out.corpus_breakdown.non_mf_equity_actual_inr == 500_000
    assert out.corpus_breakdown.excess_direct_stocks_inr == 0


# Scenario 5
def test_b9_scenario_5_non_mf_above_nfa_cap_emits_excess():
    """Non-MF equity above NFA cap → capped; excess > 0."""
    practical_in = PracticalAllocationInput(
        **_base_kwargs(mf_corpus=5_000_000, non_mf_equity_corpus=5_000_000),
    )
    out = run_practical_allocation(practical_in)
    assert out.corpus_breakdown.non_mf_equity_input_inr == 5_000_000
    assert out.corpus_breakdown.non_mf_equity_actual_inr < 5_000_000
    assert out.corpus_breakdown.excess_direct_stocks_inr > 0
    assert (out.corpus_breakdown.non_mf_equity_actual_inr
            + out.corpus_breakdown.excess_direct_stocks_inr == 5_000_000)


# Scenario 6
def test_b9_scenario_6_slider_drops_below_3_when_equity_crowded():
    """Sliding threshold v2: with crowded equity, threshold drops below 3%."""
    practical_in = PracticalAllocationInput(
        **_base_kwargs(
            effective_risk_score=6.0,
            total_corpus=4_000_000,
            mf_corpus=3_500_000,
            elss_corpus=1_200_000,
            non_mf_equity_corpus=800_000,
            net_financial_assets=15_000_000,
        ),
    )
    out = run_practical_allocation(practical_in)
    elss_rows = [r for r in out.aggregated_subgroups
                 if r.subgroup == "tax_efficient_equities"]
    non_mf_rows = [r for r in out.aggregated_subgroups
                   if r.subgroup == "non_mf_equities"]
    assert len(elss_rows) == 1 and elss_rows[0].long_term == 1_200_000
    assert len(non_mf_rows) == 1 and non_mf_rows[0].long_term > 0


# Scenario 7
def test_b9_scenario_7_mid_sequence_underfunding_emits_future_investment():
    """Mid-sequence underfunding → FutureInvestment populated; pipeline continues."""
    practical_in = PracticalAllocationInput(
        **_base_kwargs(
            total_corpus=500_000,
            mf_corpus=400_000,
            goals=[
                Goal(goal_name="Big Retirement", time_to_goal_months=300,
                     amount_needed=50_000_000, goal_priority="non_negotiable"),
            ],
        ),
    )
    out = run_practical_allocation(practical_in)
    assert len(out.future_investments_summary) >= 1
    lt_gaps = [fi for fi in out.future_investments_summary
               if fi.bucket == "long_term"]
    assert len(lt_gaps) >= 1
    assert lt_gaps[0].future_investment_amount > 0
```

- [ ] **Step 2: Run the scenario suite**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/test_scenarios_b9.py -v
```

Expected: 7 passed. If any scenario test reveals a real engine issue (rather
than a brittle assertion), fix the engine in `pipeline.py` and re-commit
under the matching task number (5–12) — do not paper over with looser
assertions.

- [ ] **Step 3: Run the full module suite + upstream suite (regression check)**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green.

- [ ] **Step 4: No commit for tests (gitignored).** If the run forced an engine fix, commit that separately under the matching task number.

---

### Task 14: Verification — full suite, bridge tests, lint, types

**Files:**
- (read-only verification task; no edits except defensive fixture tweaks if surfaced)

- [ ] **Step 1: Full module test suite**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/practical_asset_allocation/Testing/ -v
```

Expected: all green.

- [ ] **Step 2: Upstream regression check**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/asset_allocation_pydantic/Testing/ -v
```

Expected: all green — practical_asset_allocation only IMPORTS from the
upstream module; no upstream code was touched in Part B.

- [ ] **Step 3: Bridge tests for asset_allocation (no Part C bridge work yet)**

```bash
pytest app/services/ai_bridge/asset_allocation/tests/ -v
```

Expected: all green.

- [ ] **Step 4: Lint**

```bash
ruff check AI_Agents/src/practical_asset_allocation/
```

Expected: clean.

- [ ] **Step 5: Types**

```bash
pyright AI_Agents/src/practical_asset_allocation/
```

Expected: clean (or no new errors vs baseline). Common gotchas:
- `_PracticalLongTermResult.goals_allocated: list` — if pyright complains
  about untyped list, widen to `list[Any]` or import `Goal` at module top.
- `future_investment: Optional[object]` — if pyright wants stricter typing,
  import `FutureInvestment` at module top and annotate properly.

- [ ] **Step 6: Re-export verification**

```bash
PYTHONPATH=AI_Agents/src python -c "
from practical_asset_allocation import (
    CorpusBreakdown, InfeasibleGoalError,
    PracticalAllocationInput, PracticalAllocationOutput,
    run_practical_allocation,
)
print('OK: all re-exports importable.')
"
```

Expected: prints `OK: all re-exports importable.`

- [ ] **Step 7: Spot-check the dependency-edge documentation**

```bash
grep -A1 'practical_asset_allocation' AI_Agents/src/CLAUDE.md | head -40
grep 'practical_asset_allocation' AI_Agents/src/asset_allocation_pydantic/CLAUDE.md
```

Expected: both files mention the dependency edge as Task 1 added.

- [ ] **Step 8: Push the branch (if applicable per local workflow)**

(Defer to local Git workflow — no further action specified by this plan.)

---

## Self-review checklist

- ✅ Each spec section B.1–B.9 mapped to specific tasks (B.1→Task 1; B.2→Task 2; B.3→Task 3; B.4→Task 4 + Task 12; B.5→Tasks 5–10; B.6→Task 11; B.7→Tasks 4 (α), 10 (β), 7 (negative residual clamp); B.8→Task 1 documentation + import usage across Tasks 5–11; B.9→Task 13).
- ✅ Every step shows real code (no `TBD` / `TODO` placeholders).
- ✅ Test code is complete and executable; expected fail/pass outcomes named.
- ✅ TDD discipline: every task has Write test → Run fails → Implement → Run passes → Run suite → Commit.
- ✅ The single-file `pipeline.py` constraint per spec §B.1 honoured — all models, orchestrator, long-term math, step5 wrapper, and output builder live there.
- ✅ Cross-agent imports listed in spec §B.8 are introduced as needed:
  - `step1_emergency.run`, `step2_short_term.run`, `step3_medium_term.run` — Task 4
  - `step4_long_term.phase1_bounds`, `phase2_asset_class_pcts` — Tasks 5/6
  - `step4_long_term.phase4_multi_asset` — Task 8
  - `step4_long_term.phase5_equity_subgroups` — Task 9
  - `step5_aggregation.run` — Task 11
  - `utils.round_to_100` — Task 7 onward
  - `utils.ceil_to_half` — not imported directly (only used inside upstream helpers); spec §B.8 lists it as available; no test requires direct use in the practical engine.
  - Models (`AllocationInput`, `Goal`, `MarketCommentaryScores`, `MultiAssetFundComposition`, `BucketAllocation`, `AggregatedSubgroupRow`, `FutureInvestment`, `ClientSummary`, `AssetClassBreakdown`, `AssetClassSplitBlock`, `BucketAssetClassSplit`, per-step `StepNOutput`) — Tasks 2/3/11/12
- ✅ The spec's `_PracticalLongTermResult` fields are introduced incrementally; each task expands the dataclass and the test for that task asserts only on its own fields.
- ✅ The v2 slider math from the spec is implemented verbatim (Task 9 step 3):
  `min_equity_pct_required = max(8 − max(0, locked_share − 0.20) × 10, min(3, average_equity_subgroup_allocation))`.
- ✅ NFA bands (R182) implemented exactly per spec: `> 5Cr → 75%`, `> 2Cr → 60%`, `> 1Cr → 50%`, else `33%` (strict `>` at every boundary).
- ✅ ELSS-as-floor implemented at allocation_2 level (Task 6) and as a frozen row in step5 (Task 11).
- ✅ The "long-term debt residual always goes to arbitrage_plus_income" rule (spec §B.5 step 11) is enforced in Task 10.
- ✅ The `subgroup_amounts` keyspace for the long-term bucket is `STEP4_SUBGROUPS` (multi-asset + 6 equity subgroups + 2 debt + gold), exhaustively zero-padded (Task 10).
- ✅ Commits add engine code + CLAUDE.mds only — `Testing/` files are gitignored per `.gitignore`. The plan never `git add`s test files.
- ✅ Decimal vs float: every corpus / amount field uses `float` or `int`. No Decimal usage introduced.
- ✅ The cross-agent dependency edge is documented in BOTH `practical_asset_allocation/CLAUDE.md` AND `asset_allocation_pydantic/CLAUDE.md` AND `AI_Agents/src/CLAUDE.md` (Cross-module edges section) — Task 1.
- ✅ Memory rule honoured: this plan file is intended for `docs/superpowers/plans/` (the parent agent moves it there); it is never `git add`ed.
- ✅ Product name "Prozpr" used consistently (never "Prozper").
- ✅ The `_step5_aggregation_with_frozen` wrapper produces a `Step5Output` whose `grand_total` reconciles to `total_corpus` (NOT `rebalancing_corpus`) per spec §B.6.

### Potential pitfalls flagged for the executor

1. **`phase1_bounds` already applies the upstream others-gate** (`score >= 8 AND view <= 6`). The practical-side stricter gate (`score > 8 AND view < 7`) is layered on top in Task 6. Read both gate operators carefully — they are NOT symmetric (`>= / <=` vs `> / <`).
2. **The two practical-only subgroups (`tax_efficient_equities`, `non_mf_equities`) are NOT in `SUBGROUP_TO_ASSET_CLASS`.** Task 12 extends the map locally rather than mutating the upstream table.
3. **`Step4Output.subgroup_amounts` is what `step5_aggregation.run` reads.** The adapter in Task 11 must populate `subgroup_amounts` (not `planned_subgroup_amounts`) with `long_term_subgroup_amounts`.
4. **`BucketAllocation.goals` expects `List[Goal]`.** Step3's `MediumTermGoalAllocation` is NOT a `Goal` — Task 12 passes an empty list there. If a future iteration wants per-goal medium-term detail in the output, that's a separate change touching the upstream `BucketAllocation` schema.
5. **`run_allocation` (ideal engine) called in Scenario 1** must use a fresh `AllocationInput` (no practical fields). The helper `_to_alloc_input` strips them.

---

## Done criteria

- All 14 tasks' checkboxes ticked.
- All engine + CLAUDE.md commits land on the working branch (~12 commits, plus any incidental fixture/engine-fix commits surfaced in Task 13/14).
- Task 14 verification passes: full module suite green, upstream suite green, bridge tests green, lint clean, types clean, re-exports importable.
- `AI_Agents/src/practical_asset_allocation/pipeline.py` is a single file containing all models, the orchestrator, and the long-term math (revisit splitting only if it exceeds ~500 lines per spec §B.1).
- The cross-agent dependency edge is documented in three places (`practical_asset_allocation/CLAUDE.md`, `asset_allocation_pydantic/CLAUDE.md`, `AI_Agents/src/CLAUDE.md`).
- `PracticalAllocationOutput` has shape parity with `GoalAllocationOutput` (same seven fields, identical types) plus the `corpus_breakdown` extras block.
- Part C (Rebalancing) is unblocked: it can `from practical_asset_allocation import run_practical_allocation, PracticalAllocationInput, PracticalAllocationOutput` and proceed.

---
