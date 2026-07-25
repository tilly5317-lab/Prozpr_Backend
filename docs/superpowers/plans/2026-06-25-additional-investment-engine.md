# Additional-Investment Engine Implementation Plan (Plan 1 of 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the pure-Python `additional_investment` engine that turns a deploy amount (lumpsum or monthly SIP) into a BUY-only, holdings-aware list of specific funds, using the customer's existing per-bucket allocation shape and goal-funding status.

**Architecture:** A self-contained agent module under `AI_Agents/src/additional_investment/`, mirroring `practical_asset_allocation`. It imports **no** peer agent — the app layer (Plan 3) adapts real allocation/goal/ranking/holdings data into this engine's own pydantic input. Two pure stages: (a) the two-branch ratio that splits the deploy amount across asset subgroups, (b) BUY-only fund selection over the ranked fund list with per-fund caps and holdings top-up.

**Tech Stack:** Python 3.12 (`.venv-mac`), pydantic v2. No LLM, no I/O, no DB. Tests via pytest.

## Global Constraints

- **No LLM, no I/O in this module** — pure functions over pydantic models.
- **No cross-agent imports** — this module must not import `practical_asset_allocation`, `cashflow_statement`, `Rebalancing`, or any app code (`AI_Agents/src/CLAUDE.md` peer rule). It defines its own input models; the caller populates them.
- **Caps are passed in, never hardcoded** — the production caller sources cap percentages from `AI_Agents/src/Rebalancing/tables.py`/`config.py` (multi_asset 20, short_debt/arbitrage 30, default 10) to keep one source of truth.
- **Emergency bucket is always excluded** from fresh money.
- **Round per-fund amounts down to multiples of ₹100.**
- **Run tests from the `Prozpr_Backend/` directory:** `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing -v`.
- **All commit commands assume the working directory is the git repo root (`Prozpr_Backend/`).**
- **`Testing/` dirs are gitignored repo-wide** (`.gitignore:104` → `/AI_Agents/src/*/Testing/`); no agent module tracks its tests. So **commit source files only** — do NOT `git add` test files (a plain `git add` of a test path fails as "ignored"). Tests still run locally and are the implementer's test evidence; they just stay untracked, matching every sibling module.

---

### Task 1: Module scaffold + I/O models

**Files:**
- Create: `AI_Agents/src/additional_investment/__init__.py`
- Create: `AI_Agents/src/additional_investment/models.py`
- Create: `AI_Agents/src/additional_investment/Testing/__init__.py`
- Create: `AI_Agents/src/additional_investment/Testing/conftest.py`
- Test: `AI_Agents/src/additional_investment/Testing/test_models.py`

**Interfaces:**
- Produces: `Cadence`, `BranchUsed` (enums); `SubgroupBucketAmounts`, `RankedFund`, `Holding`, `AdditionalInvestmentInput`, `FundBuy`, `SubgroupTarget`, `AdditionalInvestmentOutput` (pydantic models). Field names/types below are relied on by Tasks 2–4 and by Plan 3's app adapter.

- [ ] **Step 1: Create the test-path shim so the package imports under pytest**

`AI_Agents/src/additional_investment/Testing/conftest.py`:
```python
import sys
import pathlib

# Put AI_Agents/src on sys.path so `import additional_investment` works under pytest,
# regardless of any root conftest. Harmless if the path is already present.
_SRC = pathlib.Path(__file__).resolve().parents[2]  # .../AI_Agents/src
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
```
Also create empty `AI_Agents/src/additional_investment/Testing/__init__.py`.

- [ ] **Step 2: Write the failing test**

`AI_Agents/src/additional_investment/Testing/test_models.py`:
```python
import pytest
from pydantic import ValidationError

from additional_investment.models import (
    Cadence,
    BranchUsed,
    SubgroupBucketAmounts,
    RankedFund,
    Holding,
    AdditionalInvestmentInput,
    FundBuy,
    SubgroupTarget,
    AdditionalInvestmentOutput,
)


def test_cadence_and_branch_values():
    assert Cadence.LUMPSUM.value == "lumpsum"
    assert Cadence.SIP_MONTHLY.value == "sip_monthly"
    assert BranchUsed.LONG_TERM.value == "long_term"
    assert BranchUsed.TOTAL_MINUS_EMERGENCY.value == "total_minus_emergency"


def test_input_rejects_non_positive_amount():
    with pytest.raises(ValidationError):
        AdditionalInvestmentInput(
            deploy_amount_inr=0,
            cadence=Cadence.LUMPSUM,
            subgroups=[],
            medium_term_fulfilled=True,
            ranked_funds=[],
            resulting_corpus_inr=100000,
        )


def test_input_defaults():
    inp = AdditionalInvestmentInput(
        deploy_amount_inr=500000,
        cadence=Cadence.LUMPSUM,
        subgroups=[SubgroupBucketAmounts(subgroup="large_cap_equities", long_term=100.0, total=100.0)],
        medium_term_fulfilled=True,
        ranked_funds=[],
        resulting_corpus_inr=500000,
    )
    assert inp.holdings == []
    assert inp.default_cap_pct == 10.0
    assert inp.rounding_multiple_inr == 100
    assert inp.cap_pct_by_subgroup == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'additional_investment'`

- [ ] **Step 4: Write the models**

`AI_Agents/src/additional_investment/models.py`:
```python
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Cadence(str, Enum):
    LUMPSUM = "lumpsum"
    SIP_MONTHLY = "sip_monthly"


class BranchUsed(str, Enum):
    LONG_TERM = "long_term"
    TOTAL_MINUS_EMERGENCY = "total_minus_emergency"


class SubgroupBucketAmounts(BaseModel):
    """Per-subgroup amounts across horizon buckets, lifted from the practical
    allocation output (AggregatedSubgroupRow) on the customer's CURRENT corpus."""

    subgroup: str
    emergency: float = 0.0
    short_term: float = 0.0
    medium_term: float = 0.0
    long_term: float = 0.0
    total: float = 0.0


class RankedFund(BaseModel):
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    recommended_fund: str


class Holding(BaseModel):
    isin: str
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    present_amount_inr: float
    rank: Optional[int] = None       # rank in the ranking list if matched
    rating: Optional[float] = None   # 0..10; >= 5 is acceptable
    force_exit: bool = False         # rank-9999 sentinel — never top up


class AdditionalInvestmentInput(BaseModel):
    deploy_amount_inr: float = Field(gt=0)
    cadence: Cadence
    subgroups: list[SubgroupBucketAmounts]
    medium_term_fulfilled: bool
    ranked_funds: list[RankedFund]
    holdings: list[Holding] = Field(default_factory=list)
    resulting_corpus_inr: float = Field(gt=0)  # existing holdings + deploy, for caps
    cap_pct_by_subgroup: dict[str, float] = Field(default_factory=dict)
    default_cap_pct: float = 10.0
    rounding_multiple_inr: int = 100


class SubgroupTarget(BaseModel):
    subgroup: str
    ratio: float
    target_inr: float


class FundBuy(BaseModel):
    recommended_fund: str
    isin: str
    sub_category: str
    asset_subgroup: str
    amount_inr: float
    monthly_amount_inr: Optional[float] = None  # set when cadence == sip_monthly
    reason: str


class AdditionalInvestmentOutput(BaseModel):
    branch_used: BranchUsed
    cadence: Cadence
    deploy_amount_inr: float
    per_subgroup_target: list[SubgroupTarget]
    buys: list[FundBuy]
```

`AI_Agents/src/additional_investment/__init__.py`:
```python
from additional_investment.models import (  # noqa: F401
    Cadence,
    BranchUsed,
    SubgroupBucketAmounts,
    RankedFund,
    Holding,
    AdditionalInvestmentInput,
    SubgroupTarget,
    FundBuy,
    AdditionalInvestmentOutput,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_models.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/additional_investment/
git commit -m "feat(additional_investment): scaffold module + I/O models"
```

---

### Task 2: Two-branch ratio (split the deploy amount across subgroups)

**Files:**
- Create: `AI_Agents/src/additional_investment/ratio.py`
- Test: `AI_Agents/src/additional_investment/Testing/test_ratio.py`

**Interfaces:**
- Consumes: `SubgroupBucketAmounts`, `SubgroupTarget`, `BranchUsed` (Task 1).
- Produces: `compute_branch(medium_term_fulfilled: bool) -> BranchUsed`; `compute_targets(subgroups: list[SubgroupBucketAmounts], medium_term_fulfilled: bool, deploy_amount: float) -> tuple[BranchUsed, list[SubgroupTarget]]`. Used by Task 4.

- [ ] **Step 1: Write the failing test**

`AI_Agents/src/additional_investment/Testing/test_ratio.py`:
```python
from additional_investment.models import SubgroupBucketAmounts, BranchUsed
from additional_investment.ratio import compute_branch, compute_targets


def _rows():
    # large_cap: heavy long-term; debt: split across emergency + medium; gold: emergency only
    return [
        SubgroupBucketAmounts(subgroup="large_cap_equities", emergency=0, medium_term=0, long_term=300, total=300),
        SubgroupBucketAmounts(subgroup="short_debt", emergency=100, medium_term=100, long_term=100, total=300),
        SubgroupBucketAmounts(subgroup="gold", emergency=200, medium_term=0, long_term=0, total=200),
    ]


def test_branch_selection():
    assert compute_branch(True) is BranchUsed.LONG_TERM
    assert compute_branch(False) is BranchUsed.TOTAL_MINUS_EMERGENCY


def test_long_term_branch_uses_long_term_weights():
    branch, targets = compute_targets(_rows(), medium_term_fulfilled=True, deploy_amount=400000)
    assert branch is BranchUsed.LONG_TERM
    by = {t.subgroup: t for t in targets}
    # long_term weights: large_cap 300, short_debt 100, gold 0 -> total 400
    assert by["large_cap_equities"].ratio == 0.75
    assert by["short_debt"].ratio == 0.25
    assert "gold" not in by  # zero long-term weight -> no target
    assert round(sum(t.target_inr for t in targets)) == 400000


def test_total_minus_emergency_branch():
    branch, targets = compute_targets(_rows(), medium_term_fulfilled=False, deploy_amount=600000)
    assert branch is BranchUsed.TOTAL_MINUS_EMERGENCY
    by = {t.subgroup: t for t in targets}
    # total-emergency: large_cap 300, short_debt 200, gold 0 -> total 500
    assert by["large_cap_equities"].ratio == 0.6
    assert by["short_debt"].ratio == 0.4
    assert "gold" not in by  # gold is pure emergency -> excluded
    assert round(sum(t.target_inr for t in targets)) == 600000


def test_empty_when_no_weight():
    rows = [SubgroupBucketAmounts(subgroup="gold", emergency=200, total=200)]
    branch, targets = compute_targets(rows, medium_term_fulfilled=True, deploy_amount=100000)
    assert targets == []  # only emergency weight -> nothing to deploy into
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_ratio.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'additional_investment.ratio'`

- [ ] **Step 3: Write the implementation**

`AI_Agents/src/additional_investment/ratio.py`:
```python
from __future__ import annotations

from additional_investment.models import (
    BranchUsed,
    SubgroupBucketAmounts,
    SubgroupTarget,
)


def compute_branch(medium_term_fulfilled: bool) -> BranchUsed:
    return BranchUsed.LONG_TERM if medium_term_fulfilled else BranchUsed.TOTAL_MINUS_EMERGENCY


def _weight(row: SubgroupBucketAmounts, branch: BranchUsed) -> float:
    if branch is BranchUsed.LONG_TERM:
        return max(row.long_term, 0.0)
    return max(row.total - row.emergency, 0.0)


def compute_targets(
    subgroups: list[SubgroupBucketAmounts],
    medium_term_fulfilled: bool,
    deploy_amount: float,
) -> tuple[BranchUsed, list[SubgroupTarget]]:
    branch = compute_branch(medium_term_fulfilled)
    weights = {r.subgroup: _weight(r, branch) for r in subgroups}
    total_weight = sum(weights.values())
    targets: list[SubgroupTarget] = []
    if total_weight <= 0:
        return branch, targets
    for row in subgroups:
        w = weights[row.subgroup]
        if w <= 0:
            continue
        ratio = w / total_weight
        targets.append(
            SubgroupTarget(subgroup=row.subgroup, ratio=ratio, target_inr=ratio * deploy_amount)
        )
    return branch, targets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_ratio.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
# Testing/ is gitignored (see Global Constraints) — commit the source file only.
git add AI_Agents/src/additional_investment/ratio.py
git commit -m "feat(additional_investment): two-branch subgroup ratio"
```

---

### Task 3: BUY-only, holdings-aware fund selection

**Files:**
- Create: `AI_Agents/src/additional_investment/selection.py`
- Test: `AI_Agents/src/additional_investment/Testing/test_selection.py`

**Interfaces:**
- Consumes: `SubgroupTarget`, `RankedFund`, `Holding`, `FundBuy` (Task 1).
- Produces: `select_funds(targets, ranked_funds, holdings, resulting_corpus, cap_pct_by_subgroup, default_cap_pct, rounding_multiple) -> list[FundBuy]`. Used by Task 4.

- [ ] **Step 1: Write the failing test**

`AI_Agents/src/additional_investment/Testing/test_selection.py`:
```python
from additional_investment.models import SubgroupTarget, RankedFund, Holding
from additional_investment.selection import select_funds


def _ranked():
    return [
        RankedFund(asset_subgroup="large_cap_equities", sub_category="Large Cap Fund", rank=1, isin="INF001", scheme_code="L1", recommended_fund="Alpha Large Cap"),
        RankedFund(asset_subgroup="large_cap_equities", sub_category="Large Cap Fund", rank=2, isin="INF002", scheme_code="L2", recommended_fund="Beta Large Cap"),
    ]


def test_new_investor_buys_rank1():
    targets = [SubgroupTarget(subgroup="large_cap_equities", ratio=1.0, target_inr=50000)]
    buys = select_funds(targets, _ranked(), [], resulting_corpus=500000,
                        cap_pct_by_subgroup={}, default_cap_pct=10.0, rounding_multiple=100)
    assert len(buys) == 1
    assert buys[0].isin == "INF001"
    assert buys[0].amount_inr == 50000


def test_cap_overflow_spills_to_rank2():
    # cap 10% of 500000 = 50000 per fund; target 70000 -> 50000 rank1 + 20000 rank2
    targets = [SubgroupTarget(subgroup="large_cap_equities", ratio=1.0, target_inr=70000)]
    buys = select_funds(targets, _ranked(), [], resulting_corpus=500000,
                        cap_pct_by_subgroup={}, default_cap_pct=10.0, rounding_multiple=100)
    assert [(b.isin, b.amount_inr) for b in buys] == [("INF001", 50000), ("INF002", 20000)]


def test_tops_up_acceptable_existing_holding_first():
    holdings = [Holding(isin="INF002", asset_subgroup="large_cap_equities", sub_category="Large Cap Fund",
                        recommended_fund="Beta Large Cap", present_amount_inr=10000, rank=2, rating=7)]
    targets = [SubgroupTarget(subgroup="large_cap_equities", ratio=1.0, target_inr=30000)]
    buys = select_funds(targets, _ranked(), holdings, resulting_corpus=500000,
                        cap_pct_by_subgroup={}, default_cap_pct=10.0, rounding_multiple=100)
    # existing INF002 topped up first (cap 50000, present 10000 -> headroom 40000 covers 30000)
    assert len(buys) == 1
    assert buys[0].isin == "INF002"
    assert buys[0].amount_inr == 30000
    assert "existing" in buys[0].reason.lower()


def test_force_exit_holding_not_topped_up():
    holdings = [Holding(isin="INF002", asset_subgroup="large_cap_equities", sub_category="Large Cap Fund",
                        recommended_fund="Beta Large Cap", present_amount_inr=0, rank=2, rating=7, force_exit=True)]
    targets = [SubgroupTarget(subgroup="large_cap_equities", ratio=1.0, target_inr=20000)]
    buys = select_funds(targets, _ranked(), holdings, resulting_corpus=500000,
                        cap_pct_by_subgroup={}, default_cap_pct=10.0, rounding_multiple=100)
    assert buys[0].isin == "INF001"  # skipped the force-exit fund, bought rank-1


def test_rounds_down_to_multiple():
    targets = [SubgroupTarget(subgroup="large_cap_equities", ratio=1.0, target_inr=12345)]
    buys = select_funds(targets, _ranked(), [], resulting_corpus=500000,
                        cap_pct_by_subgroup={}, default_cap_pct=10.0, rounding_multiple=100)
    assert buys[0].amount_inr == 12300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_selection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'additional_investment.selection'`

- [ ] **Step 3: Write the implementation**

`AI_Agents/src/additional_investment/selection.py`:
```python
from __future__ import annotations

from dataclasses import dataclass

from additional_investment.models import FundBuy, Holding, RankedFund, SubgroupTarget


@dataclass
class _Candidate:
    isin: str
    recommended_fund: str
    sub_category: str
    reason: str


def _round_down(amount: float, multiple: int) -> float:
    if multiple <= 0:
        return amount
    return float(int(amount // multiple) * multiple)


def _cap_amount(subgroup: str, resulting_corpus: float,
                cap_pct_by_subgroup: dict[str, float], default_cap_pct: float) -> float:
    pct = cap_pct_by_subgroup.get(subgroup, default_cap_pct)
    return resulting_corpus * pct / 100.0


def _ordered_candidates(subgroup: str,
                        ranked_by_sg: dict[str, list[RankedFund]],
                        held_by_sg: dict[str, list[Holding]]) -> list[_Candidate]:
    out: list[_Candidate] = []
    seen: set[str] = set()
    # 1) acceptable existing holdings first, biggest position first (consolidate)
    for h in sorted(held_by_sg.get(subgroup, []), key=lambda x: x.present_amount_inr, reverse=True):
        if h.force_exit:
            continue
        acceptable = (h.rank is not None) or (h.rating is not None and h.rating >= 5)
        if not acceptable:
            continue
        out.append(_Candidate(h.isin, h.recommended_fund, h.sub_category,
                              "Top-up of your existing holding in this category"))
        seen.add(h.isin)
    # 2) ranked funds rank-1..N, skipping any already added as a holding
    for f in ranked_by_sg.get(subgroup, []):
        if f.isin in seen:
            continue
        out.append(_Candidate(f.isin, f.recommended_fund, f.sub_category,
                              "Recommended fund for this category"))
        seen.add(f.isin)
    return out


def select_funds(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    holdings: list[Holding],
    resulting_corpus: float,
    cap_pct_by_subgroup: dict[str, float],
    default_cap_pct: float,
    rounding_multiple: int,
) -> list[FundBuy]:
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    held_by_sg: dict[str, list[Holding]] = {}
    for h in holdings:
        held_by_sg.setdefault(h.asset_subgroup, []).append(h)

    buys: list[FundBuy] = []
    for t in targets:
        cap_amt = _cap_amount(t.subgroup, resulting_corpus, cap_pct_by_subgroup, default_cap_pct)
        present_by_isin = {h.isin: h.present_amount_inr for h in held_by_sg.get(t.subgroup, [])}
        bought_by_isin: dict[str, float] = {}
        remaining = t.target_inr
        for cand in _ordered_candidates(t.subgroup, ranked_by_sg, held_by_sg):
            if remaining < rounding_multiple:
                break
            present = present_by_isin.get(cand.isin, 0.0)
            already = bought_by_isin.get(cand.isin, 0.0)
            headroom = cap_amt - present - already
            if headroom < rounding_multiple:
                continue
            buy_amt = _round_down(min(remaining, headroom), rounding_multiple)
            if buy_amt < rounding_multiple:
                continue
            bought_by_isin[cand.isin] = already + buy_amt
            buys.append(FundBuy(
                recommended_fund=cand.recommended_fund,
                isin=cand.isin,
                sub_category=cand.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=buy_amt,
                reason=cand.reason,
            ))
            remaining -= buy_amt
    return buys
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_selection.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
# Testing/ is gitignored (see Global Constraints) — commit the source file only.
git add AI_Agents/src/additional_investment/selection.py
git commit -m "feat(additional_investment): BUY-only holdings-aware fund selection"
```

---

### Task 4: Pipeline entry `run_additional_investment` + cadence

**Files:**
- Create: `AI_Agents/src/additional_investment/pipeline.py`
- Modify: `AI_Agents/src/additional_investment/__init__.py` (add `run_additional_investment` export)
- Test: `AI_Agents/src/additional_investment/Testing/test_pipeline.py`

**Interfaces:**
- Consumes: `compute_targets` (Task 2), `select_funds` (Task 3), all models (Task 1).
- Produces: `run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput`. This is the module's public entry, consumed by Plan 3's app adapter.

- [ ] **Step 1: Write the failing test**

`AI_Agents/src/additional_investment/Testing/test_pipeline.py`:
```python
from additional_investment.models import (
    AdditionalInvestmentInput, Cadence, BranchUsed,
    SubgroupBucketAmounts, RankedFund,
)
from additional_investment import run_additional_investment


def _input(cadence, amount):
    return AdditionalInvestmentInput(
        deploy_amount_inr=amount,
        cadence=cadence,
        subgroups=[
            SubgroupBucketAmounts(subgroup="large_cap_equities", long_term=300, total=300),
            SubgroupBucketAmounts(subgroup="short_debt", long_term=100, total=200, emergency=100),
        ],
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(asset_subgroup="large_cap_equities", sub_category="Large Cap Fund", rank=1, isin="INF001", scheme_code="L1", recommended_fund="Alpha Large Cap"),
            RankedFund(asset_subgroup="short_debt", sub_category="Short Duration Fund", rank=1, isin="INF010", scheme_code="D1", recommended_fund="Alpha Short Debt"),
        ],
        resulting_corpus_inr=2000000,
        cap_pct_by_subgroup={"short_debt": 30.0},
        default_cap_pct=15.0,  # 15% of 2,000,000 = 300,000 large-cap cap, fits the 300,000 target with one fund
    )


def test_lumpsum_end_to_end():
    out = run_additional_investment(_input(Cadence.LUMPSUM, 400000))
    assert out.branch_used is BranchUsed.LONG_TERM
    # long_term weights 300/100 -> 0.75 / 0.25 of 400000
    by = {b.asset_subgroup: b for b in out.buys}
    assert by["large_cap_equities"].amount_inr == 300000
    assert by["short_debt"].amount_inr == 100000
    assert all(b.monthly_amount_inr is None for b in out.buys)


def test_sip_sets_monthly_amount():
    out = run_additional_investment(_input(Cadence.SIP_MONTHLY, 40000))
    assert out.cadence is Cadence.SIP_MONTHLY
    for b in out.buys:
        assert b.monthly_amount_inr == b.amount_inr
    assert sum(b.amount_inr for b in out.buys) == 40000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_pipeline.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_additional_investment'`

- [ ] **Step 3: Write the pipeline**

`AI_Agents/src/additional_investment/pipeline.py`:
```python
from __future__ import annotations

from additional_investment.models import (
    AdditionalInvestmentInput,
    AdditionalInvestmentOutput,
    Cadence,
)
from additional_investment.ratio import compute_targets
from additional_investment.selection import select_funds


def run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput:
    branch, targets = compute_targets(
        inp.subgroups, inp.medium_term_fulfilled, inp.deploy_amount_inr
    )
    buys = select_funds(
        targets,
        inp.ranked_funds,
        inp.holdings,
        inp.resulting_corpus_inr,
        inp.cap_pct_by_subgroup,
        inp.default_cap_pct,
        inp.rounding_multiple_inr,
    )
    if inp.cadence is Cadence.SIP_MONTHLY:
        # deploy_amount_inr is the MONTHLY amount; per-fund amounts are monthly.
        buys = [b.model_copy(update={"monthly_amount_inr": b.amount_inr}) for b in buys]
    return AdditionalInvestmentOutput(
        branch_used=branch,
        cadence=inp.cadence,
        deploy_amount_inr=inp.deploy_amount_inr,
        per_subgroup_target=targets,
        buys=buys,
    )
```

Add to `AI_Agents/src/additional_investment/__init__.py`:
```python
from additional_investment.pipeline import run_additional_investment  # noqa: F401
```

- [ ] **Step 4: Run the full module test suite**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing -v`
Expected: PASS (all tasks' tests green)

- [ ] **Step 5: Commit**

```bash
# Testing/ is gitignored (see Global Constraints) — commit source files only.
git add AI_Agents/src/additional_investment/pipeline.py AI_Agents/src/additional_investment/__init__.py
git commit -m "feat(additional_investment): pipeline entry + SIP cadence"
```

---

## Self-Review

**Spec coverage (engine portions of `2026-06-25-additional-investment-intent-design.md`):**
- §Design decision #3 (two-branch rule, emergency excluded, renormalised) → Task 2. ✓
- decision #4 (`medium_term_fulfilled` as input bool; waterfall/no-goals handled by the caller in Plan 3) → consumed in Task 2; the no-medium-goals→fulfilled mapping is the app adapter's job (Plan 3), documented in the Plan 3 stub below. ✓
- decision #5 (caps reused, on resulting corpus, overflow rank-1→2→3) → Task 3. ✓
- decision #6 (contain-now fund selection, no rebalancing refactor) → Task 3 is self-contained. ✓
- decision #7 (round to ₹100) → Task 3 `_round_down`. ✓
- decision #8 + §3 (cadence = same ratio, SIP framing) → Task 4. ✓
- decision #10 (current allocation shape, normalised to ratio) → Task 2 normalises weights; the "current corpus, not re-run" choice is enforced by the Plan 3 adapter feeding the current allocation. ✓
- §2 engine I/O shapes → Task 1. ✓

**Not in this plan (correctly deferred):**
- Classifier intent + prompt boundaries (Plan 2).
- App domain, handler, flow, formatter, persistence, alembic migration, and the adapter that computes `medium_term_fulfilled`, sources caps from `Rebalancing/tables.py`, and maps real allocation/goal/holdings data into `AdditionalInvestmentInput` (Plan 3).

**Placeholder scan:** none — every step has runnable code and an exact command.

**Type consistency:** `AdditionalInvestmentInput` fields (Task 1) are read unchanged in Task 4; `select_funds`/`compute_targets` signatures match their call sites in `pipeline.py`. `FundBuy.monthly_amount_inr` (Task 1) is the field set in Task 4. ✓

## Downstream plans (to be written next)

- **Plan 2 — classifier:** add `ADDITIONAL_INVESTMENT` to `Intent` + `_IntentLiteral`; new intent definition + boundary re-adjudication in `prompts.py`; eval cases. Independently testable via the drift test + eval harness.
- **Plan 3 — app integration:** `app/domains/additional_investment/` (domain, `ainv_engine`, `@register` handler, `_AINV_FORMATTER_BODY`), `flow_additional_investment` + `FLOWS` row, the input adapter (computes `medium_term_fulfilled`, sources caps, builds `AdditionalInvestmentInput`), `AdditionalInvestmentRun` ORM + alembic migration + persist service + `AdditionalInvestmentRunDetailResponse`. Requires reading exact app types (`TurnContext`, `ModuleOutput`, `ChatHandlerResult`, `format_with_telemetry`) before authoring.
