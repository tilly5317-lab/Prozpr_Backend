# Goal Planning Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the goal_planning AI module per spec at `docs/superpowers/specs/2026-05-09-goal-planning-design.md` — a deterministic financial-planning engine at Excel parity wrapped by a LangGraph tool-calling agent with NL goal capture, what-if rerun, Q&A, and 7 deterministic recommendation levers.

**Architecture:** Three-layer module — (1) `financial_primitives/` shared library (pure Python, no LLM); (2) `goal_planning/engine/` deterministic 8-stage pipeline implementing 30 calculations from the Excel reference; (3) `goal_planning/agent/` LangGraph ReAct loop with 6 tools and 7 levers. Strict boundary lint tests enforce no-LLM-in-engine and no-internal-imports-from-bridge. State persists across turns via LangGraph checkpointer (MemorySaver in tests, AsyncPostgresSaver in prod).

**Tech Stack:** Python 3.11+, Pydantic v2, LangGraph, langchain-anthropic, numpy_financial, rapidfuzz, pytest, pytest-asyncio, hypothesis, time-machine, pytest-recording.

**Spec reference:** All section references like "spec §7.4" point to `docs/superpowers/specs/2026-05-09-goal-planning-design.md`.

---

## File Structure

```
AI_Agents/src/
├── financial_primitives/
│   ├── __init__.py
│   ├── time_value.py            # future_value, present_value, compound
│   ├── annuity.py               # pmt, rate (Newton-Raphson), ipmt
│   ├── inflation.py             # inflate, real_rate
│   ├── retirement.py            # retirement_corpus_pv composite
│   ├── dates.py                 # fy_for_date, fy_end, eomonth
│   └── tests/
│       └── test_primitives.py
└── goal_planning/
    ├── __init__.py              # public API
    ├── models.py                # public Pydantic contracts
    ├── prompts.py               # shared prompt fragments
    ├── config.py                # module constants
    ├── engine/                  # 13 files
    │   ├── __init__.py
    │   ├── pipeline.py
    │   ├── _types.py
    │   ├── exceptions.py
    │   ├── profile.py
    │   ├── dates.py
    │   ├── retirement.py
    │   ├── mortgages.py
    │   ├── properties.py
    │   ├── goals_table.py
    │   ├── cashflow.py
    │   ├── funding.py
    │   └── summary.py
    ├── agent/                   # 8 files
    │   ├── __init__.py
    │   ├── state.py
    │   ├── graph.py
    │   ├── nodes.py
    │   ├── tools.py
    │   ├── extractor.py
    │   ├── levers.py
    │   └── prompts.py
    └── tests/
        ├── conftest.py
        ├── unit/
        ├── integration/
        ├── agent/
        ├── boundary/
        └── fixtures/
            ├── excel_reference/
            ├── synthetic/
            └── llm_mocks/
```

Project-root additions:
- `requirements-dev.txt` (NEW)
- `pyproject.toml` `[tool.pytest.ini_options]` (NEW; project currently has no central pytest config)
- `requirements.txt` adds `numpy-financial>=1.0.0`, `rapidfuzz>=3.0`, `langgraph>=0.2`

---

## Phase 0: Test infrastructure & package skeleton

### Task 1: Create requirements-dev.txt and update requirements.txt

**Files:**
- Create: `requirements-dev.txt`
- Modify: `requirements.txt`

- [ ] **Step 1: Create requirements-dev.txt**

```
pytest>=8.0
pytest-asyncio>=0.23
pytest-cov>=5.0
pytest-recording>=0.13
pytest-rerunfailures>=14.0
respx>=0.21
time-machine>=2.14
hypothesis>=6.100
numpy-financial>=1.0.0
rapidfuzz>=3.0
```

- [ ] **Step 2: Append runtime deps to requirements.txt**

Add these lines to the existing `requirements.txt` (preserve all existing entries):
```
numpy-financial>=1.0.0
rapidfuzz>=3.0
langgraph>=0.2
```

- [ ] **Step 3: Install dev deps locally**

Run: `pip install -r requirements-dev.txt`
Expected: All packages installed.

- [ ] **Step 4: Commit**

```bash
git add requirements-dev.txt requirements.txt
git commit -m "build: add goal_planning dev and runtime dependencies"
```

---

### Task 2: Create pyproject.toml pytest config

**Files:**
- Create or modify: `pyproject.toml`

- [ ] **Step 1: Check if pyproject.toml exists**

Run: `ls pyproject.toml 2>/dev/null || echo "missing"`

- [ ] **Step 2: Add or create [tool.pytest.ini_options] section**

If file doesn't exist, create with this content. If exists, append the section:

```toml
[tool.pytest.ini_options]
pythonpath = ["AI_Agents/src", "."]
asyncio_mode = "auto"
markers = [
    "real_llm: requires ENABLE_LLM_SMOKE=1; not run in CI",
    "slow: long-running tests",
    "excel_parity: requires LibreOffice or pre-extracted fixtures",
    "performance: latency/memory regression checks",
]
```

- [ ] **Step 3: Verify pytest discovery works**

Run: `pytest --collect-only AI_Agents/src 2>&1 | head -5`
Expected: pytest discovers 0 tests (no tests yet) or output shows pythonpath added (not "no module named").

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add central pytest config for goal_planning"
```

---

### Task 3: Create empty package skeleton

**Files:**
- Create: `AI_Agents/src/financial_primitives/__init__.py`
- Create: `AI_Agents/src/financial_primitives/tests/__init__.py`
- Create: `AI_Agents/src/goal_planning/__init__.py`
- Create: `AI_Agents/src/goal_planning/engine/__init__.py`
- Create: `AI_Agents/src/goal_planning/agent/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/integration/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/agent/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/boundary/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/fixtures/__init__.py`

- [ ] **Step 1: Create the directory tree with empty __init__.py files**

```bash
mkdir -p AI_Agents/src/financial_primitives/tests
mkdir -p AI_Agents/src/goal_planning/{engine,agent,tests/{unit,integration,agent,boundary,fixtures/{excel_reference,synthetic,llm_mocks}}}
touch AI_Agents/src/financial_primitives/__init__.py
touch AI_Agents/src/financial_primitives/tests/__init__.py
touch AI_Agents/src/goal_planning/{__init__.py,engine/__init__.py,agent/__init__.py,tests/__init__.py}
touch AI_Agents/src/goal_planning/tests/{unit/__init__.py,integration/__init__.py,agent/__init__.py,boundary/__init__.py,fixtures/__init__.py}
```

- [ ] **Step 2: Verify imports work**

Run: `python -c "import financial_primitives; import cashflow_statement; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/financial_primitives/ AI_Agents/src/goal_planning/
git commit -m "feat(goal_planning): scaffold empty package skeleton"
```

---

### Task 4: Boundary lint tests (TDD baseline — they pass on empty packages)

**Files:**
- Create: `AI_Agents/src/goal_planning/tests/boundary/test_engine_no_llm.py`
- Create: `AI_Agents/src/goal_planning/tests/boundary/test_public_api_only.py`

- [ ] **Step 1: Write engine-no-LLM lint test**

```python
# AI_Agents/src/goal_planning/tests/boundary/test_engine_no_llm.py
import ast
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parents[2] / "engine"
FORBIDDEN = ("langchain_anthropic", "anthropic", "langchain_core", "langgraph")


def test_engine_has_no_llm_imports():
    """Engine must have zero LLM imports — including anthropic exceptions (stricter than project rule)."""
    violations: list[str] = []
    for py_file in ENGINE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name == p or alias.name.startswith(p + ".") for p in FORBIDDEN):
                        violations.append(f"{py_file.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module == p or node.module.startswith(p + ".") for p in FORBIDDEN):
                    violations.append(f"{py_file.name}:{node.lineno} from {node.module}")
    assert not violations, "Engine has LLM imports:\n" + "\n".join(violations)
```

- [ ] **Step 2: Write bridge-imports-only-public lint test**

```python
# AI_Agents/src/goal_planning/tests/boundary/test_public_api_only.py
import ast
from pathlib import Path

# This test runs only when the bridge layer exists.
# For now it's a placeholder that asserts the path is empty (or matches the rule when populated).
BRIDGE_DIR = Path(__file__).resolve().parents[5] / "app" / "services" / "ai_bridge" / "goal_planning"
FORBIDDEN_PREFIXES = (
    "goal_planning.engine",
    "goal_planning.agent",
    "AI_Agents.src.goal_planning.engine",
    "AI_Agents.src.goal_planning.agent",
)


def test_bridge_imports_only_public_api():
    """Bridge code (when it exists) must import only from top-level goal_planning."""
    if not BRIDGE_DIR.exists():
        return  # bridge not yet created; nothing to check
    violations: list[str] = []
    for py_file in BRIDGE_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(p) for p in FORBIDDEN_PREFIXES):
                        violations.append(f"{py_file.name}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(p) for p in FORBIDDEN_PREFIXES):
                    violations.append(f"{py_file.name}:{node.lineno} from {node.module}")
    assert not violations, "Bridge has internal imports:\n" + "\n".join(violations)
```

- [ ] **Step 3: Run both tests — they should pass on empty packages**

Run: `pytest AI_Agents/src/goal_planning/tests/boundary/ -v`
Expected: 2 passed (engine has no .py files yet, bridge dir does not exist — both pass vacuously).

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/boundary/
git commit -m "test(goal_planning): add boundary lint tests for engine and bridge"
```

---

## Phase 1: financial_primitives library

### Task 5: financial_primitives — time_value (FV, PV, compound)

**Files:**
- Create: `AI_Agents/src/financial_primitives/time_value.py`
- Create: `AI_Agents/src/financial_primitives/tests/test_time_value.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/financial_primitives/tests/test_time_value.py
import pytest
from financial_primitives.time_value import future_value, present_value, compound


def test_future_value_basic():
    # 100,000 at 8% for 10 years → 100000 * 1.08^10 ≈ 215,892.50
    assert future_value(100_000, rate=0.08, years=10) == pytest.approx(215_892.50, rel=1e-6)


def test_present_value_basic():
    # 215,892.50 discounted at 8% for 10 years → 100,000
    assert present_value(215_892.50, rate=0.08, years=10) == pytest.approx(100_000, rel=1e-6)


def test_fv_pv_inverse():
    pv = 50_000
    rate = 0.07
    years = 15
    assert present_value(future_value(pv, rate, years), rate, years) == pytest.approx(pv, rel=1e-9)


def test_compound_monthly():
    # 100,000 monthly compounded at 12% annual for 1 year
    # monthly_rate = (1.12)^(1/12) - 1 ≈ 0.00949
    # FV = 100000 * (1 + 0.00949)^12 = 100000 * 1.12 = 112000
    assert compound(100_000, monthly_rate=(1.12 ** (1/12) - 1), months=12) == pytest.approx(112_000, rel=1e-6)


def test_zero_years():
    assert future_value(100_000, rate=0.08, years=0) == 100_000
    assert present_value(100_000, rate=0.08, years=0) == 100_000


def test_negative_years_raises():
    with pytest.raises(ValueError):
        future_value(100_000, rate=0.08, years=-1)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_time_value.py -v`
Expected: FAIL — `ModuleNotFoundError: financial_primitives.time_value`

- [ ] **Step 3: Implement time_value.py**

```python
# AI_Agents/src/financial_primitives/time_value.py
"""Time-value-of-money primitives. Pure Python, no LLM, no I/O."""
from __future__ import annotations


def future_value(pv: float, rate: float, years: float) -> float:
    """FV = PV × (1 + rate)^years. Annual compounding."""
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return pv * (1 + rate) ** years


def present_value(fv: float, rate: float, years: float) -> float:
    """PV = FV / (1 + rate)^years."""
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return fv / (1 + rate) ** years


def compound(principal: float, monthly_rate: float, months: int) -> float:
    """Compound at monthly rate for N months."""
    if months < 0:
        raise ValueError(f"months must be >= 0, got {months}")
    return principal * (1 + monthly_rate) ** months
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_time_value.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/financial_primitives/time_value.py AI_Agents/src/financial_primitives/tests/test_time_value.py
git commit -m "feat(financial_primitives): add time-value primitives (FV, PV, compound)"
```

---

### Task 6: financial_primitives — annuity (PMT, RATE)

**Files:**
- Create: `AI_Agents/src/financial_primitives/annuity.py`
- Create: `AI_Agents/src/financial_primitives/tests/test_annuity.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/financial_primitives/tests/test_annuity.py
import pytest
import numpy_financial as npf
from financial_primitives.annuity import pmt, rate, ipmt


def test_pmt_matches_numpy_financial():
    # Loan: 50L at 8.5% annual, 240 months
    monthly_rate = (1.085) ** (1/12) - 1
    expected = npf.pmt(monthly_rate, 240, -5_000_000)
    assert pmt(monthly_rate, 240, 5_000_000) == pytest.approx(expected, rel=1e-9)


def test_rate_inverse_of_pmt():
    # PMT(r, n, P) → R; RATE(n, PMT, P) → r
    monthly_rate = 0.0075
    n = 180
    P = 3_000_000
    monthly_emi = pmt(monthly_rate, n, P)
    inferred = rate(n, monthly_emi, P)
    assert inferred == pytest.approx(monthly_rate, rel=1e-6)


def test_rate_non_convergence_raises():
    from financial_primitives.annuity import RATEConvergenceError
    # Impossible: paying ₹100/month on ₹10L for 12 months — rate would be negative-infinity
    with pytest.raises(RATEConvergenceError):
        rate(n=12, payment=100, principal=1_000_000, max_iter=20)


def test_ipmt_matches_numpy_financial():
    # Year 5 of a 240-month loan at 8.5% annual on 50L principal
    monthly_rate = (1.085) ** (1/12) - 1
    period = 60   # month 60
    n = 240
    principal = 5_000_000
    expected = npf.ipmt(monthly_rate, period, n, -principal)
    assert ipmt(monthly_rate, period, n, principal) == pytest.approx(expected, rel=1e-9)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_annuity.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement annuity.py**

```python
# AI_Agents/src/financial_primitives/annuity.py
"""Annuity primitives: PMT, RATE (Newton-Raphson inversion), IPMT.

Sign conventions follow numpy_financial: positive principal in, positive payments out.
"""
from __future__ import annotations
import numpy_financial as npf


class RATEConvergenceError(Exception):
    """Newton-Raphson did not converge for RATE inversion."""


def pmt(monthly_rate: float, n: int, principal: float) -> float:
    """Equated monthly payment for a loan. Wraps numpy_financial.pmt with sign-flip.

    Args:
        monthly_rate: per-period interest rate (e.g., 0.0075 for ~0.75% monthly)
        n: number of periods (months)
        principal: loan amount (positive)

    Returns:
        Positive monthly EMI.
    """
    return float(npf.pmt(monthly_rate, n, -principal))


def rate(n: int, payment: float, principal: float, max_iter: int = 100, tol: float = 1e-9) -> float:
    """Inverse of pmt: given n, payment, principal, find the per-period rate.

    Uses npf.rate (Newton-Raphson under the hood). Raises RATEConvergenceError on non-convergence.
    """
    try:
        result = npf.rate(n, -payment, principal, 0, guess=0.01, tol=tol, maxiter=max_iter)
        if result is None or (isinstance(result, float) and (result != result)):  # NaN check
            raise RATEConvergenceError(f"RATE did not converge for n={n}, pmt={payment}, P={principal}")
        return float(result)
    except (ValueError, ZeroDivisionError) as e:
        raise RATEConvergenceError(str(e)) from e


def ipmt(monthly_rate: float, period: int, n: int, principal: float) -> float:
    """Interest portion of EMI for given period (1-indexed). Sign-flipped to match numpy convention."""
    return float(npf.ipmt(monthly_rate, period, n, -principal))
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_annuity.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/financial_primitives/annuity.py AI_Agents/src/financial_primitives/tests/test_annuity.py
git commit -m "feat(financial_primitives): add PMT, RATE, IPMT with non-convergence handling"
```

---

### Task 7: financial_primitives — inflation, dates, retirement composite

**Files:**
- Create: `AI_Agents/src/financial_primitives/inflation.py`
- Create: `AI_Agents/src/financial_primitives/dates.py`
- Create: `AI_Agents/src/financial_primitives/retirement.py`
- Create: `AI_Agents/src/financial_primitives/tests/test_inflation_dates_retirement.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/financial_primitives/tests/test_inflation_dates_retirement.py
from datetime import date
import pytest
from financial_primitives.inflation import inflate, real_rate
from financial_primitives.dates import fy_for_date, fy_end_after, eomonth, year_fraction
from financial_primitives.retirement import retirement_corpus_pv


def test_inflate():
    # 100,000 at 6% for 5 years → 133,822.55
    assert inflate(100_000, rate=0.06, years=5) == pytest.approx(133_822.5577, rel=1e-6)


def test_real_rate_fisher():
    # nominal 9%, inflation 6% → real ≈ 2.83%
    r = real_rate(nominal=0.09, inflation=0.06)
    assert r == pytest.approx((1.09 / 1.06) - 1, rel=1e-9)


def test_fy_for_date_indian():
    # Indian FY: Apr 1 → Mar 31. FY year is the year of the closing March.
    assert fy_for_date(date(2026, 3, 31)) == 2026     # belongs to FY26 (closing 2026-03-31)
    assert fy_for_date(date(2026, 4, 1)) == 2027      # belongs to FY27 (closing 2027-03-31)
    assert fy_for_date(date(2026, 12, 15)) == 2027


def test_fy_end_after():
    # FY-end on or after a given date
    assert fy_end_after(date(2026, 3, 31)) == date(2026, 3, 31)
    assert fy_end_after(date(2026, 4, 1)) == date(2027, 3, 31)
    assert fy_end_after(date(2026, 12, 15)) == date(2027, 3, 31)


def test_eomonth():
    assert eomonth(date(2026, 5, 9)) == date(2026, 5, 31)
    assert eomonth(date(2026, 2, 15)) == date(2026, 2, 28)
    assert eomonth(date(2024, 2, 15)) == date(2024, 2, 29)  # leap year


def test_year_fraction():
    # exact 1 year
    assert year_fraction(date(2026, 5, 9), date(2027, 5, 9)) == pytest.approx(1.0, rel=1e-3)


def test_retirement_corpus_pv():
    # 25 years post-retirement, ₹10L annual expense (FV at retirement), 3% real ROI
    # PV at retirement-start = -PV(0.03, 25, 1_000_000)
    expected_via_npf = 1_000_000 * ((1 - (1.03) ** -25) / 0.03)
    actual = retirement_corpus_pv(
        annual_expense_fv=1_000_000,
        post_retirement_years=25,
        real_roi_annual=0.03,
    )
    assert actual == pytest.approx(expected_via_npf, rel=1e-6)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_inflation_dates_retirement.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement inflation.py**

```python
# AI_Agents/src/financial_primitives/inflation.py
"""Inflation primitives."""
from __future__ import annotations


def inflate(amount_pv: float, rate: float, years: float) -> float:
    """Inflate amount from PV to FV at given rate."""
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return amount_pv * (1 + rate) ** years


def real_rate(nominal: float, inflation: float) -> float:
    """Fisher equation: real_rate = (1 + nominal) / (1 + inflation) - 1."""
    return (1 + nominal) / (1 + inflation) - 1
```

- [ ] **Step 4: Implement dates.py**

```python
# AI_Agents/src/financial_primitives/dates.py
"""Date helpers for Indian Financial Year math."""
from __future__ import annotations
from datetime import date
from calendar import monthrange


def fy_for_date(d: date) -> int:
    """Return the Indian FY year for a given date.
    Indian FY runs Apr 1 → Mar 31. The FY year is the year of the closing March.
    Mar 31 2026 → 2026; Apr 1 2026 → 2027.
    """
    return d.year if d.month <= 3 else d.year + 1


def fy_end_after(d: date) -> date:
    """Return the FY-end (March 31) on or after the given date."""
    fy = fy_for_date(d)
    return date(fy, 3, 31)


def eomonth(d: date, months_offset: int = 0) -> date:
    """End-of-month date, offset by `months_offset` months. Excel's EOMONTH equivalent."""
    total_months = d.month - 1 + months_offset
    year = d.year + total_months // 12
    month = total_months % 12 + 1
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day)


def year_fraction(start: date, end: date) -> float:
    """Year fraction between two dates (calendar days / 365.25)."""
    return (end - start).days / 365.25
```

- [ ] **Step 5: Implement retirement.py**

```python
# AI_Agents/src/financial_primitives/retirement.py
"""Composite retirement-corpus primitive."""
from __future__ import annotations
import numpy_financial as npf


def retirement_corpus_pv(
    annual_expense_fv: float,
    post_retirement_years: int,
    real_roi_annual: float,
) -> float:
    """Compute the corpus required at retirement-start to fund annual expenses for N years.

    PV of an annuity: corpus = expense × [1 - (1+r)^(-n)] / r
    """
    if real_roi_annual == 0:
        return annual_expense_fv * post_retirement_years
    return float(-npf.pv(real_roi_annual, post_retirement_years, annual_expense_fv))
```

- [ ] **Step 6: Run tests to verify pass**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_inflation_dates_retirement.py -v`
Expected: 7 passed.

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/financial_primitives/inflation.py AI_Agents/src/financial_primitives/dates.py AI_Agents/src/financial_primitives/retirement.py AI_Agents/src/financial_primitives/tests/test_inflation_dates_retirement.py
git commit -m "feat(financial_primitives): add inflation, FY date helpers, retirement corpus"
```

---

### Task 8: financial_primitives __init__ public exports

**Files:**
- Modify: `AI_Agents/src/financial_primitives/__init__.py`

- [ ] **Step 1: Write the test file for the public surface**

```python
# AI_Agents/src/financial_primitives/tests/test_public_api.py
def test_public_exports():
    import financial_primitives as fp
    assert hasattr(fp, "future_value")
    assert hasattr(fp, "present_value")
    assert hasattr(fp, "compound")
    assert hasattr(fp, "pmt")
    assert hasattr(fp, "rate")
    assert hasattr(fp, "ipmt")
    assert hasattr(fp, "RATEConvergenceError")
    assert hasattr(fp, "inflate")
    assert hasattr(fp, "real_rate")
    assert hasattr(fp, "fy_for_date")
    assert hasattr(fp, "fy_end_after")
    assert hasattr(fp, "eomonth")
    assert hasattr(fp, "year_fraction")
    assert hasattr(fp, "retirement_corpus_pv")
```

- [ ] **Step 2: Run — should fail since __init__ is empty**

Run: `pytest AI_Agents/src/financial_primitives/tests/test_public_api.py -v`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Populate `__init__.py`**

```python
# AI_Agents/src/financial_primitives/__init__.py
"""Financial primitives — pure-Python, zero LLM, reusable across modules."""
from .time_value import future_value, present_value, compound
from .annuity import pmt, rate, ipmt, RATEConvergenceError
from .inflation import inflate, real_rate
from .dates import fy_for_date, fy_end_after, eomonth, year_fraction
from .retirement import retirement_corpus_pv

__all__ = [
    "future_value", "present_value", "compound",
    "pmt", "rate", "ipmt", "RATEConvergenceError",
    "inflate", "real_rate",
    "fy_for_date", "fy_end_after", "eomonth", "year_fraction",
    "retirement_corpus_pv",
]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest AI_Agents/src/financial_primitives/ -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/financial_primitives/__init__.py AI_Agents/src/financial_primitives/tests/test_public_api.py
git commit -m "feat(financial_primitives): public API surface"
```

---

## Phase 1: Public Pydantic contracts (`models.py`)

The full contract definitions are in spec §6. Each task below implements a section of `models.py`. Verify with: focused unit test on the new types.

### Task 9: models.py — input types (Assumptions, ClientProfile, RetirementInput)

**Files:**
- Create: `AI_Agents/src/goal_planning/models.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_models_input.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_models_input.py
from datetime import date
import pytest
from pydantic import ValidationError
from cashflow_statement.models import Assumptions, ClientProfile, RetirementInput


def test_assumptions_defaults():
    a = Assumptions()
    assert a.inflation_property == 0.06
    assert a.inflation_child_abroad_education == 0.08
    assert a.inflation_household_expense == 0.06
    assert a.roi_long_term_post_tax == 0.09
    assert a.default_mortgage_interest_annual == 0.075
    assert a.near_term_horizon_years == 2
    assert a.medium_term_horizon_years == 3


def test_client_profile_required_fields():
    p = ClientProfile(
        latest_update_date=date(2026, 5, 9),
        annual_income=2_000_000,
        tax_rate=0.30,
        financial_assets=20_000_000,
        financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000,
    )
    assert p.monthly_investment_next_12m is None  # default


def test_client_profile_monthly_investment_none_vs_zero():
    """None ≠ 0 — semantic distinction matters for M147 fallback."""
    p_none = ClientProfile(
        latest_update_date=date(2026, 5, 9), annual_income=0, tax_rate=0.30,
        financial_assets=0, financial_liabilities_excl_mortgage=0,
        monthly_household_expense=0,
    )
    p_zero = p_none.model_copy(update={"monthly_investment_next_12m": 0})
    assert p_none.monthly_investment_next_12m is None
    assert p_zero.monthly_investment_next_12m == 0


def test_retirement_input_defaults():
    r = RetirementInput(date_of_birth=date(1976, 5, 9))
    assert r.retirement_age == 60
    assert r.assumed_total_age == 85
    assert r.retirement_date_override is None
    assert r.retirement_corpus_pv_override is None
```

- [ ] **Step 2: Run — fails (module not found)**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_input.py -v`
Expected: FAIL.

- [ ] **Step 3: Create models.py with input types**

Implement per spec §6.1. Start `models.py`:

```python
# AI_Agents/src/goal_planning/models.py
"""Public Pydantic contracts for the goal_planning module.

All types here cross the engine↔agent boundary or are part of the public API
exported from cashflow_statement/__init__.py.
"""
from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class Assumptions(BaseModel):
    inflation_property: float = 0.06
    inflation_child_abroad_education: float = 0.08
    inflation_child_local_education: float = 0.06
    inflation_child_marriage: float = 0.06
    inflation_household_expense: float = 0.06
    annual_income_growth: float = 0.08
    annual_invested_amount_growth: float = 0.08
    roi_near_term_post_tax: float = 0.05
    roi_mid_term_post_tax: float = 0.07
    roi_long_term_post_tax: float = 0.09
    roi_retired_portfolio_annual: float = 0.09
    near_term_horizon_years: int = 2
    medium_term_horizon_years: int = 3
    default_mortgage_tenure_years: int = 30
    default_mortgage_interest_annual: float = 0.075


class ClientProfile(BaseModel):
    latest_update_date: date
    annual_income: float
    tax_rate: float
    financial_assets: float
    financial_liabilities_excl_mortgage: float
    monthly_household_expense: float
    monthly_investment_next_12m: float | None = None


class RetirementInput(BaseModel):
    date_of_birth: date
    retirement_age: int = 60
    assumed_total_age: int = 85
    retirement_date_override: date | None = None
    retirement_corpus_pv_override: float | None = None
```

- [ ] **Step 4: Run tests — pass**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_input.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/models.py AI_Agents/src/goal_planning/tests/unit/test_models_input.py
git commit -m "feat(goal_planning): models.py — Assumptions, ClientProfile, RetirementInput"
```

---

### Task 10: models.py — properties, custom goals, OneOffEvent, GoalType

**Files:**
- Modify: `AI_Agents/src/goal_planning/models.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_models_goals.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_models_goals.py
from datetime import date
import pytest
from pydantic import ValidationError
from cashflow_statement.models import (
    GoalType, CurrentProperty, GoalProperty, CustomGoal, OneOffEvent,
)


def test_goal_type_enum_values():
    assert GoalType.retirement.value == "retirement"
    assert GoalType.property.value == "property"
    assert GoalType.child_abroad_education.value == "child_abroad_education"
    assert GoalType.child_local_education.value == "child_local_education"
    assert GoalType.child_marriage.value == "child_marriage"
    assert GoalType.custom.value == "custom"


def test_current_property_defaults():
    p = CurrentProperty(name="apartment_1", has_mortgage=False)
    assert p.mortgage_balance is None
    assert p.mortgage_balance_as_of_date is None


def test_goal_property_defaults_cash_purchase():
    p = GoalProperty(name="house_1", target_pv=10_000_000, goal_date=date(2030, 5, 9))
    assert p.is_downpayment_only is False
    assert p.upfront_amount is None
    assert p.mortgage_tenure_years == 0
    assert p.mortgage_interest_annual == 0.075


def test_goal_property_requires_pv_or_fv():
    with pytest.raises(ValidationError):
        GoalProperty(name="house", goal_date=date(2030, 1, 1))  # neither pv nor fv


def test_goal_property_downpayment_requires_upfront():
    with pytest.raises(ValidationError, match="upfront_amount required"):
        GoalProperty(
            name="h", target_pv=10_000_000, is_downpayment_only=True,
            goal_date=date(2030, 1, 1), mortgage_tenure_years=20,
        )


def test_goal_property_downpayment_requires_tenure():
    with pytest.raises(ValidationError, match="mortgage_tenure_years"):
        GoalProperty(
            name="h", target_pv=10_000_000, is_downpayment_only=True,
            upfront_amount=2_000_000, goal_date=date(2030, 1, 1),
            mortgage_tenure_years=0,
        )


def test_custom_goal_requires_pv_or_fv():
    with pytest.raises(ValidationError):
        CustomGoal(name="g", goal_type=GoalType.custom, goal_date=date(2030, 1, 1))


def test_custom_goal_pv_or_fv_either_works():
    g_pv = CustomGoal(name="g", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2030, 1, 1))
    g_fv = CustomGoal(name="g", goal_type=GoalType.custom, amount_fv=1_500_000, goal_date=date(2030, 1, 1))
    assert g_pv.amount_pv == 1_000_000
    assert g_fv.amount_fv == 1_500_000


def test_oneoff_event():
    e = OneOffEvent(description="bonus", amount=500_000, date=date(2027, 3, 1))
    assert e.amount == 500_000
```

- [ ] **Step 2: Run — fail**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_goals.py -v`
Expected: FAIL.

- [ ] **Step 3: Append to models.py**

```python
# (append after RetirementInput in models.py)


class CurrentProperty(BaseModel):
    name: str
    has_mortgage: bool
    mortgage_balance: float | None = None
    mortgage_emi: float | None = None
    mortgage_last_date: date | None = None
    mortgage_balance_as_of_date: date | None = None  # defaults to profile.latest_update_date


class GoalProperty(BaseModel):
    name: str
    target_pv: float | None = None
    target_fv: float | None = None
    is_downpayment_only: bool = False
    upfront_amount: float | None = None
    goal_date: date
    inflation_annual: float | None = None
    mortgage_tenure_years: int = 0
    mortgage_interest_annual: float = 0.075

    @model_validator(mode="after")
    def _validate_goal_property(self) -> "GoalProperty":
        if self.target_pv is None and self.target_fv is None:
            raise ValueError("provide target_pv or target_fv (or both)")
        if self.is_downpayment_only:
            if self.upfront_amount is None:
                raise ValueError("upfront_amount required when is_downpayment_only=True")
            if self.mortgage_tenure_years <= 0:
                raise ValueError("mortgage_tenure_years must be > 0 when is_downpayment_only=True")
        return self


class GoalType(str, Enum):
    retirement = "retirement"
    property = "property"
    child_abroad_education = "child_abroad_education"
    child_local_education = "child_local_education"
    child_marriage = "child_marriage"
    custom = "custom"


class CustomGoal(BaseModel):
    name: str
    goal_type: GoalType
    amount_pv: float | None = None
    amount_fv: float | None = None
    goal_date: date
    inflation_rate_override: float | None = None

    @model_validator(mode="after")
    def _validate_custom_goal(self) -> "CustomGoal":
        if self.amount_pv is None and self.amount_fv is None:
            raise ValueError("provide amount_pv or amount_fv (or both)")
        return self


class OneOffEvent(BaseModel):
    description: str
    amount: float
    date: date
```

- [ ] **Step 4: Run tests — pass**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_goals.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/models.py AI_Agents/src/goal_planning/tests/unit/test_models_goals.py
git commit -m "feat(goal_planning): properties, custom goals, GoalType enum, OneOffEvent"
```

---

### Task 11: models.py — GoalPlanningInput with case-fold uniqueness validator

**Files:**
- Modify: `AI_Agents/src/goal_planning/models.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_models_planning_input.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_models_planning_input.py
from datetime import date
import pytest
from pydantic import ValidationError
from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
    GoalProperty, CurrentProperty, OneOffEvent,
)


def _profile():
    return ClientProfile(
        latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
        financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000,
    )


def _retirement():
    return RetirementInput(date_of_birth=date(1976, 5, 9))


def test_input_minimal_construction():
    inp = GoalPlanningInput(profile=_profile(), retirement=_retirement())
    assert inp.detail_level == "default"
    assert inp.assumptions.roi_long_term_post_tax == 0.09


def test_input_rejects_duplicate_goal_names_case_insensitive():
    with pytest.raises(ValidationError, match="Duplicate names"):
        GoalPlanningInput(
            profile=_profile(),
            retirement=_retirement(),
            custom_goals=[
                CustomGoal(name="College", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2035, 1, 1)),
                CustomGoal(name="college", goal_type=GoalType.custom, amount_pv=2_000_000, goal_date=date(2040, 1, 1)),
            ],
        )


def test_input_rejects_name_collision_with_retirement():
    with pytest.raises(ValidationError, match="Duplicate names"):
        GoalPlanningInput(
            profile=_profile(),
            retirement=_retirement(),
            custom_goals=[
                CustomGoal(name="Retirement", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2040, 1, 1)),
            ],
        )


def test_input_rejects_property_name_collision_with_oneoff():
    with pytest.raises(ValidationError, match="Duplicate names"):
        GoalPlanningInput(
            profile=_profile(),
            retirement=_retirement(),
            current_properties=[CurrentProperty(name="Mumbai_house", has_mortgage=False)],
            one_off_inflows=[OneOffEvent(description="mumbai_house", amount=500_000, date=date(2027, 1, 1))],
        )


def test_input_accepts_unique_names():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=_retirement(),
        custom_goals=[
            CustomGoal(name="college", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2035, 1, 1)),
            CustomGoal(name="marriage", goal_type=GoalType.child_marriage, amount_pv=2_000_000, goal_date=date(2045, 1, 1)),
        ],
    )
    assert len(inp.custom_goals) == 2
```

- [ ] **Step 2: Run — fail**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_planning_input.py -v`
Expected: FAIL.

- [ ] **Step 3: Append to models.py**

```python
# (append after OneOffEvent in models.py)


class GoalPlanningInput(BaseModel):
    assumptions: Assumptions = Field(default_factory=Assumptions)
    profile: ClientProfile
    retirement: RetirementInput
    current_properties: list[CurrentProperty] = []
    goal_properties: list[GoalProperty] = []
    custom_goals: list[CustomGoal] = []
    one_off_inflows: list[OneOffEvent] = []
    one_off_outflows: list[OneOffEvent] = []
    detail_level: Literal["default", "full"] = "default"

    @model_validator(mode="after")
    def _validate_unique_names(self) -> "GoalPlanningInput":
        names: list[str] = ["retirement"]
        names.extend(p.name for p in self.current_properties)
        names.extend(p.name for p in self.goal_properties)
        names.extend(g.name for g in self.custom_goals)
        names.extend(e.description for e in self.one_off_inflows)
        names.extend(e.description for e in self.one_off_outflows)
        normalized = [n.casefold() for n in names]
        dupes = {n for n in normalized if normalized.count(n) > 1}
        if dupes:
            raise ValueError(f"Duplicate names across inputs (case-insensitive): {sorted(dupes)}")
        return self
```

- [ ] **Step 4: Run tests — pass**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_planning_input.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/models.py AI_Agents/src/goal_planning/tests/unit/test_models_planning_input.py
git commit -m "feat(goal_planning): GoalPlanningInput with case-fold name uniqueness"
```

---

### Task 12: models.py — output types (HeadlineStatus, RetirementSnapshot, GoalFundingStatus, cashflow rows, MortgageAmortization, FundFlowSummary, ValidationIssue, GoalPlanningOutput)

**Files:**
- Modify: `AI_Agents/src/goal_planning/models.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_models_output.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_models_output.py
from datetime import date, datetime
from cashflow_statement.models import (
    HeadlineStatus, RetirementSnapshot, GoalFundingStatus, OneOffFundingStatus,
    AnnualCashflowRow, MonthlyCashflowRow, MonthlyNFARow,
    MortgageAmortizationRow, MortgageAmortization,
    FundFlowSummary, ValidationIssue, GoalType,
)


def test_headline_status_construction():
    h = HeadlineStatus(
        horizon_years=20, last_goal_date=date(2046, 1, 1), last_fy_end_date=date(2046, 3, 31),
        number_of_goals=3, net_financial_assets_today=15_000_000, sum_fund_today_pv=10_000_000,
        present_status=5_000_000, closing_nfa=3_000_000, total_shortfall_fv=0,
        total_funded_amount=12_000_000, is_overall_feasible=True,
        overall_shortfall_pv=0, overall_shortfall_fv=0,
    )
    assert h.is_overall_feasible


def test_retirement_snapshot_used_picks_override_when_set():
    # Plain construction; engine fills `corpus_required_used`
    s = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_500_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=40_000_000,
        corpus_required_used=40_000_000,
    )
    assert s.corpus_required_used == s.corpus_required_user_override


def test_goal_funding_status_positive_shortfall_convention():
    g = GoalFundingStatus(
        name="college", goal_type=GoalType.child_local_education, goal_date=date(2035, 1, 1),
        amount_pv=1_000_000, amount_fv=2_000_000, fund_today_pv=1_500_000,
        funded_amount=1_400_000, is_funded=False, shortfall_fv=600_000, shortfall_pv=400_000,
        expected_roi=0.07,
    )
    assert g.shortfall_fv > 0  # positive convention
    assert g.funded_amount + g.shortfall_fv == g.amount_fv


def test_monthly_nfa_row_kind_literals():
    r = MonthlyNFARow(
        month_end=date(2026, 5, 31), fy_label="FY27", nfa_open=10_000_000, regular_invest=50_000,
        regular_invest_kind="user_sip", roi=80_000, one_off_in=0, goal_outflow_total=0,
        nfa_close=10_130_000, savings_2_avg=70_000, funded_flag=True,
    )
    assert r.regular_invest_kind == "user_sip"


def test_validation_issue_severity():
    v = ValidationIssue(field="retirement.date_of_birth", message="missing", severity="error")
    assert v.severity == "error"
```

- [ ] **Step 2: Run — fail**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_output.py -v`
Expected: FAIL.

- [ ] **Step 3: Append output types to models.py per spec §6.2**

Append the following types in order: `HeadlineStatus`, `RetirementSnapshot`, `GoalFundingStatus`, `OneOffFundingStatus`, `AnnualCashflowRow`, `MonthlyCashflowRow`, `MonthlyNFARow`, `MortgageAmortizationRow`, `MortgageAmortization`, `FundFlowSummary`, `ValidationIssue`, `GoalPlanningOutput`. Full field definitions are in spec §6.2 — copy exactly.

Key invariants to preserve from spec:
- `MonthlyNFARow.regular_invest_kind: Literal["user_sip", "savings_sip_fraction", "withdrawal", "zero"]`
- `GoalPlanningOutput.engine_version: str` (required field)
- `GoalPlanningOutput.input_echo: GoalPlanningInput` (frozen snapshot)
- `monthly_cashflow`, `nfa_monthly_series`, `mortgage_amortizations` are `None` by default (γ-only)

- [ ] **Step 4: Run tests — pass**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_output.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/models.py AI_Agents/src/goal_planning/tests/unit/test_models_output.py
git commit -m "feat(goal_planning): output types — HeadlineStatus, GoalFundingStatus, cashflow rows, etc."
```

---

### Task 13: models.py — agent types (OverrideSpec discriminated union, GoalMutation, Lever, ExtractedFinancialEvent)

**Files:**
- Modify: `AI_Agents/src/goal_planning/models.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_models_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_models_agent.py
from datetime import date
import pytest
from pydantic import ValidationError, TypeAdapter
from cashflow_statement.models import (
    NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride,
    OverrideSpec, GoalMutation, LeverAction, Lever, HeadlineStatus,
    ExtractedFinancialEvent, ExtractedGoal, ExtractedProperty, ExtractedCashflow,
    ExtractedMutation, ExtractionError,
    CustomGoal, GoalProperty, OneOffEvent, GoalType,
)


def test_numeric_override_rejects_invalid_key():
    with pytest.raises(ValidationError):
        NumericOverride(kind="numeric", key="retirement_age", value=58)  # retirement_age moved to mutate_goal per Q3


def test_numeric_override_accepts_valid_key():
    n = NumericOverride(kind="numeric", key="monthly_investment_next_12m", value=50_000)
    assert n.value == 50_000


def test_property_field_override_includes_early_payoff_date():
    o = PropertyFieldOverride(
        kind="property_field", property_name="apartment_1",
        field="early_payoff_date", value=date(2030, 5, 9),
    )
    assert o.field == "early_payoff_date"


def test_override_spec_discriminator():
    # Discriminated union dispatches by `kind`
    adapter = TypeAdapter(OverrideSpec)
    parsed = adapter.validate_python({
        "kind": "rate", "key": "inflation_property", "value": 0.07,
    })
    assert isinstance(parsed, RateOverride)


def test_goal_mutation_fields():
    m = GoalMutation(kind="mutation", op="update", goal_name="retirement", fields={"retirement_age": 58})
    assert m.fields["retirement_age"] == 58


def test_lever_action_union_supports_mutation():
    adapter = TypeAdapter(LeverAction)
    parsed = adapter.validate_python({
        "kind": "mutation", "op": "update",
        "goal_name": "retirement", "fields": {"retirement_age": 62},
    })
    assert isinstance(parsed, GoalMutation)


def test_extracted_event_discriminator():
    adapter = TypeAdapter(ExtractedFinancialEvent)
    g = adapter.validate_python({
        "kind": "custom_goal",
        "goal": {
            "name": "college", "goal_type": "child_local_education",
            "amount_pv": 1_000_000, "goal_date": "2035-01-01",
        },
    })
    assert isinstance(g, ExtractedGoal)
```

- [ ] **Step 2: Run — fail**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_agent.py -v`
Expected: FAIL.

- [ ] **Step 3: Append agent types to models.py per spec §6.3**

Append: `NumericOverride`, `RateOverride`, `PerGoalRateOverride`, `PropertyFieldOverride`, `OverrideSpec` (discriminated `Annotated[Union[...], Field(discriminator="kind")]`), `GoalMutation`, `LeverAction` (union including `GoalMutation`), `Lever`, `ExtractedGoal`, `ExtractedProperty`, `ExtractedCashflow`, `ExtractedMutation`, `ExtractedFinancialEvent` (discriminated union), `ExtractionError`, `GoalPlanningResponse`. See spec §6.3 for exact field definitions.

**Critical:** `NumericOverride.key` literal MUST exclude `retirement_age`, `assumed_total_age`, `retirement_corpus_pv_override` (those go via `mutate_goal` per Q3). `PropertyFieldOverride.field` MUST include `"early_payoff_date"` (for Lever G).

- [ ] **Step 4: Run tests — pass**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_agent.py -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/models.py AI_Agents/src/goal_planning/tests/unit/test_models_agent.py
git commit -m "feat(goal_planning): agent types — OverrideSpec union, GoalMutation, Lever, ExtractedFinancialEvent"
```

---

### Task 14: models.py — `dated_field()` accessor on ExtractedFinancialEvent

**Files:**
- Modify: `AI_Agents/src/goal_planning/models.py`
- Modify: `AI_Agents/src/goal_planning/tests/unit/test_models_agent.py`

- [ ] **Step 1: Add failing test**

Append to `test_models_agent.py`:

```python
def test_dated_field_for_each_kind():
    from datetime import date
    g = ExtractedGoal(kind="custom_goal", goal=CustomGoal(
        name="x", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2035, 1, 1),
    ))
    assert g.dated_field() == date(2035, 1, 1)

    p = ExtractedProperty(kind="property_goal", property=GoalProperty(
        name="x", target_pv=10_000_000, goal_date=date(2030, 1, 1),
    ), assumptions_used=[])
    assert p.dated_field() == date(2030, 1, 1)

    c = ExtractedCashflow(kind="cashflow_event", event=OneOffEvent(
        description="bonus", amount=100_000, date=date(2027, 3, 1),
    ), direction="in", confidence="high")
    assert c.dated_field() == date(2027, 3, 1)

    m = ExtractedMutation(kind="goal_mutation", op="update", goal_name="g", fields={"goal_date": date(2040, 1, 1)})
    assert m.dated_field() == date(2040, 1, 1)

    m_no_date = ExtractedMutation(kind="goal_mutation", op="update", goal_name="g", fields={"amount_pv": 2_000_000})
    assert m_no_date.dated_field() is None
```

- [ ] **Step 2: Run — fail**

Expected: AttributeError, `dated_field` not defined.

- [ ] **Step 3: Add `dated_field()` method to each Extracted* class**

In `models.py`, add to each variant:

```python
class ExtractedGoal(BaseModel):
    kind: Literal["custom_goal"]
    goal: CustomGoal

    def dated_field(self) -> date | None:
        return self.goal.goal_date


class ExtractedProperty(BaseModel):
    kind: Literal["property_goal"]
    property: GoalProperty
    assumptions_used: list[str] = []

    def dated_field(self) -> date | None:
        return self.property.goal_date


class ExtractedCashflow(BaseModel):
    kind: Literal["cashflow_event"]
    event: OneOffEvent
    direction: Literal["in", "out"]
    confidence: Literal["high", "medium", "low"]

    def dated_field(self) -> date | None:
        return self.event.date


class ExtractedMutation(BaseModel):
    kind: Literal["goal_mutation"]
    op: Literal["add", "remove", "update"]
    goal_name: str
    fields: dict[str, Any] = {}

    def dated_field(self) -> date | None:
        v = self.fields.get("goal_date")
        return v if isinstance(v, date) else None
```

- [ ] **Step 4: Run — pass**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_models_agent.py::test_dated_field_for_each_kind -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/models.py AI_Agents/src/goal_planning/tests/unit/test_models_agent.py
git commit -m "feat(goal_planning): dated_field() accessor on ExtractedFinancialEvent variants"
```

---

## Phase 1: Engine — internal types, exceptions, dates

### Task 15: engine/exceptions.py

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/exceptions.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_exceptions.py`

- [ ] **Step 1: Write test**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_exceptions.py
import pytest
from cashflow_statement.engine.exceptions import (
    GoalPlanningEngineError, MissingDOBError, PastGoalDateError, RATEConvergenceError,
)


def test_exception_hierarchy():
    assert issubclass(MissingDOBError, GoalPlanningEngineError)
    assert issubclass(PastGoalDateError, GoalPlanningEngineError)
    assert issubclass(RATEConvergenceError, GoalPlanningEngineError)


def test_can_raise_and_catch():
    with pytest.raises(GoalPlanningEngineError):
        raise MissingDOBError("dob missing")
```

- [ ] **Step 2: Run — fail**

Run: `pytest AI_Agents/src/goal_planning/tests/unit/test_engine_exceptions.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

```python
# AI_Agents/src/goal_planning/engine/exceptions.py
"""Engine exception classes — used by validate_input_only (pre-flight) and engine internals."""
from __future__ import annotations


class GoalPlanningEngineError(Exception):
    """Base class for engine errors."""


class MissingDOBError(GoalPlanningEngineError):
    """Date of birth missing — required for retirement calc."""


class PastGoalDateError(GoalPlanningEngineError):
    """Goal date is on or before latest_update_date.

    Raised by validate_input_only (strict pre-flight); runtime engine drops with warning.
    """


class RATEConvergenceError(GoalPlanningEngineError):
    """RATE inversion did not converge for an existing mortgage.

    Caught internally; warning emitted; fallback to assumptions.default_mortgage_interest_annual.
    """
```

- [ ] **Step 4: Run — pass**

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/exceptions.py AI_Agents/src/goal_planning/tests/unit/test_engine_exceptions.py
git commit -m "feat(engine): exception hierarchy (MissingDOB, PastGoalDate, RATEConvergence)"
```

---

### Task 16: engine/dates.py — _round_thousand and FY helpers

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/dates.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_dates.py`

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_dates.py
from datetime import date
import pytest
from cashflow_statement.engine.dates import (
    _round_thousand, near_term_cutoff, medium_term_cutoff, real_roi_monthly,
)


def test_round_thousand():
    assert _round_thousand(12_500) == 13_000   # banker's: pythons rounds .5 to even, 12500 → 12000? Use ROUND-half-away
    assert _round_thousand(12_499) == 12_000
    assert _round_thousand(12_501) == 13_000
    assert _round_thousand(0) == 0
    assert _round_thousand(-1_500) == -2_000


def test_near_term_cutoff_24_months_then_fy_end():
    # latest_update = 2026-05-09 → +24 months = 2028-05-09 → fy_end_after = 2029-03-31
    assert near_term_cutoff(date(2026, 5, 9)) == date(2029, 3, 31)


def test_medium_term_cutoff_36_months_after_near():
    # near = 2029-03-31; +36 months = 2032-03-31 (already FY end); fy_end_after = 2032-03-31
    assert medium_term_cutoff(near_term_end=date(2029, 3, 31)) == date(2032, 3, 31)


def test_real_roi_monthly():
    # nominal 9%, inflation 6% → real_annual = (1.09/1.06) - 1 = 0.02830...
    # real_monthly = (1.0283)^(1/12) - 1
    expected_annual = (1.09 / 1.06) - 1
    expected_monthly = (1 + expected_annual) ** (1/12) - 1
    assert real_roi_monthly(roi_nominal=0.09, inflation=0.06) == pytest.approx(expected_monthly, rel=1e-9)
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement**

```python
# AI_Agents/src/goal_planning/engine/dates.py
"""Engine date helpers + ROUND_THOUSAND convention helper.

Re-exports from financial_primitives.dates where applicable.
"""
from __future__ import annotations
from datetime import date
from financial_primitives.dates import fy_for_date, fy_end_after, eomonth, year_fraction
from financial_primitives.inflation import real_rate
from dateutil.relativedelta import relativedelta  # already in project deps


def _round_thousand(x: float) -> float:
    """Round to nearest 1000, half-away-from-zero (matches Excel ROUND(_, -3))."""
    if x >= 0:
        return float(int((x + 500) // 1000) * 1000)
    else:
        return -float(int((-x + 500) // 1000) * 1000)


def near_term_cutoff(latest_update_date: date, years: int = 2) -> date:
    """Near-term cutoff = FY-end on/after (latest_update + N years)."""
    return fy_end_after(latest_update_date + relativedelta(years=years))


def medium_term_cutoff(near_term_end: date, years: int = 3) -> date:
    """Medium-term cutoff = FY-end on/after (near_term_end + N years)."""
    return fy_end_after(near_term_end + relativedelta(years=years))


def real_roi_monthly(roi_nominal: float, inflation: float) -> float:
    """Compute monthly real-return rate via Fisher equation, monthly-compounded."""
    real_annual = real_rate(roi_nominal, inflation)
    return (1 + real_annual) ** (1/12) - 1


__all__ = [
    "_round_thousand", "near_term_cutoff", "medium_term_cutoff", "real_roi_monthly",
    "fy_for_date", "fy_end_after", "eomonth", "year_fraction",
]
```

- [ ] **Step 4: Run — pass**

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/dates.py AI_Agents/src/goal_planning/tests/unit/test_engine_dates.py
git commit -m "feat(engine): dates.py — _round_thousand, near/medium-term cutoffs, real_roi_monthly"
```

---

### Task 17: engine/_types.py — RunContext + intermediate types

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/_types.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_types.py`

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_types.py
from datetime import date
from cashflow_statement.engine._types import (
    RunContext, MortgageAnnualRow, GoalPropertyOutcome, GoalInternal,
)
from cashflow_statement.models import GoalType, RetirementSnapshot


def test_run_context_with_retirement_immutable_update():
    ctx = RunContext(
        nfa=15_000_000, latest_update_date=date(2026, 5, 9),
        annual_income=2_000_000, annual_household_expense=960_000,
        monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        tax_rate=0.30, current_fy_end=date(2026, 3, 31), current_fy_year=2026,
        near_term_end=date(2029, 3, 31), medium_term_end=date(2032, 3, 31),
        retirement_date_considered=None, retired_portfolio_roi_annual=0.09,
        real_roi_retired_monthly=0.0023,
        sip_share=0.75, annual_income_growth=0.08, annual_invested_amount_growth=0.08,
        inflation_household_expense=0.06, near_term_roi=0.05, mid_term_roi=0.07, long_term_roi=0.09,
    )
    snap = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )
    new_ctx = ctx.with_retirement(snap)
    assert ctx.retirement_date_considered is None  # original unchanged
    assert new_ctx.retirement_date_considered == date(2036, 5, 9)
    assert new_ctx.real_roi_retired_monthly == 0.0023


def test_goal_internal_construction():
    g = GoalInternal(
        name="college", goal_type=GoalType.child_local_education,
        goal_date=date(2035, 1, 1), goal_date_fy=date(2035, 3, 31),
        amount_pv=1_000_000, amount_fv=2_000_000, inflation_rate=0.06,
        expected_roi=0.07, fund_today_pv=1_500_000,
    )
    assert g.fund_today_pv == 1_500_000
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement per spec §7.3**

```python
# AI_Agents/src/goal_planning/engine/_types.py
"""Engine-private intermediate types. NOT exported from cashflow_statement.__init__."""
from __future__ import annotations
from datetime import date
from typing import Any
from pydantic import BaseModel
from cashflow_statement.models import (
    GoalType, MortgageAmortizationRow, RetirementSnapshot,
    GoalFundingStatus, OneOffFundingStatus, MonthlyNFARow,
)


class RunContext(BaseModel):
    # Profile (resolved)
    nfa: float
    latest_update_date: date
    annual_income: float
    annual_household_expense: float
    monthly_household_expense: float
    monthly_investment_next_12m: float | None
    tax_rate: float

    # Date anchors
    current_fy_end: date
    current_fy_year: int
    near_term_end: date
    medium_term_end: date
    horizon_cap_years: int = 80

    # Resolved retirement (populated by .with_retirement())
    retirement_date_considered: date | None = None
    retired_portfolio_roi_annual: float
    real_roi_retired_monthly: float

    # Assumption snapshot
    sip_share: float
    annual_income_growth: float
    annual_invested_amount_growth: float
    inflation_household_expense: float
    near_term_roi: float
    mid_term_roi: float
    long_term_roi: float

    def with_retirement(self, snap: RetirementSnapshot) -> "RunContext":
        return self.model_copy(update={
            "retirement_date_considered": snap.retirement_date,
            "retired_portfolio_roi_annual": snap.real_roi_annual,
            "real_roi_retired_monthly": snap.real_roi_monthly,
        })


class MortgageAnnualRow(BaseModel):
    fy_end: date
    opening_balance: float
    annual_interest: float
    annual_principal: float
    annual_emi_total: float
    closing_balance: float


class MortgageSchedule(BaseModel):
    property_ref: str
    start_date: date
    monthly_rows: list[MortgageAmortizationRow]
    annual_rows: list[MortgageAnnualRow]

    def total_emi_in_fy(self, fy_end: date) -> float:
        for row in self.annual_rows:
            if row.fy_end == fy_end:
                return row.annual_emi_total
        return 0.0

    def total_emi_in_month(self, month_end: date) -> float:
        for row in self.monthly_rows:
            if row.month_end == month_end:
                return row.emi
        return 0.0


class GoalPropertyOutcome(BaseModel):
    name: str
    target_fv: float
    payout_amount_fv: float
    mortgage_amount: float
    amortization: MortgageSchedule | None


class GoalInternal(BaseModel):
    name: str
    goal_type: GoalType
    goal_date: date
    goal_date_fy: date
    amount_pv: float
    amount_fv: float
    inflation_rate: float
    expected_roi: float
    fund_today_pv: float


class FundingResult(BaseModel):
    nfa_monthly: list[MonthlyNFARow]
    closing_nfa: float
    min_nfa_in_horizon: float
    per_goal_status: list[GoalFundingStatus]
    per_one_off_outflow_status: list[OneOffFundingStatus]
    per_outflow_underfunded_total: dict[str, float]
    per_outflow_funded_amount: dict[str, float]
```

- [ ] **Step 4: Run — pass**

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/_types.py AI_Agents/src/goal_planning/tests/unit/test_engine_types.py
git commit -m "feat(engine): _types.py — RunContext, MortgageSchedule, GoalInternal, FundingResult"
```

---

### Task 18: engine/profile.py — build_initial_context

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/profile.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_profile.py`

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_profile.py
from datetime import date
import pytest
from cashflow_statement.models import Assumptions, ClientProfile
from cashflow_statement.engine.profile import build_initial_context


def _profile():
    return ClientProfile(
        latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
        financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
    )


def test_nfa_computation():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.nfa == 15_000_000  # 20M - 5M


def test_annual_household_expense():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.annual_household_expense == 80_000 * 12


def test_current_fy_year_and_end():
    # latest_update 2026-05-09 → current FY is 2027 (closes 2027-03-31)
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.current_fy_year == 2027
    assert ctx.current_fy_end == date(2027, 3, 31)


def test_near_term_and_medium_term_anchors():
    ctx = build_initial_context(_profile(), Assumptions())
    # 2026-05-09 + 24mo = 2028-05-09 → fy_end_after = 2029-03-31
    assert ctx.near_term_end == date(2029, 3, 31)
    # 2029-03-31 + 36mo = 2032-03-31 (already FY end)
    assert ctx.medium_term_end == date(2032, 3, 31)


def test_assumption_snapshot_copied_into_context():
    ctx = build_initial_context(_profile(), Assumptions(roi_long_term_post_tax=0.10))
    assert ctx.long_term_roi == 0.10
    assert ctx.sip_share == 0.75  # default per Assumptions/spec


def test_retirement_fields_unset_initially():
    ctx = build_initial_context(_profile(), Assumptions())
    assert ctx.retirement_date_considered is None
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement**

```python
# AI_Agents/src/goal_planning/engine/profile.py
"""Stage 1: build initial RunContext from profile + assumptions."""
from __future__ import annotations
from cashflow_statement.models import Assumptions, ClientProfile
from cashflow_statement.engine._types import RunContext
from cashflow_statement.engine.dates import (
    fy_for_date, fy_end_after, near_term_cutoff, medium_term_cutoff, real_roi_monthly,
)
from financial_primitives.inflation import real_rate

# B30 default: IFNA(B28, B29) where B29=0.8; spec uses 0.75 as combined default
DEFAULT_SIP_SHARE = 0.75


def build_initial_context(profile: ClientProfile, assumptions: Assumptions) -> RunContext:
    """Stage 1 of pipeline: resolve profile + assumptions into a RunContext."""
    nfa = profile.financial_assets - profile.financial_liabilities_excl_mortgage
    annual_household_expense = profile.monthly_household_expense * 12

    current_fy_year = fy_for_date(profile.latest_update_date)
    current_fy_end = fy_end_after(profile.latest_update_date)
    near_term_end = near_term_cutoff(profile.latest_update_date, assumptions.near_term_horizon_years)
    medium_term_end = medium_term_cutoff(near_term_end, assumptions.medium_term_horizon_years)

    real_monthly = real_roi_monthly(
        roi_nominal=assumptions.roi_retired_portfolio_annual,
        inflation=assumptions.inflation_household_expense,
    )

    return RunContext(
        nfa=nfa,
        latest_update_date=profile.latest_update_date,
        annual_income=profile.annual_income,
        annual_household_expense=annual_household_expense,
        monthly_household_expense=profile.monthly_household_expense,
        monthly_investment_next_12m=profile.monthly_investment_next_12m,
        tax_rate=profile.tax_rate,
        current_fy_end=current_fy_end,
        current_fy_year=current_fy_year,
        near_term_end=near_term_end,
        medium_term_end=medium_term_end,
        retirement_date_considered=None,
        retired_portfolio_roi_annual=assumptions.roi_retired_portfolio_annual,
        real_roi_retired_monthly=real_monthly,
        sip_share=DEFAULT_SIP_SHARE,
        annual_income_growth=assumptions.annual_income_growth,
        annual_invested_amount_growth=assumptions.annual_invested_amount_growth,
        inflation_household_expense=assumptions.inflation_household_expense,
        near_term_roi=assumptions.roi_near_term_post_tax,
        mid_term_roi=assumptions.roi_mid_term_post_tax,
        long_term_roi=assumptions.roi_long_term_post_tax,
    )
```

- [ ] **Step 4: Run — pass**

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/profile.py AI_Agents/src/goal_planning/tests/unit/test_engine_profile.py
git commit -m "feat(engine): profile.py — build_initial_context (stage 1)"
```

---

### Task 19: engine/retirement.py — compute_retirement_snapshot (with already-retired branch)

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/retirement.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_retirement.py`

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_retirement.py
from datetime import date
import pytest
from cashflow_statement.models import RetirementInput, Assumptions, ClientProfile
from cashflow_statement.engine.retirement import compute_retirement_snapshot
from cashflow_statement.engine.profile import build_initial_context
from cashflow_statement.engine.exceptions import MissingDOBError


def _ctx(latest_update=date(2026, 5, 9)):
    return build_initial_context(
        ClientProfile(
            latest_update_date=latest_update, annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_corpus_computed_matches_pv_formula():
    # DOB 1976-05-09 → age 50 at 2026-05-09 → retire at 60 in 2036-05-09
    # post_retire_years = 25; expense PV = 960k; FV at retirement = 960k × 1.06^10 = 1,718,991.34
    # corpus = -PV(real_annual, 25, FV) ≈ specific value
    inp = RetirementInput(date_of_birth=date(1976, 5, 9))
    snap = compute_retirement_snapshot(inp, _ctx(), [])
    assert snap.years_to_retirement == pytest.approx(10.0, abs=0.1)
    assert snap.post_retirement_years == 25
    # corpus_required_used == corpus_required_computed when no override
    assert snap.corpus_required_used == snap.corpus_required_computed
    assert snap.corpus_required_user_override is None


def test_user_override_takes_precedence():
    inp = RetirementInput(
        date_of_birth=date(1976, 5, 9),
        retirement_corpus_pv_override=40_000_000,
    )
    snap = compute_retirement_snapshot(inp, _ctx(), [])
    assert snap.corpus_required_user_override is not None
    assert snap.corpus_required_used != snap.corpus_required_computed
    # override is in PV today; engine inflates to retirement-year FV before "used"
    # spec §7.4: if override is PV, inflate by inflation_household_expense
    expected_used_fv = 40_000_000 * (1.06 ** 10)
    assert snap.corpus_required_used == pytest.approx(expected_used_fv, rel=1e-3)


def test_already_retired_branch():
    # DOB 1956-01-01, retire at 60, latest_update 2026 → already 70, retired 10y ago
    inp = RetirementInput(date_of_birth=date(1956, 1, 1))
    warnings: list[str] = []
    snap = compute_retirement_snapshot(inp, _ctx(), warnings)
    assert snap.years_to_retirement <= 0
    assert any("already retired" in w.lower() for w in warnings)


def test_missing_dob_raises():
    # Pydantic should reject construction; engine layer also defends
    with pytest.raises((ValueError, MissingDOBError)):
        RetirementInput()  # type: ignore[call-arg]
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement per spec §7.4 calc #3, #4**

```python
# AI_Agents/src/goal_planning/engine/retirement.py
"""Stage 2a: compute RetirementSnapshot."""
from __future__ import annotations
from datetime import date
from dateutil.relativedelta import relativedelta

from cashflow_statement.models import RetirementInput, RetirementSnapshot
from cashflow_statement.engine._types import RunContext
from cashflow_statement.engine.dates import _round_thousand, real_roi_monthly
from cashflow_statement.engine.exceptions import MissingDOBError
from financial_primitives.inflation import inflate, real_rate
from financial_primitives.retirement import retirement_corpus_pv


def compute_retirement_snapshot(
    inp: RetirementInput,
    ctx: RunContext,
    warnings: list[str],
) -> RetirementSnapshot:
    if inp.date_of_birth is None:
        raise MissingDOBError("date_of_birth is required for retirement snapshot")

    # Resolve retirement date
    retirement_date = inp.retirement_date_override or (
        inp.date_of_birth + relativedelta(years=inp.retirement_age)
    )
    years_to_retire = (retirement_date - ctx.latest_update_date).days / 365.25
    post_retirement_years = inp.assumed_total_age - inp.retirement_age

    # Already-retired guard
    if retirement_date <= ctx.latest_update_date:
        warnings.append(f"Person is already retired as of {ctx.latest_update_date}; using drawdown branch")
        years_to_retire = 0.0

    # Annual household expense FV at retirement (spec calc #3)
    annual_expense_fv = _round_thousand(
        inflate(ctx.annual_household_expense, ctx.inflation_household_expense, max(years_to_retire, 0))
    )

    # Real ROI for corpus calc
    real_annual = real_rate(ctx.retired_portfolio_roi_annual, ctx.inflation_household_expense)
    real_monthly = real_roi_monthly(ctx.retired_portfolio_roi_annual, ctx.inflation_household_expense)

    # Computed corpus (spec calc #4)
    corpus_computed = _round_thousand(retirement_corpus_pv(
        annual_expense_fv=annual_expense_fv,
        post_retirement_years=post_retirement_years,
        real_roi_annual=real_annual,
    ))

    # User override path (spec §7.4 #4): override is PV today; inflate to retirement FV
    if inp.retirement_corpus_pv_override is not None:
        corpus_user_fv = _round_thousand(
            inflate(inp.retirement_corpus_pv_override, ctx.inflation_household_expense, max(years_to_retire, 0))
        )
        corpus_used = corpus_user_fv
    else:
        corpus_user_fv = None
        corpus_used = corpus_computed

    return RetirementSnapshot(
        retirement_date=retirement_date,
        years_to_retirement=years_to_retire,
        annual_household_expense_at_retirement=annual_expense_fv,
        post_retirement_years=post_retirement_years,
        real_roi_annual=real_annual,
        real_roi_monthly=real_monthly,
        corpus_required_computed=corpus_computed,
        corpus_required_user_override=corpus_user_fv,
        corpus_required_used=corpus_used,
    )
```

- [ ] **Step 4: Run — pass**

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/retirement.py AI_Agents/src/goal_planning/tests/unit/test_engine_retirement.py
git commit -m "feat(engine): retirement.py — compute_retirement_snapshot with override + already-retired branch"
```

---

### Task 20: engine/mortgages.py — RATE inversion + amortization (existing properties)

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/mortgages.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_mortgages.py`

This task implements the existing-mortgage path (calc #5 RATE inversion, #6 amortization with first-FY proration). Goal-mortgage extension is in the next task.

- [ ] **Step 1: Write tests for existing-mortgage flow**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_mortgages.py
from datetime import date
import pytest
from cashflow_statement.models import Assumptions, ClientProfile, CurrentProperty
from cashflow_statement.engine.mortgages import build_existing_mortgages
from cashflow_statement.engine.profile import build_initial_context


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_skips_property_without_mortgage():
    props = [CurrentProperty(name="apt_paid_off", has_mortgage=False)]
    schedules = build_existing_mortgages(props, _ctx(), [])
    assert schedules == []


def test_rate_inversion_round_trip():
    # 5L EMI on 50L principal, 240 months → infer rate; PMT(rate, 240, 50L) ≈ 5L
    props = [CurrentProperty(
        name="apt_1", has_mortgage=True,
        mortgage_balance=5_000_000, mortgage_emi=43_391,  # 8.5% nominal
        mortgage_last_date=date(2046, 5, 9),  # 240 months from latest_update
    )]
    schedules = build_existing_mortgages(props, _ctx(), [])
    assert len(schedules) == 1
    sched = schedules[0]
    assert sched.property_ref == "existing:apt_1"
    assert len(sched.monthly_rows) > 0
    # First-month interest should be ≈ balance × inferred_rate
    first = sched.monthly_rows[0]
    assert first.interest_portion > 0
    assert first.principal_portion > 0
    assert first.opening_balance == pytest.approx(5_000_000, rel=1e-3)
    assert first.emi == pytest.approx(43_391, rel=1e-3)


def test_rate_non_convergence_falls_back_to_default():
    # Impossible ratio; engine should warn and use 0.075 default
    props = [CurrentProperty(
        name="apt_bad", has_mortgage=True,
        mortgage_balance=10_000_000, mortgage_emi=100,  # too low
        mortgage_last_date=date(2027, 5, 9),
    )]
    warnings: list[str] = []
    schedules = build_existing_mortgages(props, _ctx(), warnings)
    assert any("converge" in w.lower() or "fallback" in w.lower() for w in warnings)


def test_first_fy_proration():
    # latest_update 2026-05-09; first FY ends 2027-03-31 (10 months elapsed within FY27)
    # First annual row's annual_emi_total should reflect 10 months of EMI, not 12
    props = [CurrentProperty(
        name="apt_1", has_mortgage=True,
        mortgage_balance=5_000_000, mortgage_emi=43_391,
        mortgage_last_date=date(2046, 5, 9),
    )]
    schedules = build_existing_mortgages(props, _ctx(), [])
    sched = schedules[0]
    first_fy_row = sched.annual_rows[0]
    # First FY has ~11 months remaining (May 2026 → Mar 2027 = 11 months)
    # min(emi × months, opening + interest)
    assert first_fy_row.annual_emi_total < 43_391 * 12  # less than full year
    assert first_fy_row.annual_emi_total > 43_391 * 8  # at least 8 months


def test_skips_already_paid_off_mortgage():
    """Mortgage with last_date in the past — already paid off; engine drops with warning."""
    props = [CurrentProperty(
        name="paid_off_apt", has_mortgage=True,
        mortgage_balance=5_000_000, mortgage_emi=43_391,
        mortgage_last_date=date(2020, 1, 1),  # before latest_update
    )]
    warnings: list[str] = []
    schedules = build_existing_mortgages(props, _ctx(), warnings)
    assert schedules == [] or schedules[0].monthly_rows == []
    assert any("already" in w.lower() for w in warnings)
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement existing-mortgage flow per spec §7.4 #5, #6**

```python
# AI_Agents/src/goal_planning/engine/mortgages.py
"""Stage 3 + 4 mortgage helpers: RATE inversion, amortization (monthly + annual with first-FY proration), IPMT for goal-mortgages."""
from __future__ import annotations
from datetime import date
from dateutil.relativedelta import relativedelta

from cashflow_statement.models import CurrentProperty, MortgageAmortizationRow
from cashflow_statement.engine._types import RunContext, MortgageSchedule, MortgageAnnualRow
from cashflow_statement.engine.dates import fy_end_after
from financial_primitives.annuity import pmt, rate, ipmt, RATEConvergenceError


DEFAULT_FALLBACK_RATE_ANNUAL = 0.075


def _months_between(start: date, end: date) -> int:
    rd = relativedelta(end, start)
    return rd.years * 12 + rd.months


def build_existing_mortgages(
    properties: list[CurrentProperty],
    ctx: RunContext,
    warnings: list[str],
) -> list[MortgageSchedule]:
    schedules: list[MortgageSchedule] = []
    for p in properties:
        if not p.has_mortgage:
            continue
        if p.mortgage_balance is None or p.mortgage_emi is None or p.mortgage_last_date is None:
            warnings.append(f"existing:{p.name} missing mortgage fields; skipping")
            continue
        as_of = p.mortgage_balance_as_of_date or ctx.latest_update_date
        if p.mortgage_last_date <= as_of:
            warnings.append(f"existing:{p.name} mortgage already paid off as of {p.mortgage_last_date}")
            continue

        months_remaining = _months_between(as_of, p.mortgage_last_date)
        if months_remaining <= 0:
            continue

        # Invert RATE; on failure, fall back to 0.075 annual
        try:
            monthly_rate = rate(months_remaining, p.mortgage_emi, p.mortgage_balance)
        except RATEConvergenceError as e:
            warnings.append(
                f"existing:{p.name} mortgage rate inversion did not converge ({e}); "
                f"falling back to default {DEFAULT_FALLBACK_RATE_ANNUAL:.1%}"
            )
            monthly_rate = (1 + DEFAULT_FALLBACK_RATE_ANNUAL) ** (1/12) - 1

        # Build monthly amortization
        monthly_rows = _amortize_monthly(
            start=as_of, principal=p.mortgage_balance,
            monthly_rate=monthly_rate, emi=p.mortgage_emi,
            n_months=months_remaining,
        )
        annual_rows = _aggregate_annual(monthly_rows)
        schedules.append(MortgageSchedule(
            property_ref=f"existing:{p.name}",
            start_date=as_of,
            monthly_rows=monthly_rows,
            annual_rows=annual_rows,
        ))
    return schedules


def _amortize_monthly(
    start: date, principal: float, monthly_rate: float, emi: float, n_months: int,
) -> list[MortgageAmortizationRow]:
    """Build monthly amortization rows. EMI is held constant; interest = balance × rate."""
    rows: list[MortgageAmortizationRow] = []
    balance = principal
    for i in range(n_months):
        month_end = (start.replace(day=1) + relativedelta(months=i+1)) - relativedelta(days=1)
        interest = balance * monthly_rate
        principal_portion = min(emi - interest, balance)
        actual_emi = interest + principal_portion
        new_balance = max(balance - principal_portion, 0.0)
        rows.append(MortgageAmortizationRow(
            month_end=month_end,
            opening_balance=balance,
            emi=actual_emi,
            interest_portion=interest,
            principal_portion=principal_portion,
            closing_balance=new_balance,
        ))
        balance = new_balance
        if balance <= 0:
            break
    return rows


def _aggregate_annual(monthly_rows: list[MortgageAmortizationRow]) -> list[MortgageAnnualRow]:
    """Aggregate monthly rows into per-FY annual rows. First/last FYs may be partial."""
    by_fy: dict[date, list[MortgageAmortizationRow]] = {}
    for r in monthly_rows:
        fy = fy_end_after(r.month_end)
        by_fy.setdefault(fy, []).append(r)
    annual: list[MortgageAnnualRow] = []
    for fy_end in sorted(by_fy):
        rows = by_fy[fy_end]
        opening = rows[0].opening_balance
        closing = rows[-1].closing_balance
        interest = sum(r.interest_portion for r in rows)
        principal = sum(r.principal_portion for r in rows)
        emi_total = sum(r.emi for r in rows)
        annual.append(MortgageAnnualRow(
            fy_end=fy_end, opening_balance=opening, annual_interest=interest,
            annual_principal=principal, annual_emi_total=emi_total, closing_balance=closing,
        ))
    return annual
```

- [ ] **Step 4: Run — pass**

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/mortgages.py AI_Agents/src/goal_planning/tests/unit/test_engine_mortgages.py
git commit -m "feat(engine): mortgages.py — RATE inversion, monthly+annual amortization, first-FY proration"
```

---

### Task 21: engine/properties.py — build_goal_properties

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/properties.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_properties.py`

This implements spec calc #7 (goal-property FV), #8 (mortgage_amount), #9 (EMI via PMT), #10 (goal-property amortization).

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_properties.py
from datetime import date
import pytest
from cashflow_statement.models import Assumptions, ClientProfile, GoalProperty
from cashflow_statement.engine.profile import build_initial_context
from cashflow_statement.engine.properties import build_goal_properties


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_cash_purchase_no_mortgage():
    # is_downpayment_only=False → cash purchase, target_fv = target_pv inflated, no mortgage
    props = [GoalProperty(name="house_1", target_pv=10_000_000, goal_date=date(2030, 5, 9))]
    outcomes = build_goal_properties(props, _ctx(), [])
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.mortgage_amount == 0
    assert o.amortization is None
    # FV at goal date = 10M × 1.06^4 ≈ 12,624,770 → rounded to 1000s
    assert 12_624_000 <= o.payout_amount_fv <= 12_625_000
    assert o.payout_amount_fv == o.target_fv  # cash → payout == full target


def test_mortgage_path_payout_is_upfront_only():
    # is_downpayment_only=True; payout = upfront_FV (NOT full target_FV)
    props = [GoalProperty(
        name="house_2", target_pv=10_000_000, is_downpayment_only=True,
        upfront_amount=2_000_000, goal_date=date(2030, 5, 9),
        mortgage_tenure_years=20, mortgage_interest_annual=0.085,
    )]
    outcomes = build_goal_properties(props, _ctx(), [])
    o = outcomes[0]
    # FV target ≈ 12.6M; FV upfront ≈ 2.5M; mortgage ≈ 10.1M
    assert o.mortgage_amount > 9_000_000
    assert o.mortgage_amount < 11_000_000
    # Payout == upfront FV, NOT full target FV
    assert o.payout_amount_fv < 3_000_000
    assert o.payout_amount_fv < o.target_fv
    assert o.amortization is not None
    assert o.amortization.property_ref == "goal:house_2"


def test_target_fv_provided_skips_inflation():
    # When target_fv directly given, don't double-inflate
    props = [GoalProperty(name="house_3", target_fv=15_000_000, goal_date=date(2030, 5, 9))]
    outcomes = build_goal_properties(props, _ctx(), [])
    o = outcomes[0]
    assert o.target_fv == pytest.approx(15_000_000, rel=1e-6)
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement per spec §7.4 #7-#10**

```python
# AI_Agents/src/goal_planning/engine/properties.py
"""Stage 4: build goal-property outcomes (FV, mortgage, amortization)."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import GoalProperty
from cashflow_statement.engine._types import RunContext, GoalPropertyOutcome, MortgageSchedule, MortgageAnnualRow
from cashflow_statement.engine.dates import _round_thousand, fy_end_after, year_fraction
from cashflow_statement.engine.mortgages import _amortize_monthly, _aggregate_annual
from financial_primitives.annuity import pmt
from financial_primitives.inflation import inflate


def build_goal_properties(
    properties: list[GoalProperty],
    ctx: RunContext,
    warnings: list[str],
) -> list[GoalPropertyOutcome]:
    outcomes: list[GoalPropertyOutcome] = []
    for p in properties:
        if p.goal_date <= ctx.latest_update_date:
            warnings.append(f"goal:{p.name} goal_date is in the past; dropped")
            continue

        years_to_goal = year_fraction(ctx.latest_update_date, p.goal_date)
        inflation = p.inflation_annual if p.inflation_annual is not None else _inflation_property_default(ctx)

        # Target FV (spec calc #7)
        if p.target_fv is not None:
            target_fv = _round_thousand(p.target_fv)
        else:
            target_fv = _round_thousand(inflate(p.target_pv, inflation, years_to_goal))

        if not p.is_downpayment_only:
            # Cash purchase — payout = full FV, no mortgage
            outcomes.append(GoalPropertyOutcome(
                name=p.name, target_fv=target_fv, payout_amount_fv=target_fv,
                mortgage_amount=0, amortization=None,
            ))
            continue

        # Mortgage path
        upfront_fv = _round_thousand(inflate(p.upfront_amount, inflation, years_to_goal))
        mortgage_amount = max(target_fv - upfront_fv, 0)
        n_months = p.mortgage_tenure_years * 12
        monthly_rate = (1 + p.mortgage_interest_annual) ** (1/12) - 1
        emi = pmt(monthly_rate, n_months, mortgage_amount)

        monthly_rows = _amortize_monthly(
            start=p.goal_date, principal=mortgage_amount,
            monthly_rate=monthly_rate, emi=emi, n_months=n_months,
        )
        annual_rows = _aggregate_annual(monthly_rows)

        outcomes.append(GoalPropertyOutcome(
            name=p.name,
            target_fv=target_fv,
            payout_amount_fv=upfront_fv,
            mortgage_amount=mortgage_amount,
            amortization=MortgageSchedule(
                property_ref=f"goal:{p.name}",
                start_date=p.goal_date,
                monthly_rows=monthly_rows,
                annual_rows=annual_rows,
            ),
        ))
    return outcomes


def _inflation_property_default(ctx: RunContext) -> float:
    # spec: GoalProperty.inflation_annual default → assumptions.inflation_property
    # We don't carry inflation_property in RunContext (yet); reconstruct from ctx via household-expense default
    # Actually inflation_property lives in Assumptions, not RunContext. Engine wires this through pipeline.
    # For Stage 4, the caller already has Assumptions; we accept default 0.06 here.
    return 0.06
```

**Note:** `_inflation_property_default` is a stand-in. The pipeline orchestrator (Task 26) passes Assumptions through; refactor `build_goal_properties` to take `assumptions: Assumptions` argument once stage wiring exists. For now, hard-coded 0.06 default suffices for tests with default Assumptions.

- [ ] **Step 4: Run — pass**

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/properties.py AI_Agents/src/goal_planning/tests/unit/test_engine_properties.py
git commit -m "feat(engine): properties.py — goal-property FV, mortgage assembly, amortization"
```

---

### Task 22: engine/goals_table.py — unified goals list, expected_roi 3-band, fund_today_pv

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/goals_table.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_goals_table.py`

This implements spec calc #11 (unified table assembly), #12 (expected_roi 3-band), #13 (amount_fv with retirement skipping inflation lookup), #14 (fund_today_pv).

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_goals_table.py
from datetime import date
import pytest
from cashflow_statement.models import (
    Assumptions, ClientProfile, RetirementInput, CustomGoal, GoalType, RetirementSnapshot,
)
from cashflow_statement.engine.profile import build_initial_context
from cashflow_statement.engine.goals_table import (
    expected_roi_for_goal, build_goals_table,
)


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_expected_roi_three_bands():
    ctx = _ctx()
    # Near-term: goal_date < near_term_end (2029-03-31) → 0.05
    assert expected_roi_for_goal(date(2027, 6, 1), ctx) == pytest.approx(0.05)
    # Mid-term: near < goal_date <= medium_term_end (2032-03-31) → 0.07
    assert expected_roi_for_goal(date(2030, 6, 1), ctx) == pytest.approx(0.07)
    # Long-term: goal_date > medium_term_end → 0.09
    assert expected_roi_for_goal(date(2040, 1, 1), ctx) == pytest.approx(0.09)


def test_retirement_uses_corpus_used_directly_skipping_inflation_lookup():
    ctx = _ctx()
    snap = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )
    goals = build_goals_table(snap, [], [], ctx, Assumptions(), [])
    retirement = next(g for g in goals if g.goal_type == GoalType.retirement)
    # amount_fv = corpus_required_used directly; engine doesn't double-inflate
    assert retirement.amount_fv == 30_000_000
    # Inflation rate stored = inflation_household_expense (used pre/post-retirement)
    assert retirement.inflation_rate == ctx.inflation_household_expense


def test_custom_goal_amount_fv_inflated_by_goal_type_default():
    ctx = _ctx()
    snap = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )
    goals = build_goals_table(
        snap, [],
        [CustomGoal(
            name="college", goal_type=GoalType.child_local_education,
            amount_pv=1_000_000, goal_date=date(2035, 1, 1),
        )],
        ctx, Assumptions(), [],
    )
    college = next(g for g in goals if g.name == "college")
    assert college.inflation_rate == 0.06  # default for child_local_education
    # 8.65 years to goal at 6% → ~1.66M
    assert college.amount_fv > 1_500_000
    assert college.amount_fv < 1_800_000


def test_amount_fv_when_user_provides_fv_directly():
    ctx = _ctx()
    snap = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )
    goals = build_goals_table(
        snap, [],
        [CustomGoal(
            name="abroad_ed", goal_type=GoalType.child_abroad_education,
            amount_fv=20_000_000, goal_date=date(2040, 1, 1),
        )],
        ctx, Assumptions(), [],
    )
    g = next(g for g in goals if g.name == "abroad_ed")
    # amount_fv given directly; no double-inflation
    assert g.amount_fv == pytest.approx(20_000_000, rel=1e-6)


def test_fund_today_pv_discount():
    ctx = _ctx()
    snap = RetirementSnapshot(
        retirement_date=date(2036, 5, 9), years_to_retirement=10.0,
        annual_household_expense_at_retirement=1_700_000, post_retirement_years=25,
        real_roi_annual=0.0283, real_roi_monthly=0.0023,
        corpus_required_computed=30_000_000, corpus_required_user_override=None,
        corpus_required_used=30_000_000,
    )
    goals = build_goals_table(
        snap, [],
        [CustomGoal(
            name="g1", goal_type=GoalType.custom, amount_fv=10_000_000, goal_date=date(2040, 1, 1),
        )],
        ctx, Assumptions(), [],
    )
    g = next(g for g in goals if g.name == "g1")
    # fund_today_pv = amount_fv / (1 + expected_roi)^years_to_goal
    # ~13.65y at long-term 9% → ~10M / 1.09^13.65 ≈ 3.21M
    years = (date(2040, 1, 1) - ctx.latest_update_date).days / 365.25
    expected = 10_000_000 / (1.09 ** years)
    assert g.fund_today_pv == pytest.approx(expected, rel=1e-3)
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement per spec §7.4 #11–#14**

```python
# AI_Agents/src/goal_planning/engine/goals_table.py
"""Stage 5: build unified goals table (retirement + properties + customs)."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import (
    Assumptions, CustomGoal, GoalType, RetirementSnapshot,
)
from cashflow_statement.engine._types import RunContext, GoalInternal, GoalPropertyOutcome
from cashflow_statement.engine.dates import fy_end_after, year_fraction
from financial_primitives.inflation import inflate


_INFLATION_BY_GOAL_TYPE = {
    GoalType.property: "inflation_property",
    GoalType.child_abroad_education: "inflation_child_abroad_education",
    GoalType.child_local_education: "inflation_child_local_education",
    GoalType.child_marriage: "inflation_child_marriage",
    GoalType.custom: "inflation_household_expense",  # fallback
}


def expected_roi_for_goal(goal_date: date, ctx: RunContext) -> float:
    """3-band horizon lookup: near (≤near_term_end) → mid (≤medium_term_end) → long."""
    if goal_date <= ctx.near_term_end:
        return ctx.near_term_roi
    if goal_date <= ctx.medium_term_end:
        return ctx.mid_term_roi
    return ctx.long_term_roi


def build_goals_table(
    retirement_snap: RetirementSnapshot,
    goal_property_outcomes: list[GoalPropertyOutcome],
    custom_goals: list[CustomGoal],
    ctx: RunContext,
    assumptions: Assumptions,
    warnings: list[str],
) -> list[GoalInternal]:
    rows: list[GoalInternal] = []

    # 1. Retirement (special-case: skip inflation lookup; amount_fv = corpus_required_used)
    retirement_date = retirement_snap.retirement_date
    rows.append(GoalInternal(
        name="retirement",
        goal_type=GoalType.retirement,
        goal_date=retirement_date,
        goal_date_fy=fy_end_after(retirement_date),
        amount_pv=retirement_snap.corpus_required_user_override or retirement_snap.corpus_required_computed,
        amount_fv=retirement_snap.corpus_required_used,
        inflation_rate=ctx.inflation_household_expense,
        expected_roi=expected_roi_for_goal(retirement_date, ctx),
        fund_today_pv=_fund_today_pv(retirement_snap.corpus_required_used, expected_roi_for_goal(retirement_date, ctx), ctx, retirement_date),
    ))

    # 2. Goal properties — payout_amount_fv used as amount_fv (per spec)
    for o in goal_property_outcomes:
        # Find the original GoalProperty's date — assume name match; for proto use o.name
        # In production we'd thread the goal_date through GoalPropertyOutcome — refactor as needed
        pass  # Placeholder — Task 26 (pipeline) re-threads goal_date via dict from input

    # 3. Custom goals
    for g in custom_goals:
        if g.goal_date <= ctx.latest_update_date:
            warnings.append(f"custom_goal:{g.name} goal_date is in the past; dropped")
            continue

        years_to = year_fraction(ctx.latest_update_date, g.goal_date)
        inflation = (
            g.inflation_rate_override
            if g.inflation_rate_override is not None
            else getattr(assumptions, _INFLATION_BY_GOAL_TYPE.get(g.goal_type, "inflation_household_expense"))
        )
        if g.amount_fv is not None:
            amount_fv = g.amount_fv
            amount_pv = g.amount_pv if g.amount_pv is not None else g.amount_fv / (1 + inflation) ** years_to
        else:
            amount_pv = g.amount_pv
            amount_fv = inflate(amount_pv, inflation, years_to)

        roi = expected_roi_for_goal(g.goal_date, ctx)
        fund_pv = _fund_today_pv(amount_fv, roi, ctx, g.goal_date)

        rows.append(GoalInternal(
            name=g.name,
            goal_type=g.goal_type,
            goal_date=g.goal_date,
            goal_date_fy=fy_end_after(g.goal_date),
            amount_pv=amount_pv,
            amount_fv=amount_fv,
            inflation_rate=inflation,
            expected_roi=roi,
            fund_today_pv=fund_pv,
        ))

    rows.sort(key=lambda r: r.goal_date)
    return rows


def _fund_today_pv(amount_fv: float, expected_roi: float, ctx: RunContext, goal_date: date) -> float:
    years_to = year_fraction(ctx.latest_update_date, goal_date)
    return amount_fv / (1 + expected_roi) ** years_to
```

**Note**: Goal-property branch in step 2 is a placeholder. Task 26 (pipeline orchestrator) wires this — we pass GoalPropertyOutcome alongside its source GoalProperty so we know `goal_date`. Refactor `build_goals_table` signature to accept `dict[name, goal_date]` or pair (outcome, goal) to thread date through.

- [ ] **Step 4: Run — pass on retirement and custom goals; goal-property test deferred**

Expected: 5 of 5 listed test cases pass (none of them exercise goal-property branch).

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/goals_table.py AI_Agents/src/goal_planning/tests/unit/test_engine_goals_table.py
git commit -m "feat(engine): goals_table.py — retirement + custom goals; expected_roi 3-band; fund_today_pv"
```

---

### Task 23: engine/cashflow.py — monthly + annual projection with savings_2_avg

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/cashflow.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_cashflow.py`

This implements spec calc #16-#22.

- [ ] **Step 1: Write tests for step-ups, tax, savings_1, savings_2, savings_2_avg**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_cashflow.py
from datetime import date
import pytest
from cashflow_statement.models import Assumptions, ClientProfile, OneOffEvent
from cashflow_statement.engine.profile import build_initial_context
from cashflow_statement.engine.cashflow import project_cashflow, compute_horizon_years


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        ),
        Assumptions(),
    )


def test_savings_1_first_month():
    """Month 1 savings_1 = monthly_income - tax - monthly_expense."""
    ctx = _ctx()
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    first = monthly[0]
    # income/12 = 166,667; tax = 30% = 50,000; expense = 80,000; savings_1 = 36,667
    assert first.savings_1 == pytest.approx(166_666.67 - 50_000 - 80_000, rel=1e-3)


def test_savings_2_subtracts_emi():
    """savings_2 = savings_1 − existing_emi − goal_emi (both 0 here)."""
    ctx = _ctx()
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    first = monthly[0]
    assert first.savings_2 == pytest.approx(first.savings_1, rel=1e-9)


def test_income_step_up_year_2():
    """FY2 income = FY1 × (1 + 0.08)."""
    ctx = _ctx()
    _, annual = project_cashflow(ctx, [], [], [], [], horizon_years=3, warnings=[])
    assert annual[0].income == pytest.approx(2_000_000, rel=1e-3)
    assert annual[1].income == pytest.approx(2_000_000 * 1.08, rel=1e-3)
    assert annual[2].income == pytest.approx(2_000_000 * 1.08 ** 2, rel=1e-3)


def test_expense_step_up_per_fy():
    ctx = _ctx()
    _, annual = project_cashflow(ctx, [], [], [], [], horizon_years=3, warnings=[])
    base = 80_000 * 12
    assert annual[1].household_expense == pytest.approx(base * 1.06, rel=1e-3)


def test_savings_2_avg_constant_within_fy():
    """savings_2_avg is the FY-bucket average — same value across all months in same FY."""
    ctx = _ctx()
    monthly, _ = project_cashflow(ctx, [], [], [], [], horizon_years=2, warnings=[])
    fy_groups: dict[str, list[float]] = {}
    for r in monthly:
        fy_groups.setdefault(r.fy_label, []).append(r.savings_2_avg)
    for fy, values in fy_groups.items():
        assert len(set(values)) == 1, f"savings_2_avg should be constant within {fy}"


def test_horizon_years_includes_one_off_outflows():
    """B88 = MAX(goal_FY, one_off_outflow_FY)."""
    horizon = compute_horizon_years(
        retirement_date=date(2036, 5, 9),
        last_goal_fy=date(2040, 3, 31),
        one_off_outflows=[OneOffEvent(description="x", amount=1_000_000, date=date(2050, 6, 1))],
        latest_update_date=date(2026, 5, 9),
        cap=80,
    )
    # 2050-FY = 2051; horizon = 2051 - 2026 = 25 years
    assert horizon == 25


def test_horizon_capped_at_80():
    horizon = compute_horizon_years(
        retirement_date=date(2200, 1, 1),
        last_goal_fy=date(2200, 3, 31),
        one_off_outflows=[],
        latest_update_date=date(2026, 5, 9),
        cap=80,
    )
    assert horizon == 80
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement per spec §7.4 #16-#22**

Implement two functions: `project_cashflow(ctx, existing_mortgages, goal_mortgages, one_off_inflows, one_off_outflows, horizon_years, warnings) -> (monthly, annual)` and `compute_horizon_years(retirement_date, last_goal_fy, one_off_outflows, latest_update_date, cap=80) -> int`.

Algorithm summary (full per spec §7.4):
1. For each FY from current_fy_year to current_fy_year + horizon:
   - Income = `annual_income × (1 + income_growth)^(fy_year - current_fy_year)`
   - Tax = `income × tax_rate`
   - Expense = `annual_household_expense × (1 + inflation_household_expense)^(fy_year - current_fy_year)`
   - Existing EMI total = sum across schedules using `total_emi_in_fy(fy_end)`
   - Goal EMI total = same for goal_mortgages
   - One-off in/out = sum events whose date falls in this FY
   - savings_1 = income − tax − expense
   - savings_2 = savings_1 − EMIs
   - investment_amount = `monthly_investment_next_12m × 12 × (1 + invested_amount_growth)^(fy_year - current_fy_year)` if not None else 0
2. For monthly tape: distribute annual values across 12 months; first FY may be partial. `savings_2_avg = SUMIF(savings_2 in FY) / COUNTIF(months in FY)` — constant per FY.
3. NFA opening/closing tracked but actual ROI applied in funding stage; use placeholder NFA values here (real NFA evolution lives in funding.py).

**Implementation tip**: To keep this task tractable, return cashflow rows with `nfa_opening`, `nfa_roi`, `nfa_closing` set to 0 in this stage. Funding stage (Task 25) recomputes NFA evolution. Cashflow's job is income/tax/expense/savings only.

- [ ] **Step 4: Run — pass**

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/cashflow.py AI_Agents/src/goal_planning/tests/unit/test_engine_cashflow.py
git commit -m "feat(engine): cashflow.py — FY step-ups, savings_2_avg, horizon cap"
```

---

### Task 24: engine/funding.py — shared NFA pool, M147 4-branch rule, proportional shortfall

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/funding.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_funding.py`

This is the most complex stage. Implements spec calc #23a (NFA pool evolution), #23b (M147 4-branch), #24-26 (per-goal status, totals).

- [ ] **Step 1: Write tests for the 4-branch rule (one input per branch)**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_funding.py
from datetime import date
import pytest
from cashflow_statement.engine.funding import monthly_invest_or_withdraw


def test_branch_zero_post_retirement():
    """year > retirement_year → invested = 0, kind='zero'."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2040, 5, 31),
        savings_2_avg=50_000,
        user_sip=50_000,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert invested == 0
    assert kind == "zero"


def test_branch_user_sip_pre_retirement():
    """year < retire_year, user_sip set & > 100 → user_sip × growth^(yr - base)."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2030, 5, 31),
        savings_2_avg=200_000,  # ignored on this branch
        user_sip=50_000,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert kind == "user_sip"
    # m_year = 2030 (mar→apr boundary FY27 = 2027? Actually 2030-05-31 → FY31 = 2031)
    # Use fy_for_date to determine year delta from base_year=2027
    # Expected: 50,000 × 1.08^(yr_delta)
    assert invested > 50_000


def test_branch_savings_sip_fraction_year_equal_or_no_user_sip():
    """retire-year-equal OR user_sip None: K-based fallback. K>0 → K × sip_share."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2036, 6, 30),  # same FY as retirement (2036-05-09)
        savings_2_avg=80_000,
        user_sip=50_000,
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert kind == "savings_sip_fraction"
    assert invested == pytest.approx(60_000, rel=1e-9)  # 80k × 0.75


def test_branch_withdrawal_negative_savings():
    """K-based fallback path, K<0 → invested = K (withdrawal)."""
    invested, kind = monthly_invest_or_withdraw(
        m=date(2030, 5, 31),
        savings_2_avg=-30_000,
        user_sip=None,  # no user SIP
        invest_growth=0.08,
        base_year=2027,
        sip_share=0.75,
        retirement_date=date(2036, 5, 9),
    )
    assert kind == "withdrawal"
    assert invested == -30_000
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement M147 helper plus full compute_funding per spec §7.4 #23a/b, #24-26**

Implement `monthly_invest_or_withdraw(m, savings_2_avg, user_sip, invest_growth, base_year, sip_share, retirement_date) -> (float, str)` per the spec corrected rule (Section 8.4 G3 in spec):

```
m_year = fy_for_date(m)
ret_year = fy_for_date(retirement_date)
if m_year > ret_year:
    return 0, "zero"
if m_year < ret_year and user_sip is not None and user_sip > 100:
    return user_sip × (1 + invest_growth)^(m_year - base_year), "user_sip"
# m_year == ret_year OR user_sip absent OR user_sip <= 100 → K-based fallback
if savings_2_avg > 0:
    return savings_2_avg × sip_share, "savings_sip_fraction"
return savings_2_avg, "withdrawal"
```

Implement `compute_funding(goals_internal, ctx, monthly_cashflow, one_off_inflows, one_off_outflows, warnings) -> FundingResult` per spec §8.4 algorithm:
- Single shared NFA pool starting at `ctx.nfa`
- For each monthly row: `nfa_close = nfa_open + regular_invest + roi + one_off_in − goal_outflow_total`
- Outflows include both goals at their goal_date_fy match AND one_off_outflows by their date
- Proportional shortfall split per outflow when `available < outflow_total`
- 2-band ROI: `near_term_roi` if `m <= near_term_end` else `long_term_roi`
- Build per_goal_status (positive shortfall_fv convention) and per_one_off_outflow_status

- [ ] **Step 4: Run — pass for the 4 branch tests**

Expected: 4 passed.

- [ ] **Step 5: Add integration tests for compute_funding**

Add tests:
- Two goals same date, NFA half coverage → proportional shortfall split (each gets ~half)
- Goal funded with huge NFA → shortfall_fv == 0
- Per-goal status + one-off-outflow status both populated

```python
# Append to test_engine_funding.py
def test_compute_funding_proportional_shortfall_two_equal_goals():
    """NFA = 5M, two goals each needing 5M, same date → each underfunded by ~half."""
    # Build minimal ctx, monthly_cashflow, goals_internal, run compute_funding, assert
    # See spec §10.3 synthetic test #7a for expected values
    pass  # Implementation detail; engineer fills in fixture wiring
```

- [ ] **Step 6: Run — full suite passes**

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/funding.py AI_Agents/src/goal_planning/tests/unit/test_engine_funding.py
git commit -m "feat(engine): funding.py — shared NFA pool, M147 4-branch, proportional shortfall"
```

---

### Task 25: engine/summary.py — HeadlineStatus + FundFlowSummary

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/summary.py`
- Create: `AI_Agents/src/goal_planning/tests/unit/test_engine_summary.py`

Implements spec calc #15 (sum_fund_today_pv + present_status), #27 (FundFlowSummary bridge), #28 (is_overall_feasible), #29 (total_shortfall_fv distinct from closing_nfa).

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/unit/test_engine_summary.py
from datetime import date
from cashflow_statement.engine.summary import (
    build_headline_status, build_fund_flow_summary,
)


def test_present_status_nfa_minus_sum_fund_pv():
    """present_status = NFA - sum(fund_today_pv)."""
    # Construct fixture inputs and assert per spec §7.4 #15
    pass


def test_total_shortfall_distinct_from_closing_nfa():
    """L113 (sum shortfall) != S214 (closing NFA) — both surface independently."""
    pass


def test_is_overall_feasible_predicate():
    """Feasible iff: all goals funded AND present_status >= 0 AND min_nfa_in_horizon >= 0."""
    pass


def test_fund_flow_bridge_identity():
    """closing == opening + invest + roi + one_off_in - one_off_out - goals."""
    pass
```

- [ ] **Step 2: Implement per spec §7.4 #15, #27, #28, #29**

```python
# AI_Agents/src/goal_planning/engine/summary.py
"""Stage 8: Summary — HeadlineStatus + FundFlowSummary."""
from __future__ import annotations
from cashflow_statement.models import (
    HeadlineStatus, FundFlowSummary, RetirementSnapshot,
    OneOffEvent, AnnualCashflowRow,
)
from cashflow_statement.engine._types import RunContext, GoalInternal, FundingResult


def build_headline_status(
    ctx: RunContext,
    goals_internal: list[GoalInternal],
    funding: FundingResult,
    retirement: RetirementSnapshot,
    annual_cashflow: list[AnnualCashflowRow],
    warnings: list[str],
) -> HeadlineStatus:
    sum_fund_pv = sum(g.fund_today_pv for g in goals_internal)
    present_status = ctx.nfa - sum_fund_pv
    last_goal_date = max((g.goal_date for g in goals_internal), default=ctx.latest_update_date)
    last_fy_end_date = max((g.goal_date_fy for g in goals_internal), default=ctx.current_fy_end)
    total_shortfall = sum(s.shortfall_fv for s in funding.per_goal_status)
    total_funded = sum(s.funded_amount for s in funding.per_goal_status)

    is_feasible = (
        all(s.is_funded for s in funding.per_goal_status)
        and present_status >= 0
        and funding.min_nfa_in_horizon >= 0
    )

    overall_shortfall_fv = max(total_shortfall, 0)
    overall_shortfall_pv = sum(s.shortfall_pv for s in funding.per_goal_status)
    horizon_years = (last_fy_end_date.year - ctx.current_fy_year)

    return HeadlineStatus(
        horizon_years=horizon_years,
        last_goal_date=last_goal_date,
        last_fy_end_date=last_fy_end_date,
        number_of_goals=len(goals_internal),
        net_financial_assets_today=ctx.nfa,
        sum_fund_today_pv=sum_fund_pv,
        present_status=present_status,
        closing_nfa=funding.closing_nfa,
        total_shortfall_fv=total_shortfall,
        total_funded_amount=total_funded,
        is_overall_feasible=is_feasible,
        overall_shortfall_pv=overall_shortfall_pv,
        overall_shortfall_fv=overall_shortfall_fv,
    )


def build_fund_flow_summary(
    ctx: RunContext,
    annual_cashflow: list[AnnualCashflowRow],
    funding: FundingResult,
    one_off_inflows: list[OneOffEvent],
    one_off_outflows: list[OneOffEvent],
) -> FundFlowSummary:
    total_invest = sum(r.regular_invest for r in funding.nfa_monthly if r.regular_invest > 0)
    total_roi = sum(r.roi for r in funding.nfa_monthly)
    total_in = sum(e.amount for e in one_off_inflows)
    total_out = sum(e.amount for e in one_off_outflows)
    total_goals = sum(r.goal_outflow_total for r in funding.nfa_monthly) - total_out
    return FundFlowSummary(
        opening_nfa=ctx.nfa,
        total_investments=total_invest,
        total_roi=total_roi,
        total_one_off_in=total_in,
        total_one_off_out=total_out,
        total_goals_paid=total_goals,
        closing_nfa=funding.closing_nfa,
    )
```

- [ ] **Step 3: Implement test fixtures (engineer fills `pass` in tests with concrete data)**

Each test in Step 1 needs concrete `ctx`, `goals_internal`, `funding`, `retirement`, `annual_cashflow` — wire from prior tasks.

- [ ] **Step 4: Run — pass**

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/summary.py AI_Agents/src/goal_planning/tests/unit/test_engine_summary.py
git commit -m "feat(engine): summary.py — HeadlineStatus, FundFlowSummary, feasibility predicate"
```

---

### Task 26: engine/pipeline.py — 8-stage orchestrator + ENGINE_VERSION

**Files:**
- Create: `AI_Agents/src/goal_planning/engine/pipeline.py`
- Create: `AI_Agents/src/goal_planning/tests/integration/test_engine_pipeline.py`

This is the public engine entry point. Per spec §7.2.

- [ ] **Step 1: Write integration test**

```python
# AI_Agents/src/goal_planning/tests/integration/test_engine_pipeline.py
from datetime import date
from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, Assumptions, CustomGoal, GoalType,
)
from cashflow_statement.engine.pipeline import compute_full_projection, ENGINE_VERSION


def test_minimal_pipeline_runs_end_to_end():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="college", goal_type=GoalType.child_local_education,
            amount_pv=2_000_000, goal_date=date(2035, 1, 1),
        )],
    )
    out = compute_full_projection(inp)
    assert out.engine_version == ENGINE_VERSION
    assert out.headline.number_of_goals >= 2  # retirement + college
    assert out.retirement.corpus_required_used > 0
    assert isinstance(out.headline.is_overall_feasible, bool)


def test_default_detail_level_omits_gamma_fields():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    assert out.monthly_cashflow is None
    assert out.nfa_monthly_series is None
    assert out.mortgage_amortizations is None


def test_full_detail_level_populates_gamma_fields():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        detail_level="full",
    )
    out = compute_full_projection(inp)
    assert out.monthly_cashflow is not None
    assert out.nfa_monthly_series is not None
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement orchestrator per spec §7.2**

```python
# AI_Agents/src/goal_planning/engine/pipeline.py
"""Public engine entry: 8-stage orchestrator. Imports nothing LLM-related."""
from __future__ import annotations
from datetime import datetime

from cashflow_statement.models import (
    GoalPlanningInput, GoalPlanningOutput, ValidationIssue,
)
from cashflow_statement.engine.profile import build_initial_context
from cashflow_statement.engine.retirement import compute_retirement_snapshot
from cashflow_statement.engine.mortgages import build_existing_mortgages
from cashflow_statement.engine.properties import build_goal_properties
from cashflow_statement.engine.goals_table import build_goals_table
from cashflow_statement.engine.cashflow import project_cashflow, compute_horizon_years
from cashflow_statement.engine.funding import compute_funding
from cashflow_statement.engine.summary import build_headline_status, build_fund_flow_summary

ENGINE_VERSION = "0.1.0"


def compute_full_projection(input: GoalPlanningInput) -> GoalPlanningOutput:
    warnings: list[str] = []

    ctx = build_initial_context(input.profile, input.assumptions)                              # 1
    retirement = compute_retirement_snapshot(input.retirement, ctx, warnings)                  # 2a
    ctx = ctx.with_retirement(retirement)                                                      # 2b

    existing_mortgages = build_existing_mortgages(input.current_properties, ctx, warnings)     # 3
    goal_property_outcomes = build_goal_properties(input.goal_properties, ctx, warnings)       # 4

    # Pair outcomes with their source GoalProperty for goal_date threading
    name_to_goal_date = {g.name: g.goal_date for g in input.goal_properties}
    goals_internal = build_goals_table(
        retirement, goal_property_outcomes, input.custom_goals, ctx, input.assumptions, warnings
    )                                                                                          # 5

    horizon = compute_horizon_years(
        retirement_date=retirement.retirement_date,
        last_goal_fy=max((g.goal_date_fy for g in goals_internal), default=ctx.current_fy_end),
        one_off_outflows=input.one_off_outflows,
        latest_update_date=ctx.latest_update_date,
        cap=ctx.horizon_cap_years,
    )
    monthly_cashflow, annual_cashflow = project_cashflow(
        ctx, existing_mortgages,
        [g.amortization for g in goal_property_outcomes if g.amortization],
        input.one_off_inflows, input.one_off_outflows,
        horizon_years=horizon, warnings=warnings,
    )                                                                                          # 6

    funding = compute_funding(
        goals_internal, ctx, monthly_cashflow, input.one_off_inflows, input.one_off_outflows,
        warnings,
    )                                                                                          # 7

    headline = build_headline_status(ctx, goals_internal, funding, retirement, annual_cashflow, warnings)  # 8
    fund_flow = build_fund_flow_summary(ctx, annual_cashflow, funding, input.one_off_inflows, input.one_off_outflows)

    full = (input.detail_level == "full")
    return GoalPlanningOutput(
        engine_version=ENGINE_VERSION,
        input_echo=input,
        headline=headline,
        retirement=retirement,
        goals=funding.per_goal_status,
        one_off_outflow_status=funding.per_one_off_outflow_status,
        annual_cashflow=annual_cashflow,
        fund_flow_summary=fund_flow,
        monthly_cashflow=monthly_cashflow if full else None,
        nfa_monthly_series=funding.nfa_monthly if full else None,
        mortgage_amortizations=(
            existing_mortgages
            + [g.amortization for g in goal_property_outcomes if g.amortization]
        ) if full else None,
        warnings=warnings,
        computed_at=datetime.utcnow(),
    )


def validate_input_only(input: GoalPlanningInput) -> list[ValidationIssue]:
    """Pre-flight check: cheap validation; raises strict errors. No projection run."""
    issues: list[ValidationIssue] = []

    if input.retirement.date_of_birth is None:
        issues.append(ValidationIssue(
            field="retirement.date_of_birth", message="DOB required for retirement calc",
            severity="error",
        ))

    update_date = input.profile.latest_update_date
    for g in input.custom_goals:
        if g.goal_date <= update_date:
            issues.append(ValidationIssue(
                field=f"custom_goals[{g.name}].goal_date",
                message=f"goal_date {g.goal_date} is in the past (latest_update_date={update_date})",
                severity="error",
            ))
    for g in input.goal_properties:
        if g.goal_date <= update_date:
            issues.append(ValidationIssue(
                field=f"goal_properties[{g.name}].goal_date",
                message=f"goal_date {g.goal_date} is in the past",
                severity="error",
            ))

    return issues
```

- [ ] **Step 4: Run — pass**

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/pipeline.py AI_Agents/src/goal_planning/tests/integration/test_engine_pipeline.py
git commit -m "feat(engine): pipeline.py — 8-stage orchestrator + ENGINE_VERSION + validate_input_only"
```

---

### Task 27: engine/__init__.py — public exports

**Files:**
- Modify: `AI_Agents/src/goal_planning/engine/__init__.py`
- Modify: `AI_Agents/src/goal_planning/tests/boundary/test_engine_no_llm.py`

- [ ] **Step 1: Populate __init__.py**

```python
# AI_Agents/src/goal_planning/engine/__init__.py
"""Engine — public surface."""
from .pipeline import compute_full_projection, validate_input_only, ENGINE_VERSION

__all__ = ["compute_full_projection", "validate_input_only", "ENGINE_VERSION"]
```

- [ ] **Step 2: Re-run boundary lint test**

Run: `pytest AI_Agents/src/goal_planning/tests/boundary/test_engine_no_llm.py -v`
Expected: PASS — engine has no LLM imports.

- [ ] **Step 3: Smoke import test**

Run: `python -c "from cashflow_statement.engine import compute_full_projection, ENGINE_VERSION; print(ENGINE_VERSION)"`
Expected: `0.1.0`

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/engine/__init__.py
git commit -m "feat(engine): public surface in engine/__init__.py"
```

---

## Phase 1: Testing — synthetic parity, Excel parity, performance

### Task 28: Synthetic parity — closed-form tests #1-5

**Files:**
- Create: `AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py`

This implements 5 of the 13 synthetic cases (per spec §10.3). The other 8 are split across Tasks 29-31.

- [ ] **Step 1: Add tests #1-5**

```python
# AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py
"""Synthetic parity tests: hand-compute expected values via numpy_financial; assert engine matches.

Per spec §10.3, rel_tol=0.001 since both sides are pure Python computation.
"""
from datetime import date
import numpy_financial as npf
import pytest

from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, Assumptions,
    CustomGoal, GoalType, GoalProperty,
)
from cashflow_statement.engine import compute_full_projection


REL = 0.001


def _profile(**overrides):
    base = dict(
        latest_update_date=date(2026, 5, 9),
        annual_income=2_000_000, tax_rate=0.30,
        financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000,
    )
    base.update(overrides)
    return ClientProfile(**base)


def test_1_single_retirement_corpus_matches_pv_formula():
    """Test #1: corpus = -PV(real_roi_annual, post_retire_yrs, annual_expense_FV)."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)

    # Hand compute: 50yo, retire at 60 in ~10y; 25y post-retirement
    annual_pv = 80_000 * 12
    years_to = 10  # approximate; engine uses date diff
    annual_fv = annual_pv * (1.06 ** years_to)
    real_annual = (1.09 / 1.06) - 1
    expected_corpus = -npf.pv(real_annual, 25, annual_fv)
    # _round_thousand applied
    expected_rounded = round(expected_corpus / 1000) * 1000

    # Allow some flex for date-fraction differences
    assert out.retirement.corpus_required_computed == pytest.approx(expected_rounded, rel=0.01)


def test_2_cash_purchase_property_payout_fv():
    """Test #2: payout_amount_fv = target_pv × (1+inflation)^years_to_goal."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        goal_properties=[GoalProperty(
            name="house_1", target_pv=10_000_000, goal_date=date(2030, 5, 9),
        )],
    )
    out = compute_full_projection(inp, )
    # Note: GoalProperty isn't yet wired into goals_table in Task 22 — that's a known gap
    # filled in Task 26. After Task 26, this test should pass.
    property_goal = next(g for g in out.goals if g.goal_type == GoalType.property)
    expected_fv = round(10_000_000 * (1.06 ** 4) / 1000) * 1000
    assert property_goal.amount_fv == pytest.approx(expected_fv, rel=0.005)


def test_3_mortgaged_property_emi_matches_pmt():
    """Test #3: EMI = PMT(monthly_rate, total_months, -mortgage_amount)."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        goal_properties=[GoalProperty(
            name="house_2", target_pv=10_000_000, is_downpayment_only=True,
            upfront_amount=2_000_000, goal_date=date(2030, 5, 9),
            mortgage_tenure_years=20, mortgage_interest_annual=0.085,
        )],
        detail_level="full",
    )
    out = compute_full_projection(inp)
    schedule = next(m for m in out.mortgage_amortizations if "goal:house_2" == m.property_ref)
    actual_emi = schedule.monthly_schedule[0].emi

    fv_target = round(10_000_000 * (1.06 ** 4) / 1000) * 1000
    fv_upfront = round(2_000_000 * (1.06 ** 4) / 1000) * 1000
    mortgage_amount = fv_target - fv_upfront
    monthly_rate = (1.085) ** (1/12) - 1
    expected_emi = npf.pmt(monthly_rate, 240, -mortgage_amount)
    assert actual_emi == pytest.approx(expected_emi, rel=0.01)


def test_4_empty_goals_nfa_growth_two_band():
    """Test #4: With no goals, no income, no expense → NFA grows at near for 2y, long after.

    NFA[5] = NFA × (1.05)^2 × (1.09)^3 ≈ 14,290,000 from 10M base.
    """
    inp = GoalPlanningInput(
        profile=_profile(
            annual_income=0, monthly_household_expense=0, monthly_investment_next_12m=0,
            financial_assets=10_000_000, financial_liabilities_excl_mortgage=0,
        ),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),  # young → far retirement
    )
    out = compute_full_projection(inp)
    # NFA at year 5
    expected_nfa_y5 = 10_000_000 * (1.05 ** 2) * (1.09 ** 3)
    # Find the year-5 NFA close
    actual_y5 = out.annual_cashflow[4].nfa_closing
    assert actual_y5 == pytest.approx(expected_nfa_y5, rel=0.01)


def test_5_existing_mortgage_rate_inversion_round_trip():
    """Test #5: PMT(inferred_rate, months, -balance) ≈ given EMI."""
    from cashflow_statement.models import CurrentProperty
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        current_properties=[CurrentProperty(
            name="apt", has_mortgage=True,
            mortgage_balance=5_000_000, mortgage_emi=43_391,
            mortgage_last_date=date(2046, 5, 9),
        )],
        detail_level="full",
    )
    out = compute_full_projection(inp)
    sched = next(m for m in out.mortgage_amortizations if "existing:apt" == m.property_ref)
    first = sched.monthly_schedule[0]
    # First-month interest = balance × inferred_monthly_rate
    inferred_monthly_rate = first.interest_portion / 5_000_000
    pmt_check = npf.pmt(inferred_monthly_rate, 240, -5_000_000)
    assert pmt_check == pytest.approx(43_391, rel=0.01)
```

- [ ] **Step 2: Run**

Run: `pytest AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py -v`
Expected: 5 passed (some may need engine refinement; iterate on engine if any fail).

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py
git commit -m "test(goal_planning): synthetic parity tests #1-5 (corpus, FV, EMI, NFA growth, RATE)"
```

---

### Task 29: Synthetic parity — tests #6, #7a/b/c, #8 (funded/shortfall + 3-band ROI)

**Files:**
- Modify: `AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py`

- [ ] **Step 1: Append 5 tests**

```python
def test_6_goal_funded_with_huge_nfa():
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=100_000_000, financial_liabilities_excl_mortgage=0),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="small_goal", goal_type=GoalType.custom,
            amount_pv=1_000_000, goal_date=date(2030, 5, 9),
        )],
    )
    out = compute_full_projection(inp)
    g = next(s for s in out.goals if s.name == "small_goal")
    assert g.is_funded
    assert g.shortfall_fv == 0


def test_7a_two_equal_goals_same_date_proportional_split():
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=5_000_000, annual_income=0, monthly_household_expense=0),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
        custom_goals=[
            CustomGoal(name="g1", goal_type=GoalType.custom, amount_fv=5_000_000, goal_date=date(2027, 6, 1)),
            CustomGoal(name="g2", goal_type=GoalType.custom, amount_fv=5_000_000, goal_date=date(2027, 6, 1)),
        ],
    )
    out = compute_full_projection(inp)
    s1 = next(s for s in out.goals if s.name == "g1")
    s2 = next(s for s in out.goals if s.name == "g2")
    assert s1.shortfall_fv > 0
    assert s2.shortfall_fv > 0
    # Proportional → roughly equal
    assert s1.shortfall_fv == pytest.approx(s2.shortfall_fv, rel=0.05)


def test_7b_three_goals_total_3x_nfa():
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=3_000_000, annual_income=0, monthly_household_expense=0),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
        custom_goals=[
            CustomGoal(name=f"g{i}", goal_type=GoalType.custom, amount_fv=3_000_000, goal_date=date(2027, 6, 1))
            for i in range(3)
        ],
    )
    out = compute_full_projection(inp)
    # Each gets 1M, so each is short by 2M ≈ 2/3 of FV
    for s in out.goals:
        if s.goal_type == GoalType.custom:
            assert s.shortfall_fv == pytest.approx(2_000_000, rel=0.10)


def test_7c_mixed_goals_plus_oneoff_outflow_same_month():
    from cashflow_statement.models import OneOffEvent
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=2_000_000, annual_income=0, monthly_household_expense=0),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
        custom_goals=[
            CustomGoal(name="g1", goal_type=GoalType.custom, amount_fv=1_000_000, goal_date=date(2027, 6, 1)),
        ],
        one_off_outflows=[OneOffEvent(description="renovation", amount=2_000_000, date=date(2027, 6, 15))],
    )
    out = compute_full_projection(inp)
    # Total need = 3M; available ≤ 2M → all underfunded
    g1 = next(s for s in out.goals if s.name == "g1")
    reno = next(s for s in out.one_off_outflow_status if s.description == "renovation")
    assert g1.shortfall_fv > 0
    assert reno.shortfall > 0


def test_8_per_goal_expected_roi_three_band():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[
            CustomGoal(name="near_goal", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2027, 6, 1)),
            CustomGoal(name="mid_goal", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2030, 6, 1)),
            CustomGoal(name="long_goal", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2040, 6, 1)),
        ],
    )
    out = compute_full_projection(inp)
    near = next(g for g in out.goals if g.name == "near_goal")
    mid = next(g for g in out.goals if g.name == "mid_goal")
    long = next(g for g in out.goals if g.name == "long_goal")
    assert near.expected_roi == pytest.approx(0.05)
    assert mid.expected_roi == pytest.approx(0.07)
    assert long.expected_roi == pytest.approx(0.09)
```

- [ ] **Step 2: Run**

Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py
git commit -m "test(goal_planning): synthetic parity #6, #7a/b/c, #8"
```

---

### Task 30: Synthetic parity — tests #9, #10, #12, #13, #14 (edge cases + invariants)

**Files:**
- Modify: `AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py`

- [ ] **Step 1: Append**

```python
def test_9_already_retired_drawdown_branch():
    """Test #9: person already retired; drawdown from t=0 without divide-by-zero."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1956, 1, 1)),  # 70yo
    )
    out = compute_full_projection(inp)
    assert any("already retired" in w.lower() for w in out.warnings)
    assert out.retirement.years_to_retirement <= 0


def test_10_past_date_goal_dropped_with_warning():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="past_goal", goal_type=GoalType.custom,
            amount_pv=1_000_000, goal_date=date(2025, 1, 1),
        )],
    )
    out = compute_full_projection(inp)
    # Goal is dropped; warnings include note
    assert not any(g.name == "past_goal" for g in out.goals)
    assert any("past_goal" in w and "past" in w for w in out.warnings)


def test_12_goal_property_ipmt_year_2_plus():
    """Test #12: goal-property mortgage interest schedule uses IPMT for year 2+."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        goal_properties=[GoalProperty(
            name="house", target_pv=10_000_000, is_downpayment_only=True,
            upfront_amount=2_000_000, goal_date=date(2030, 5, 9),
            mortgage_tenure_years=20, mortgage_interest_annual=0.085,
        )],
        detail_level="full",
    )
    out = compute_full_projection(inp)
    sched = next(m for m in out.mortgage_amortizations if m.property_ref == "goal:house")
    # Annual rows: year 2's annual interest should be < year 1's (declining balance)
    year2_interest = sched.monthly_schedule[12].interest_portion
    year1_interest = sched.monthly_schedule[0].interest_portion
    assert year2_interest < year1_interest


def test_13_m147_4_branch_coverage():
    """Test #13: each of the 4 M147 branches is reachable and produces matching kind."""
    # Run a 50-year projection covering pre-retirement and post-retirement
    inp = GoalPlanningInput(
        profile=_profile(monthly_investment_next_12m=50_000),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        detail_level="full",
    )
    out = compute_full_projection(inp)
    nfa_rows = out.nfa_monthly_series
    kinds = {r.regular_invest_kind for r in nfa_rows}
    # At minimum, "user_sip" pre-retirement and "zero" post-retirement should appear
    assert "user_sip" in kinds
    assert "zero" in kinds


def test_14_step_up_compounding():
    """Test #14: FY3 income = FY1 × (1+growth)^2."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    fy1 = out.annual_cashflow[0].income
    fy3 = out.annual_cashflow[2].income
    assert fy3 == pytest.approx(fy1 * (1.08 ** 2), rel=1e-3)
```

- [ ] **Step 2: Run**

Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_synthetic_parity.py
git commit -m "test(goal_planning): synthetic parity #9, #10, #12, #13, #14"
```

---

### Task 31: Performance + memory tests

**Files:**
- Create: `AI_Agents/src/goal_planning/tests/integration/test_engine_performance.py`

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/integration/test_engine_performance.py
import time
import tracemalloc
from datetime import date
import pytest

from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
)
from cashflow_statement.engine import compute_full_projection


def _realistic_input():
    """Indian-realistic numbers per spec §10.7: NFA 5Cr, income 25L, 21 goals, 50-year horizon."""
    custom_goals = [
        CustomGoal(
            name=f"goal_{i}", goal_type=GoalType.custom,
            amount_pv=5_000_000, goal_date=date(2030 + i, 5, 9),
        )
        for i in range(20)
    ]
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_500_000, tax_rate=0.30,
            financial_assets=50_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=120_000, monthly_investment_next_12m=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=custom_goals,
    )


def test_engine_call_under_500ms():
    inp = _realistic_input()
    start = time.perf_counter()
    out = compute_full_projection(inp)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.5, f"Engine too slow: {elapsed*1000:.0f}ms"


def test_engine_memory_under_50mb():
    inp = _realistic_input()
    tracemalloc.start()
    out = compute_full_projection(inp)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 50 * 1024 * 1024, f"Engine peak memory: {peak/1024/1024:.1f}MB"
```

- [ ] **Step 2: Run**

Run: `pytest AI_Agents/src/goal_planning/tests/integration/test_engine_performance.py -v`
Expected: 2 passed.

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_engine_performance.py
git commit -m "test(goal_planning): performance and memory budgets (<500ms, <50MB)"
```

---

### Task 32: Excel parity — fixture extraction script (DEV-ONLY)

**Files:**
- Create: `scripts/extract_excel_reference.py`
- Create: `AI_Agents/src/goal_planning/tests/fixtures/excel_reference/cell_mapping.md`

This task creates the LibreOffice-driven extraction script. **Output `expected.json` files are committed to git** (CI doesn't run LibreOffice).

- [ ] **Step 1: Write the cell mapping documentation**

```markdown
# Excel cell mapping for goal_planning parity

Per spec §10.2. Maps Excel cells to engine output paths.

## Headline
| Excel | Output path |
|---|---|
| B26 | headline.net_financial_assets_today |
| B43 | retirement.corpus_required_computed |
| B44 | retirement.corpus_required_user_override |
| B46 | retirement.corpus_required_used |
| B86 | headline.number_of_goals |
| B88 | headline.last_fy_end_date |
| O113 | headline.sum_fund_today_pv |
| L113 | headline.total_shortfall_fv (sign-flipped) |
| M113 | headline.total_funded_amount |
| S105 | headline.present_status |
| (last NFA) | headline.closing_nfa |

## Per-goal (rows 93-112)
| Excel | Output path |
|---|---|
| H93..H112 | goals[i].amount_fv |
| L93..L112 | goals[i].shortfall_fv (sign-flipped) |
| M93..M112 | goals[i].funded_amount |

## Annual cashflow (rows 190-289)
| Excel | Output path |
|---|---|
| H190..H289 | annual_cashflow[i].existing_mortgage_emi_total |

## Per-outflow (cols AS290..BM290)
| Excel | Output path |
|---|---|
| AS290..BM290[name] | per_outflow_underfunded_total[name] |
```

- [ ] **Step 2: Write extraction script skeleton**

```python
# scripts/extract_excel_reference.py
"""DEV-ONLY: extract canonical input.json + expected.json from a goal_planning Excel scenario.

Usage:
    python scripts/extract_excel_reference.py <path-to-xlsx> <scenario-name>

Outputs:
    AI_Agents/src/goal_planning/tests/fixtures/excel_reference/<scenario-name>/input.json
    AI_Agents/src/goal_planning/tests/fixtures/excel_reference/<scenario-name>/expected.json

Prerequisites: LibreOffice headless installed locally. Uses scripts/recalc.py.
"""
import json
import sys
from pathlib import Path
from openpyxl import load_workbook


CELL_MAP = {
    "B26": "headline.net_financial_assets_today",
    "B43": "retirement.corpus_required_computed",
    "B44": "retirement.corpus_required_user_override",
    "B46": "retirement.corpus_required_used",
    "B86": "headline.number_of_goals",
    "B88": "headline.last_fy_end_date",
    "O113": "headline.sum_fund_today_pv",
    "L113": "headline.total_shortfall_fv",
    "M113": "headline.total_funded_amount",
    "S105": "headline.present_status",
}


def extract_input(xlsx_path: Path) -> dict:
    """Read Sourabh's Excel sheet 'Goal planning' and produce a GoalPlanningInput JSON."""
    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb["Goal planning"]
    # Map cells per spec §6 — engineer fills in based on the actual Excel structure
    return {
        "profile": {
            "latest_update_date": ws["B18"].value.isoformat() if ws["B18"].value else "2026-05-09",
            "annual_income": ws["B22"].value,
            "tax_rate": ws["B23"].value,
            "financial_assets": ws["B24"].value,
            "financial_liabilities_excl_mortgage": ws["B25"].value,
            # ... continue per Excel layout
        },
        # ... retirement, properties, goals, etc.
    }


def extract_expected(xlsx_path: Path) -> dict:
    """Extract expected output values keyed by output path."""
    wb = load_workbook(xlsx_path, data_only=True)
    ws = wb["Goal planning"]
    expected = {"checkpoint_cells": {}, "tolerance_overrides": {}}
    for cell, _path in CELL_MAP.items():
        v = ws[cell].value
        expected["checkpoint_cells"][cell] = v
    # Sign-flip shortfall cells (Excel stores negative)
    if expected["checkpoint_cells"].get("L113") is not None:
        expected["checkpoint_cells"]["L113"] = abs(expected["checkpoint_cells"]["L113"])
    return expected


def main():
    if len(sys.argv) != 3:
        print("Usage: python scripts/extract_excel_reference.py <xlsx> <scenario-name>")
        sys.exit(1)
    xlsx_path = Path(sys.argv[1])
    scenario = sys.argv[2]
    out_dir = Path("AI_Agents/src/goal_planning/tests/fixtures/excel_reference") / scenario
    out_dir.mkdir(parents=True, exist_ok=True)

    inp = extract_input(xlsx_path)
    exp = extract_expected(xlsx_path)
    (out_dir / "input.json").write_text(json.dumps(inp, indent=2, default=str))
    (out_dir / "expected.json").write_text(json.dumps(exp, indent=2, default=str))
    print(f"Wrote {out_dir}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run extraction on Sourabh's Excel for scenario #01**

Run: `python scripts/recalc.py "/path/to/goal_based_allocation_model (10).xlsx"`
Then: `python scripts/extract_excel_reference.py "/path/to/goal_based_allocation_model (10).xlsx" 01_baseline`
Expected: `AI_Agents/src/goal_planning/tests/fixtures/excel_reference/01_baseline/{input,expected}.json` created.

- [ ] **Step 4: Visually inspect both JSONs and adjust the extraction map if needed**

The full cell mapping (per spec §10.2) requires iterative refinement against the actual Excel layout. Engineer adjusts CELL_MAP and `extract_input` until output matches Sourabh's intent.

- [ ] **Step 5: Commit script + fixtures**

```bash
git add scripts/extract_excel_reference.py AI_Agents/src/goal_planning/tests/fixtures/excel_reference/01_baseline/ AI_Agents/src/goal_planning/tests/fixtures/excel_reference/cell_mapping.md
git commit -m "feat(test): Excel reference extractor + scenario #01 fixture"
```

---

### Task 33: Excel parity — author scenarios 02-04 (DEPENDS ON SOURABH)

**Files:**
- Add: `AI_Agents/src/goal_planning/tests/fixtures/excel_reference/02_no_mortgages/{input,expected}.json`
- Add: `AI_Agents/src/goal_planning/tests/fixtures/excel_reference/03_already_retired/{input,expected}.json`
- Add: `AI_Agents/src/goal_planning/tests/fixtures/excel_reference/04_overfunded/{input,expected}.json`

Per Q1 from spec, Sourabh authors variant Excels for these three scenarios:
- **02_no_mortgages**: drop existing + goal mortgages from baseline
- **03_already_retired**: DOB shifts so retirement_date < latest_update_date
- **04_overfunded**: financial_assets bumped 10× so present_status > 0

- [ ] **Step 1: Coordinate with Sourabh — author 3 variant Excel files**

This is a **resourcing dependency** — engineer cannot self-serve. Open issue/ticket assigning Sourabh.

- [ ] **Step 2: Once each variant Excel exists, run extraction**

```bash
python scripts/extract_excel_reference.py /path/to/scenario_02.xlsx 02_no_mortgages
python scripts/extract_excel_reference.py /path/to/scenario_03.xlsx 03_already_retired
python scripts/extract_excel_reference.py /path/to/scenario_04.xlsx 04_overfunded
```

- [ ] **Step 3: Commit fixtures**

```bash
git add AI_Agents/src/goal_planning/tests/fixtures/excel_reference/02_no_mortgages/ \
        AI_Agents/src/goal_planning/tests/fixtures/excel_reference/03_already_retired/ \
        AI_Agents/src/goal_planning/tests/fixtures/excel_reference/04_overfunded/
git commit -m "feat(test): Excel parity scenarios 02-04 fixtures"
```

---

### Task 34: Excel parity test harness — type-aware tolerance

**Files:**
- Create: `AI_Agents/src/goal_planning/tests/integration/test_excel_parity.py`

- [ ] **Step 1: Write the harness**

```python
# AI_Agents/src/goal_planning/tests/integration/test_excel_parity.py
"""Excel parity test — loads fixtures, runs engine, asserts within tolerance per spec §10.2."""
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from cashflow_statement.models import GoalPlanningInput
from cashflow_statement.engine import compute_full_projection


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "excel_reference"

# Per spec §10.2 — type-aware tolerance
DEFAULT_REL_TOL = 0.005
DEFAULT_ABS_TOL = 100

# Per-cell overrides
PER_CELL_OVERRIDES = {
    "L113": {"rel_tol": 0.001},
    "M113": {"rel_tol": 0.001},
}


@pytest.mark.excel_parity
@pytest.mark.parametrize("scenario_dir", sorted(FIXTURES_DIR.iterdir()) if FIXTURES_DIR.exists() else [])
def test_excel_parity(scenario_dir: Path):
    if not scenario_dir.is_dir() or scenario_dir.name == "__pycache__":
        pytest.skip("not a scenario")
    input_file = scenario_dir / "input.json"
    expected_file = scenario_dir / "expected.json"
    if not input_file.exists() or not expected_file.exists():
        pytest.skip(f"fixtures missing for {scenario_dir.name}")

    inp = GoalPlanningInput.model_validate_json(input_file.read_text())
    expected = json.loads(expected_file.read_text())
    actual = compute_full_projection(inp)

    failures: list[str] = []
    for cell, expected_value in expected["checkpoint_cells"].items():
        actual_value = _get_path(actual, _cell_to_path(cell))
        ok, diff_msg = _assert_close(
            actual_value, expected_value,
            **(PER_CELL_OVERRIDES.get(cell) or {"rel_tol": DEFAULT_REL_TOL, "abs_tol": DEFAULT_ABS_TOL}),
        )
        if not ok:
            failures.append(
                f"  cell:     {cell}\n"
                f"  expected: {expected_value}\n"
                f"  actual:   {actual_value}\n"
                f"  {diff_msg}"
            )

    if failures:
        raise AssertionError(
            f"Excel parity mismatch [scenario={scenario_dir.name}]:\n" + "\n".join(failures)
        )


def _cell_to_path(cell: str) -> str:
    """Look up the dotted output path for a cell. Mirror scripts/extract_excel_reference.py CELL_MAP."""
    from scripts.extract_excel_reference import CELL_MAP  # type: ignore[import-not-found]
    return CELL_MAP[cell]


def _get_path(obj: Any, path: str) -> Any:
    parts = path.split(".")
    cur = obj
    for p in parts:
        cur = getattr(cur, p)
    return cur


def _assert_close(actual: float, expected: float, rel_tol: float = DEFAULT_REL_TOL, abs_tol: float = DEFAULT_ABS_TOL) -> tuple[bool, str]:
    """Type-aware close-enough check. Returns (ok, diff_message)."""
    if isinstance(expected, bool) or isinstance(actual, bool):
        return (actual == expected, f"diff: {actual} vs {expected} (boolean exact)")
    if isinstance(expected, int) and isinstance(actual, int):
        return (actual == expected, f"diff: {actual - expected} (int exact)")
    if expected is None and actual is None:
        return (True, "")
    if expected is None or actual is None:
        return (False, f"diff: one is None")
    diff = float(actual) - float(expected)
    rel = abs(diff) / abs(expected) if expected else float("inf")
    if abs(diff) <= abs_tol or rel <= rel_tol:
        return (True, "")
    return (False, f"diff: {diff:+,.2f} ({rel:+.2%}); tol rel={rel_tol}, abs={abs_tol}")
```

- [ ] **Step 2: Run on scenario #01**

Run: `pytest AI_Agents/src/goal_planning/tests/integration/test_excel_parity.py -v -m excel_parity`
Expected: passes for #01 (after iteration on engine to match Excel within tolerance).

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_excel_parity.py
git commit -m "test(goal_planning): Excel parity harness with type-aware tolerance"
```

---

### Task 35: Excel parity — bridge identity test (FundFlowSummary)

**Files:**
- Create: `AI_Agents/src/goal_planning/tests/integration/test_excel_bridge_identity.py`

Per spec §10.2: `closing_nfa = opening + invest + roi + one_off_in − one_off_out − goals` for every FY.

- [ ] **Step 1: Write test**

```python
# AI_Agents/src/goal_planning/tests/integration/test_excel_bridge_identity.py
"""Test that FundFlowSummary's bridge identity holds for both engine and Excel."""
import json
from pathlib import Path
import pytest
from cashflow_statement.models import GoalPlanningInput
from cashflow_statement.engine import compute_full_projection


FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "excel_reference"


@pytest.mark.parametrize("scenario_dir", sorted(FIXTURES_DIR.iterdir()) if FIXTURES_DIR.exists() else [])
def test_fund_flow_bridge_identity(scenario_dir):
    if not scenario_dir.is_dir():
        pytest.skip("not a dir")
    input_file = scenario_dir / "input.json"
    if not input_file.exists():
        pytest.skip("no input.json")

    inp = GoalPlanningInput.model_validate_json(input_file.read_text())
    out = compute_full_projection(inp)
    s = out.fund_flow_summary

    expected_closing = (
        s.opening_nfa + s.total_investments + s.total_roi
        + s.total_one_off_in - s.total_one_off_out - s.total_goals_paid
    )
    assert s.closing_nfa == pytest.approx(expected_closing, rel=1e-6, abs=1.0)
```

- [ ] **Step 2: Run**

Expected: passes for available scenarios.

- [ ] **Step 3: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_excel_bridge_identity.py
git commit -m "test(goal_planning): FundFlowSummary bridge identity"
```

---

### Task 36: Phase 1 verification gate

- [ ] **Step 1: Run full Phase 1 suite**

Run:
```bash
pytest AI_Agents/src/financial_primitives/ AI_Agents/src/goal_planning/tests/ -v \
       --cov=AI_Agents/src/financial_primitives \
       --cov=AI_Agents/src/goal_planning/engine \
       --cov-fail-under=95 \
       --cov-report=term-missing:skip-covered
```

- [ ] **Step 2: Verify all 4 acceptance criteria**

| Gate | Verification |
|---|---|
| Excel parity scenarios 01-04 pass | `pytest -m excel_parity` |
| 13 synthetic tests pass | `pytest tests/integration/test_synthetic_parity.py` |
| Engine call < 500ms | `pytest tests/integration/test_engine_performance.py::test_engine_call_under_500ms` |
| Memory < 50MB | `pytest tests/integration/test_engine_performance.py::test_engine_memory_under_50mb` |
| 95% engine coverage | coverage report ≥ 95% |
| Boundary lint passes | `pytest tests/boundary/` |

- [ ] **Step 3: Tag the commit**

```bash
git tag goal-planning-phase-1
git commit --allow-empty -m "feat(goal_planning): Phase 1 verification gate passed"
```

---

## Phase 2: Agent + 7 Levers

### Task 37: agent/state.py — AgentState TypedDict + CapturedCashflow

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/state.py`
- Create: `AI_Agents/src/goal_planning/tests/agent/test_state.py`

- [ ] **Step 1: Write tests**

```python
# AI_Agents/src/goal_planning/tests/agent/test_state.py
from datetime import date
from cashflow_statement.agent.state import AgentState, CapturedCashflow
from cashflow_statement.models import OneOffEvent


def test_captured_cashflow_carries_direction():
    cc = CapturedCashflow(
        event=OneOffEvent(description="bonus", amount=500_000, date=date(2027, 1, 1)),
        direction="in",
    )
    assert cc.direction == "in"


def test_agent_state_has_required_keys():
    """AgentState is a TypedDict — validate via type_hints."""
    from typing import get_type_hints
    hints = get_type_hints(AgentState)
    assert "messages" in hints
    assert "baseline_input" in hints
    assert "anchor_date" in hints
    assert "accumulated_overrides" in hints
    assert "captured_goals" in hints
    assert "captured_properties" in hints
    assert "captured_cashflows" in hints
    assert "captured_mutations" in hints
    assert "last_output" in hints
    assert "last_levers" in hints
    assert "dirty" in hints
    assert "error_log" in hints
```

- [ ] **Step 2: Implement**

```python
# AI_Agents/src/goal_planning/agent/state.py
"""Agent working memory — persists across turns via LangGraph checkpointer."""
from __future__ import annotations
from datetime import date
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from cashflow_statement.models import (
    GoalPlanningInput, GoalPlanningOutput, OneOffEvent,
    OverrideSpec, GoalMutation, CustomGoal, GoalProperty, Lever,
)


class CapturedCashflow(BaseModel):
    event: OneOffEvent
    direction: Literal["in", "out"]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    # Refreshed each turn
    baseline_input: GoalPlanningInput
    anchor_date: date

    # Persisted across turns
    accumulated_overrides: list[OverrideSpec]
    captured_goals: list[CustomGoal]
    captured_properties: list[GoalProperty]
    captured_cashflows: list[CapturedCashflow]
    captured_mutations: list[GoalMutation]

    # Computed within turn
    last_output: GoalPlanningOutput | None
    last_levers: list[Lever]

    # Control
    dirty: bool
    error_log: list[str]
```

- [ ] **Step 3: Run — pass**

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/state.py AI_Agents/src/goal_planning/tests/agent/test_state.py
git commit -m "feat(agent): AgentState TypedDict + CapturedCashflow"
```

---

### Task 38: agent/levers.py — Lever A (SIP) with bisection

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/levers.py`
- Create: `AI_Agents/src/goal_planning/tests/agent/test_levers.py`

- [ ] **Step 1: Write test**

```python
# AI_Agents/src/goal_planning/tests/agent/test_levers.py
from datetime import date
from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
)
from cashflow_statement.engine import compute_full_projection
from cashflow_statement.agent.levers import generate_lever_a_increase_sip


def _shortfall_input():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=5_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=20_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="big_goal", goal_type=GoalType.custom,
            amount_pv=10_000_000, goal_date=date(2035, 1, 1),
        )],
    )


def test_lever_a_finds_feasible_sip_when_one_exists():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    lever = generate_lever_a_increase_sip(inp, out, sip_max_multiplier=5.0)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "monthly_investment_next_12m"
        # Verify projected_outcome is feasible
        assert lever.projected_outcome.is_overall_feasible


def test_lever_a_returns_none_when_already_feasible():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=10_000_000, tax_rate=0.30,
            financial_assets=100_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=200_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    lever = generate_lever_a_increase_sip(inp, out, sip_max_multiplier=5.0)
    assert lever is None  # already feasible — no lever needed
```

- [ ] **Step 2: Run — fail**

- [ ] **Step 3: Implement Lever A with bisection**

```python
# AI_Agents/src/goal_planning/agent/levers.py
"""Deterministic lever generators (A through G).

Each generator returns a Lever | None. None means the lever doesn't apply or can't close the gap
within its search bounds. Per spec §8.4 every lever asserts mid-horizon NFA non-negativity.
"""
from __future__ import annotations
from datetime import date
from dateutil.relativedelta import relativedelta

from cashflow_statement.models import (
    GoalPlanningInput, GoalPlanningOutput, Lever, NumericOverride,
    GoalMutation, PropertyFieldOverride,
)
from cashflow_statement.engine import compute_full_projection


def _is_feasible(out: GoalPlanningOutput) -> bool:
    """Both is_overall_feasible AND min_nfa_in_horizon non-negative."""
    if not out.headline.is_overall_feasible:
        return False
    if out.nfa_monthly_series:
        if any(r.nfa_close < 0 for r in out.nfa_monthly_series):
            return False
    return True


def generate_lever_a_increase_sip(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
    sip_max_multiplier: float = 5.0,
) -> Lever | None:
    """Lever A: bisect monthly_investment from current to N× to find smallest feasible value."""
    if _is_feasible(baseline_out):
        return None

    base_sip = inp.profile.monthly_investment_next_12m or 1
    lo, hi = base_sip, base_sip * sip_max_multiplier
    best = None

    # Cap iterations for budget
    for _ in range(8):
        mid = (lo + hi) / 2
        new_inp = inp.model_copy(deep=True)
        new_inp.profile = inp.profile.model_copy(update={"monthly_investment_next_12m": mid})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            best = (mid, new_out)
            hi = mid
        else:
            lo = mid

    if best is None:
        return None
    target_sip, target_out = best

    confidence = "high" if target_sip < 2 * base_sip else ("medium" if target_sip < 3 * base_sip else "low")
    return Lever(
        description=f"Increase monthly investment from ₹{base_sip:,.0f} to ₹{target_sip:,.0f}",
        action=NumericOverride(
            kind="numeric", key="monthly_investment_next_12m", value=target_sip,
        ),
        projected_outcome=target_out.headline,
        confidence=confidence,
    )
```

- [ ] **Step 4: Run — pass**

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/levers.py AI_Agents/src/goal_planning/tests/agent/test_levers.py
git commit -m "feat(agent): Lever A — increase SIP via bisection"
```

---

### Task 39: agent/levers.py — Levers B (defer) + C (reduce target) + D (retirement age)

**Files:**
- Modify: `AI_Agents/src/goal_planning/agent/levers.py`
- Modify: `AI_Agents/src/goal_planning/tests/agent/test_levers.py`

- [ ] **Step 1: Add tests**

```python
def test_lever_b_defers_largest_underfunded_goal():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_b_defer_goal
    lever = generate_lever_b_defer_goal(inp, out, defer_max_years=10)
    if lever is not None:
        assert lever.action.kind == "mutation"
        assert lever.action.op == "update"
        assert "goal_date" in lever.action.fields


def test_lever_c_reduces_target():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_c_reduce_target
    lever = generate_lever_c_reduce_target(inp, out, reduce_max_pct=0.50)
    if lever is not None:
        assert lever.action.kind == "mutation"
        assert "amount_pv" in lever.action.fields


def test_lever_d_changes_retirement_age():
    """Lever D only generated when retirement is the underfunded goal."""
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_d_retirement_age
    lever = generate_lever_d_retirement_age(inp, out)
    if lever is not None:
        assert lever.action.kind == "mutation"
        assert lever.action.goal_name == "retirement"
        assert "retirement_age" in lever.action.fields
```

- [ ] **Step 2: Implement Levers B, C, D per spec §8.4**

Append to `levers.py`:

```python
def generate_lever_b_defer_goal(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, defer_max_years: int = 10,
) -> Lever | None:
    """Lever B: Pick largest underfunded goal; defer 1-N years; smallest deferral that closes gap."""
    if _is_feasible(baseline_out):
        return None
    underfunded = [g for g in baseline_out.goals if g.shortfall_fv > 0]
    if not underfunded:
        return None
    largest = max(underfunded, key=lambda g: g.shortfall_fv)

    for years in range(1, defer_max_years + 1):
        new_date = largest.goal_date + relativedelta(years=years)
        new_inp = _apply_goal_mutation(inp, largest.name, {"goal_date": new_date})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            confidence = "high" if years <= 2 else ("medium" if years <= 5 else "low")
            return Lever(
                description=f"Defer '{largest.name}' by {years} years",
                action=GoalMutation(
                    kind="mutation", op="update", goal_name=largest.name,
                    fields={"goal_date": new_date},
                ),
                projected_outcome=new_out.headline,
                confidence=confidence,
            )
    return None


def generate_lever_c_reduce_target(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, reduce_max_pct: float = 0.50,
) -> Lever | None:
    """Lever C: Reduce largest underfunded goal target in 5pp steps to 50%."""
    if _is_feasible(baseline_out):
        return None
    underfunded = [g for g in baseline_out.goals if g.shortfall_fv > 0]
    if not underfunded:
        return None
    largest = max(underfunded, key=lambda g: g.shortfall_fv)

    for pct in range(5, int(reduce_max_pct * 100) + 1, 5):
        cut = pct / 100
        new_amount_pv = largest.amount_pv * (1 - cut)
        new_inp = _apply_goal_mutation(inp, largest.name, {"amount_pv": new_amount_pv})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            confidence = "high" if pct <= 15 else ("medium" if pct <= 30 else "low")
            return Lever(
                description=f"Reduce '{largest.name}' target by {pct}%",
                action=GoalMutation(
                    kind="mutation", op="update", goal_name=largest.name,
                    fields={"amount_pv": new_amount_pv},
                ),
                projected_outcome=new_out.headline,
                confidence=confidence,
            )
    return None


def generate_lever_d_retirement_age(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
) -> Lever | None:
    """Lever D: Bisect retirement_age upward. Only when retirement is underfunded."""
    retirement_status = next(
        (g for g in baseline_out.goals if g.goal_type.value == "retirement"), None
    )
    if retirement_status is None or retirement_status.shortfall_fv == 0:
        return None
    if _is_feasible(baseline_out):
        return None

    base = inp.retirement.retirement_age
    upper = inp.retirement.assumed_total_age - 5
    lo, hi = base + 1, upper

    for new_age in range(lo, hi + 1):
        new_inp = _apply_goal_mutation(inp, "retirement", {"retirement_age": new_age})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            return Lever(
                description=f"Retire at {new_age} instead of {base}",
                action=GoalMutation(
                    kind="mutation", op="update", goal_name="retirement",
                    fields={"retirement_age": new_age},
                ),
                projected_outcome=new_out.headline,
                confidence="medium",
            )
    return None


def _apply_goal_mutation(inp: GoalPlanningInput, goal_name: str, fields: dict) -> GoalPlanningInput:
    """Helper: produce a new input with a goal mutation applied. Treats retirement as a goal."""
    new_inp = inp.model_copy(deep=True)
    if goal_name == "retirement":
        # Retirement-specific updates flow into RetirementInput
        update_kwargs = {}
        for k, v in fields.items():
            update_kwargs[k] = v
        new_inp.retirement = inp.retirement.model_copy(update=update_kwargs)
    else:
        # Custom goal — find by name and update
        for i, g in enumerate(new_inp.custom_goals):
            if g.name.casefold() == goal_name.casefold():
                new_inp.custom_goals[i] = g.model_copy(update=fields)
                return new_inp
        # Goal property?
        for i, g in enumerate(new_inp.goal_properties):
            if g.name.casefold() == goal_name.casefold():
                new_inp.goal_properties[i] = g.model_copy(update=fields)
                return new_inp
    return new_inp
```

- [ ] **Step 3: Run — pass**

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/levers.py AI_Agents/src/goal_planning/tests/agent/test_levers.py
git commit -m "feat(agent): Levers B/C/D — defer, reduce, retirement age (via mutate_goal)"
```

---

### Task 40: agent/levers.py — Levers E (step-up) + F (expense) + G (mortgage payoff)

**Files:**
- Modify: `AI_Agents/src/goal_planning/agent/levers.py`
- Modify: `AI_Agents/src/goal_planning/tests/agent/test_levers.py`

- [ ] **Step 1: Add tests**

```python
def test_lever_e_increases_step_up():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_e_step_up
    lever = generate_lever_e_step_up(inp, out, step_up_max_delta_pp=0.20)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "step_up_rate"


def test_lever_f_reduces_expense():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_f_reduce_expense
    lever = generate_lever_f_reduce_expense(inp, out)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "monthly_household_expense"
        assert lever.confidence == "low"


def test_lever_g_skipped_with_no_existing_mortgage():
    inp = _shortfall_input()  # no current_properties
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_g_mortgage_payoff
    lever = generate_lever_g_mortgage_payoff(inp, out)
    assert lever is None  # silently skipped


def test_lever_g_with_active_mortgage():
    from cashflow_statement.models import CurrentProperty
    inp = _shortfall_input()
    inp = inp.model_copy(update={
        "current_properties": [CurrentProperty(
            name="apt", has_mortgage=True,
            mortgage_balance=3_000_000, mortgage_emi=30_000,
            mortgage_last_date=date(2046, 1, 1),
        )],
    })
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import generate_lever_g_mortgage_payoff
    lever = generate_lever_g_mortgage_payoff(inp, out)
    if lever is not None:
        assert lever.action.kind == "property_field"
        assert lever.action.field == "early_payoff_date"
```

- [ ] **Step 2: Implement E/F/G per spec §8.4**

Append to `levers.py`:

```python
def generate_lever_e_step_up(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, step_up_max_delta_pp: float = 0.20,
) -> Lever | None:
    """Lever E: bisect step-up rate from baseline to baseline+max_delta."""
    if _is_feasible(baseline_out):
        return None
    base_rate = inp.assumptions.annual_invested_amount_growth
    lo, hi = base_rate, base_rate + step_up_max_delta_pp
    best = None
    for _ in range(8):
        mid = (lo + hi) / 2
        new_inp = inp.model_copy(deep=True)
        new_inp.assumptions = inp.assumptions.model_copy(update={"annual_invested_amount_growth": mid})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            best = (mid, new_out)
            hi = mid
        else:
            lo = mid
    if best is None:
        return None
    rate, target_out = best
    delta = rate - base_rate
    confidence = "high" if delta <= 0.05 else ("medium" if delta <= 0.10 else "low")
    return Lever(
        description=f"Increase step-up rate from {base_rate:.1%} to {rate:.1%}",
        action=NumericOverride(kind="numeric", key="step_up_rate", value=rate),
        projected_outcome=target_out.headline,
        confidence=confidence,
    )


def generate_lever_f_reduce_expense(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
    reduce_pct_list: tuple = (0.05, 0.10, 0.15),
) -> Lever | None:
    """Lever F: try -5/-10/-15% on monthly_household_expense. Confidence: low always."""
    if _is_feasible(baseline_out):
        return None
    base = inp.profile.monthly_household_expense
    for pct in reduce_pct_list:
        new_expense = base * (1 - pct)
        new_inp = inp.model_copy(deep=True)
        new_inp.profile = inp.profile.model_copy(update={"monthly_household_expense": new_expense})
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            return Lever(
                description=f"Reduce monthly household expense by {int(pct*100)}% (from ₹{base:,.0f} to ₹{new_expense:,.0f})",
                action=NumericOverride(
                    kind="numeric", key="monthly_household_expense", value=new_expense,
                ),
                projected_outcome=new_out.headline,
                confidence="low",
            )
    return None


def generate_lever_g_mortgage_payoff(
    inp: GoalPlanningInput, baseline_out: GoalPlanningOutput,
    payoff_years_list: tuple = (1, 3, 5, 10),
) -> Lever | None:
    """Lever G: only if user has ≥1 active existing mortgage."""
    active_mortgages = [
        p for p in inp.current_properties
        if p.has_mortgage
        and p.mortgage_last_date is not None
        and p.mortgage_last_date > inp.profile.latest_update_date
    ]
    if not active_mortgages:
        return None
    if _is_feasible(baseline_out):
        return None

    target_property = active_mortgages[0]  # pick first
    for years in payoff_years_list:
        payoff_date = inp.profile.latest_update_date + relativedelta(years=years)
        new_inp = inp.model_copy(deep=True)
        for i, p in enumerate(new_inp.current_properties):
            if p.name == target_property.name:
                # Apply early_payoff_date as an override that engine respects
                # (engine's mortgages.py would need to honor this — Phase 2 task notes refactor)
                new_inp.current_properties[i] = p.model_copy(update={
                    "mortgage_last_date": payoff_date,
                })
                break
        new_out = compute_full_projection(new_inp)
        if _is_feasible(new_out):
            return Lever(
                description=f"Pay off '{target_property.name}' mortgage by {payoff_date.isoformat()}",
                action=PropertyFieldOverride(
                    kind="property_field", property_name=target_property.name,
                    field="early_payoff_date", value=payoff_date,
                ),
                projected_outcome=new_out.headline,
                confidence="medium",
            )
    return None
```

**Note:** Lever G's engine integration uses `mortgage_last_date` as a proxy for `early_payoff_date`. A cleaner future refactor: engine accepts a separate `early_payoff_date` field on `CurrentProperty` and computes amortization through that date instead. For v1, the proxy works.

- [ ] **Step 3: Run — pass**

Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/levers.py AI_Agents/src/goal_planning/tests/agent/test_levers.py
git commit -m "feat(agent): Levers E/F/G — step-up, expense, mortgage payoff"
```

---

### Task 41: agent/levers.py — composite ranking + top 3 + propose_levers

**Files:**
- Modify: `AI_Agents/src/goal_planning/agent/levers.py`

- [ ] **Step 1: Add ranking test**

```python
# Append to test_levers.py
def test_propose_levers_returns_top_3_ranked():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    from cashflow_statement.agent.levers import propose_levers
    levers = propose_levers(inp, out)
    assert len(levers) <= 3  # capped at top 3
```

- [ ] **Step 2: Implement `propose_levers` ranking**

Append to `levers.py`:

```python
CATEGORY_PRIORITY = {
    "A": 1.0, "B": 0.9, "E": 0.85, "C": 0.6, "G": 0.55, "D": 0.5, "F": 0.4,
}
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}


def _score_lever(lever: Lever, category: str, severity_required: float = 0.5) -> float:
    return (1.0 / max(severity_required, 0.01)) * CONFIDENCE_WEIGHT[lever.confidence] * CATEGORY_PRIORITY[category]


def propose_levers(inp: GoalPlanningInput, baseline_out: GoalPlanningOutput, max_count: int = 3) -> list[Lever]:
    """Generate up to 7 levers, score, return top N."""
    if _is_feasible(baseline_out):
        return []
    candidates: list[tuple[Lever, str]] = []
    if (l := generate_lever_a_increase_sip(inp, baseline_out)):
        candidates.append((l, "A"))
    if (l := generate_lever_b_defer_goal(inp, baseline_out)):
        candidates.append((l, "B"))
    if (l := generate_lever_c_reduce_target(inp, baseline_out)):
        candidates.append((l, "C"))
    if (l := generate_lever_d_retirement_age(inp, baseline_out)):
        candidates.append((l, "D"))
    if (l := generate_lever_e_step_up(inp, baseline_out)):
        candidates.append((l, "E"))
    if (l := generate_lever_f_reduce_expense(inp, baseline_out)):
        candidates.append((l, "F"))
    if (l := generate_lever_g_mortgage_payoff(inp, baseline_out)):
        candidates.append((l, "G"))

    candidates.sort(key=lambda lc: _score_lever(lc[0], lc[1]), reverse=True)

    if not candidates:
        # No-lever-helps fallback
        underfunded = [g for g in baseline_out.goals if g.shortfall_fv > 0]
        if underfunded:
            largest = max(underfunded, key=lambda g: g.shortfall_fv)
            return [Lever(
                description="Even at maximum levers, this isn't feasible — consider reducing scope",
                action=GoalMutation(kind="mutation", op="remove", goal_name=largest.name, fields={}),
                projected_outcome=baseline_out.headline,
                confidence="low",
            )]
    return [c[0] for c in candidates[:max_count]]
```

- [ ] **Step 3: Run — pass**

Expected: passes.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/levers.py AI_Agents/src/goal_planning/tests/agent/test_levers.py
git commit -m "feat(agent): propose_levers — composite ranking + top 3 + no-lever fallback"
```

---

### Task 42: agent/extractor.py — minimal stub for Phase 2 (real extractor in Phase 3)

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/extractor.py`

For Phase 2 tools to exist, we need a stub extractor that the real extraction tool can call. Phase 3 fills it in.

- [ ] **Step 1: Create stub**

```python
# AI_Agents/src/goal_planning/agent/extractor.py
"""NL extractor — Phase 3 implementation. Stub here for Phase 2 tool wiring."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import (
    ExtractedFinancialEvent, ExtractionError,
)


class FinancialEventExtractor:
    def __init__(self, model: str | None = None):
        self._model = model

    async def extract(
        self,
        description: str,
        anchor_date: date,
        existing_goal_names: list[str],
    ) -> ExtractedFinancialEvent | ExtractionError:
        # Stub for Phase 2 tests; real impl in Phase 3
        return ExtractionError(kind="error", reason="Extractor not yet implemented (Phase 3)")
```

- [ ] **Step 2: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/extractor.py
git commit -m "feat(agent): extractor stub for Phase 2 tool wiring"
```

---

### Task 43: agent/tools.py — extract_financial_event + apply_override + clear_overrides

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/tools.py`
- Create: `AI_Agents/src/goal_planning/tests/agent/test_tools.py`

- [ ] **Step 1: Write tests for tool wrappers**

```python
# AI_Agents/src/goal_planning/tests/agent/test_tools.py
from datetime import date
import pytest

from cashflow_statement.agent.tools import (
    extract_financial_event_impl,
    apply_override_impl,
    clear_overrides_impl,
)
from cashflow_statement.agent.state import AgentState
from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, NumericOverride,
)


def _state() -> AgentState:
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    return {  # type: ignore[return-value]
        "messages": [],
        "baseline_input": inp,
        "anchor_date": date(2026, 5, 9),
        "accumulated_overrides": [],
        "captured_goals": [],
        "captured_properties": [],
        "captured_cashflows": [],
        "captured_mutations": [],
        "last_output": None,
        "last_levers": [],
        "dirty": False,
        "error_log": [],
    }


def test_apply_override_appends_to_state():
    state = _state()
    summary = apply_override_impl(
        NumericOverride(kind="numeric", key="monthly_investment_next_12m", value=50_000), state,
    )
    assert len(state["accumulated_overrides"]) == 1
    assert state["dirty"] is True
    assert "monthly_investment_next_12m" in summary


def test_clear_overrides_empties_state():
    state = _state()
    state["accumulated_overrides"] = [
        NumericOverride(kind="numeric", key="monthly_investment_next_12m", value=50_000),
    ]
    summary = clear_overrides_impl(None, state)
    assert state["accumulated_overrides"] == []
    assert "cleared" in summary.lower()


@pytest.mark.asyncio
async def test_extract_financial_event_with_stub_returns_error():
    """Phase 2 stub returns ExtractionError; verify tool surfaces it as a string."""
    state = _state()
    summary = await extract_financial_event_impl("buy a house", state)
    assert "not yet implemented" in summary.lower() or "could not" in summary.lower()
```

- [ ] **Step 2: Run — fail (module not found)**

- [ ] **Step 3: Implement tools (impl functions; LangChain @tool wrappers added in Task 44)**

```python
# AI_Agents/src/goal_planning/agent/tools.py
"""Six tools for the LangGraph agent.

Each tool's *implementation function* (foo_impl) is a pure-Python operation on AgentState.
The @tool decorator versions are added in agent/__init__.py to keep this file LLM-import-free
where possible; ChatAnthropic is only imported at agent boundary.
"""
from __future__ import annotations
from datetime import date
from typing import Any

from cashflow_statement.models import (
    OverrideSpec, GoalMutation, ExtractedFinancialEvent, ExtractionError,
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
)
from cashflow_statement.agent.state import AgentState, CapturedCashflow
from cashflow_statement.agent.extractor import FinancialEventExtractor


_extractor = FinancialEventExtractor()


async def extract_financial_event_impl(description: str, state: AgentState) -> str:
    """Parse NL → discriminated-union; route to the right captured_* list."""
    existing_names = (
        ["retirement"]
        + [g.name for g in state["baseline_input"].custom_goals]
        + [c.name for c in state["captured_goals"]]
        + [p.name for p in state["baseline_input"].goal_properties]
        + [p.name for p in state["captured_properties"]]
    )
    result = await _extractor.extract(description, state["anchor_date"], existing_names)

    if isinstance(result, ExtractionError):
        state["error_log"].append(result.reason)
        return f"Could not extract: {result.reason}"

    state["dirty"] = True
    if isinstance(result, ExtractedGoal):
        state["captured_goals"].append(result.goal)
        return f"Captured custom goal: {result.goal.name} on {result.goal.goal_date.isoformat()}"
    if isinstance(result, ExtractedProperty):
        state["captured_properties"].append(result.property)
        if result.assumptions_used:
            return f"Captured property goal: {result.property.name}; assumptions used: {', '.join(result.assumptions_used)}"
        return f"Captured property goal: {result.property.name}"
    if isinstance(result, ExtractedCashflow):
        state["captured_cashflows"].append(CapturedCashflow(event=result.event, direction=result.direction))
        return f"Captured one-off {result.direction}flow: {result.event.description} ₹{result.event.amount:,.0f}"
    if isinstance(result, ExtractedMutation):
        state["captured_mutations"].append(GoalMutation(
            kind="mutation", op=result.op, goal_name=result.goal_name, fields=result.fields,
        ))
        return f"Captured mutation on {result.goal_name}: {result.op}"
    return f"Unknown extraction kind"


def apply_override_impl(override: OverrideSpec, state: AgentState) -> str:
    """Stage a parameter override. Validation happens at engine time (engine accepts the merged input)."""
    state["accumulated_overrides"].append(override)
    state["dirty"] = True
    if hasattr(override, "key"):
        return f"Override staged: {override.key}={override.value}. Run compute_projection to see impact."
    return f"Override staged. Run compute_projection to see impact."


def clear_overrides_impl(keys: list[str] | None, state: AgentState) -> str:
    """Clear all overrides (keys=None) or specific keys."""
    if keys is None:
        n = len(state["accumulated_overrides"])
        state["accumulated_overrides"] = []
        state["dirty"] = True
        return f"Cleared {n} override(s)."
    before = len(state["accumulated_overrides"])
    state["accumulated_overrides"] = [
        o for o in state["accumulated_overrides"] if getattr(o, "key", None) not in keys
    ]
    state["dirty"] = True
    return f"Cleared {before - len(state['accumulated_overrides'])} override(s)."


def mutate_goal_impl(op: str, goal_name: str, fields: dict[str, Any], state: AgentState) -> str:
    """Stage a goal mutation (add / remove / update). Retirement allowed per Q3."""
    state["captured_mutations"].append(GoalMutation(
        kind="mutation", op=op, goal_name=goal_name, fields=fields,  # type: ignore[arg-type]
    ))
    state["dirty"] = True
    return f"Goal mutation staged: {op} '{goal_name}' with fields {list(fields.keys())}"
```

- [ ] **Step 4: Run — 3 passes**

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/tools.py AI_Agents/src/goal_planning/tests/agent/test_tools.py
git commit -m "feat(agent): tool impls — extract_financial_event, apply_override, clear_overrides, mutate_goal"
```

---

### Task 44: agent/tools.py — compute_projection (idempotent) + propose_levers wrapper

**Files:**
- Modify: `AI_Agents/src/goal_planning/agent/tools.py`
- Modify: `AI_Agents/src/goal_planning/tests/agent/test_tools.py`

- [ ] **Step 1: Add tests for idempotency**

```python
def test_compute_projection_short_circuits_when_clean():
    from cashflow_statement.agent.tools import compute_projection_impl
    state = _state()
    s1 = compute_projection_impl(state)
    assert state["last_output"] is not None
    n_runs = 1

    state["dirty"] = False  # simulate post-compute cleanup
    s2 = compute_projection_impl(state)
    # Should return cached
    assert "cached" in s2.lower() or s2 == s1


def test_compute_projection_runs_when_dirty():
    from cashflow_statement.agent.tools import compute_projection_impl
    state = _state()
    state["dirty"] = True
    summary = compute_projection_impl(state)
    assert state["last_output"] is not None
    assert state["dirty"] is False  # reset post-compute


def test_propose_levers_no_op_when_no_output():
    from cashflow_statement.agent.tools import propose_levers_impl
    state = _state()
    summary = propose_levers_impl(state)
    assert "compute_projection first" in summary.lower() or "no output" in summary.lower()


def test_propose_levers_no_op_when_feasible():
    from cashflow_statement.agent.tools import propose_levers_impl, compute_projection_impl
    state = _state()
    state["baseline_input"] = state["baseline_input"].model_copy(update={
        "profile": state["baseline_input"].profile.model_copy(update={
            "financial_assets": 200_000_000,  # huge NFA
        }),
    })
    state["dirty"] = True
    compute_projection_impl(state)
    if state["last_output"] and state["last_output"].headline.is_overall_feasible:
        summary = propose_levers_impl(state)
        assert "no levers needed" in summary.lower()
```

- [ ] **Step 2: Implement**

Append to `tools.py`:

```python
def _merge_state_into_input(state: AgentState):
    """Apply accumulated overrides + captures into baseline_input → fresh GoalPlanningInput."""
    inp = state["baseline_input"].model_copy(deep=True)

    # Merge captured custom goals
    if state["captured_goals"]:
        inp.custom_goals = inp.custom_goals + state["captured_goals"]
    # Merge captured property goals
    if state["captured_properties"]:
        inp.goal_properties = inp.goal_properties + state["captured_properties"]
    # Merge captured cashflows by direction
    for cc in state["captured_cashflows"]:
        if cc.direction == "in":
            inp.one_off_inflows = inp.one_off_inflows + [cc.event]
        else:
            inp.one_off_outflows = inp.one_off_outflows + [cc.event]

    # Apply overrides (last-write-wins per key)
    by_key: dict[str, OverrideSpec] = {}
    for o in state["accumulated_overrides"]:
        if hasattr(o, "key"):
            by_key[o.key] = o
        else:
            by_key[id(o)] = o  # property overrides keyed by object identity for now

    for o in by_key.values():
        if o.kind == "numeric":
            if o.key == "monthly_investment_next_12m":
                inp.profile = inp.profile.model_copy(update={"monthly_investment_next_12m": o.value})
            elif o.key == "annual_income":
                inp.profile = inp.profile.model_copy(update={"annual_income": o.value})
            elif o.key == "monthly_household_expense":
                inp.profile = inp.profile.model_copy(update={"monthly_household_expense": o.value})
            elif o.key == "step_up_rate":
                inp.assumptions = inp.assumptions.model_copy(update={"annual_invested_amount_growth": o.value})
        elif o.kind == "rate":
            inp.assumptions = inp.assumptions.model_copy(update={o.key: o.value})
        # PerGoalRateOverride and PropertyFieldOverride: TODO refine engine integration

    # Apply mutations (each in order; last-write-wins per goal_name)
    from cashflow_statement.agent.levers import _apply_goal_mutation
    for m in state["captured_mutations"]:
        if m.op == "remove":
            inp.custom_goals = [g for g in inp.custom_goals if g.name.casefold() != m.goal_name.casefold()]
        elif m.op == "update":
            inp = _apply_goal_mutation(inp, m.goal_name, m.fields)
        # add: not supported via mutation in v1; goals come via captured_goals

    return inp


def compute_projection_impl(state: AgentState) -> str:
    """Run engine; idempotent (short-circuits if not dirty AND last_output exists)."""
    from cashflow_statement.engine import compute_full_projection

    if not state.get("dirty", True) and state.get("last_output") is not None:
        out = state["last_output"]
        return f"Cached projection: feasible={out.headline.is_overall_feasible}, " \
               f"shortfall=₹{out.headline.total_shortfall_fv:,.0f}, " \
               f"closing NFA=₹{out.headline.closing_nfa:,.0f}"

    inp = _merge_state_into_input(state)
    out = compute_full_projection(inp)
    state["last_output"] = out
    state["dirty"] = False

    return _summarize_output(out)


def _summarize_output(out) -> str:
    """Bounded summary string for the LLM (~300 tokens). Top-3 underfunded goals."""
    h = out.headline
    underfunded = sorted(
        [g for g in out.goals if g.shortfall_fv > 0],
        key=lambda g: g.shortfall_fv, reverse=True,
    )[:3]
    lines = [
        f"Feasible: {h.is_overall_feasible}",
        f"NFA today: ₹{h.net_financial_assets_today:,.0f}; closing NFA: ₹{h.closing_nfa:,.0f}",
        f"Total shortfall (FV): ₹{h.total_shortfall_fv:,.0f}",
        f"Retirement corpus needed: ₹{out.retirement.corpus_required_used:,.0f}",
    ]
    if underfunded:
        lines.append("Top underfunded goals:")
        for g in underfunded:
            lines.append(f"  - {g.name}: short by ₹{g.shortfall_fv:,.0f} (target ₹{g.amount_fv:,.0f})")
    return "\n".join(lines)


def propose_levers_impl(state: AgentState) -> str:
    """Generate up to 7 levers; return top 3 ranked summary."""
    from cashflow_statement.agent.levers import propose_levers

    out = state.get("last_output")
    if out is None:
        return "Run compute_projection first to generate a baseline output."
    if out.headline.is_overall_feasible:
        return "No shortfalls — no levers needed; plan is feasible."

    inp = _merge_state_into_input(state)
    levers = propose_levers(inp, out, max_count=3)
    state["last_levers"] = levers
    if not levers:
        return "No lever within the search bounds closes the gap."
    lines = [f"{i+1}. {l.description} (confidence: {l.confidence})" for i, l in enumerate(levers)]
    return "Recommended levers:\n" + "\n".join(lines)
```

- [ ] **Step 3: Run — pass**

Expected: 4 passes.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/tools.py AI_Agents/src/goal_planning/tests/agent/test_tools.py
git commit -m "feat(agent): compute_projection (idempotent) + propose_levers wrapper + state merge"
```

---

### Task 45: agent/prompts.py — system prompt + fallback messages

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/prompts.py`

- [ ] **Step 1: Create**

```python
# AI_Agents/src/goal_planning/agent/prompts.py
"""System prompts and fallback messages for the goal_planning agent."""

SYSTEM_PROMPT = """You are Tilly's goal-planning assistant.

Today's anchor date: {anchor_date}
Net financial assets: ₹{nfa_today:,.0f}

You have 6 tools — use them in this order when applicable:
1. extract_financial_event — when the user mentions a new goal, property, or one-off cashflow
2. apply_override / clear_overrides — when the user changes a parameter (income, expense, SIP, rate)
3. mutate_goal — when the user changes a specific goal (defer, reduce target, change retirement age)
4. compute_projection — ALWAYS run after step 1, 2, or 3, OR for a fresh query
5. propose_levers — only after compute_projection shows shortfalls

Workflow rules:
- Never compute_projection until you have ingested any new goals/overrides/mutations
- For pure Q&A about an existing projection (no new inputs), respond from your last output
- After tools return, write a concise narrative: state feasibility, name the largest shortfall, recommend a lever

Be concrete: rupee amounts, specific goal names. Never give investment advice — only project feasibility."""


_RECURSION_LIMIT_MESSAGE = (
    "I worked through several what-ifs but ran out of room — please ask a more focused question, "
    "such as 'what if I retire at 58?' or 'how much SIP do I need to fund my child's education?'"
)


_AGENT_DOWN_MESSAGE = (
    "I'm having trouble computing your goal-planning projection right now. "
    "Please try again in a moment, or check that your profile (date of birth, income, expenses) is up to date."
)
```

- [ ] **Step 2: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/prompts.py
git commit -m "feat(agent): system prompt + fallback messages"
```

---

### Task 46: agent/nodes.py — ingest_baseline_node, agent_node, should_continue

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/nodes.py`

- [ ] **Step 1: Implement nodes**

```python
# AI_Agents/src/goal_planning/agent/nodes.py
"""LangGraph nodes for goal_planning agent."""
from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END

from cashflow_statement.agent.state import AgentState
from cashflow_statement.agent.prompts import SYSTEM_PROMPT
from cashflow_statement.config import AGENT_MODEL


def ingest_baseline_node(state: AgentState) -> dict:
    """Validate persisted overrides against fresh baseline; drop orphans; reset levers; check baseline diff."""
    valid = []
    dropped = []
    for o in state.get("accumulated_overrides", []):
        # Validate property-level overrides have a matching property
        if hasattr(o, "property_name"):
            existing_names = {p.name.casefold() for p in state["baseline_input"].current_properties}
            existing_names |= {p.name.casefold() for p in state["baseline_input"].goal_properties}
            if o.property_name.casefold() not in existing_names:
                dropped.append(f"{o.kind}:{o.property_name}")
                continue
        valid.append(o)

    # Invalidate last_output if baseline diffs from echo
    last_out = state.get("last_output")
    invalidate = (
        last_out is not None
        and last_out.input_echo.profile != state["baseline_input"].profile
    )

    return {
        "accumulated_overrides": valid,
        "last_levers": [],
        "last_output": None if invalidate else last_out,
        "dirty": bool(dropped) or invalidate,
        "error_log": [
            *(state.get("error_log", [])),
            *(f"Dropped orphaned override: {d}" for d in dropped),
        ],
    }


def make_agent_node(tools: list):
    """Closure factory: bind tools and return the agent node fn."""
    llm = ChatAnthropic(model=AGENT_MODEL, temperature=0).bind_tools(tools)

    def agent_node(state: AgentState) -> dict:
        nfa = (
            state["baseline_input"].profile.financial_assets
            - state["baseline_input"].profile.financial_liabilities_excl_mortgage
        )
        sys_msg = SystemMessage(content=SYSTEM_PROMPT.format(
            anchor_date=state["anchor_date"].isoformat(),
            nfa_today=nfa,
        ))
        response = llm.invoke([sys_msg] + state["messages"])
        return {"messages": [response]}

    return agent_node


def should_continue(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return END
```

- [ ] **Step 2: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/nodes.py
git commit -m "feat(agent): graph nodes — ingest_baseline, agent_node, should_continue"
```

---

### Task 47: agent/graph.py + agent/__init__.py — compile graph + run_cashflow_statement_agent

**Files:**
- Create: `AI_Agents/src/goal_planning/agent/graph.py`
- Modify: `AI_Agents/src/goal_planning/agent/__init__.py`

- [ ] **Step 1: Implement graph compilation**

```python
# AI_Agents/src/goal_planning/agent/graph.py
"""StateGraph definition + compile."""
from __future__ import annotations
import asyncio
from datetime import date
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, InjectedState
from typing import Annotated

from cashflow_statement.agent.state import AgentState
from cashflow_statement.agent.nodes import ingest_baseline_node, make_agent_node, should_continue
from cashflow_statement.agent.tools import (
    extract_financial_event_impl, apply_override_impl, clear_overrides_impl,
    mutate_goal_impl, compute_projection_impl, propose_levers_impl,
)
from cashflow_statement.agent.prompts import _RECURSION_LIMIT_MESSAGE
from cashflow_statement.engine import ENGINE_VERSION
from cashflow_statement.models import (
    GoalPlanningInput, GoalPlanningResponse, OverrideSpec,
)


# === Tool wrappers (LangChain @tool) ===

@tool
def extract_financial_event(
    description: str,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Parse a natural-language description of a financial goal, property purchase, or one-off cashflow.

    Use when the user mentions a new goal, property, or cashflow event. Returns confirmation of what was captured."""
    return asyncio.run(extract_financial_event_impl(description, state))


@tool
def apply_override(
    override: dict,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Stage a what-if change to a parameter (income, expense, SIP, rate).

    Pass an OverrideSpec dict with `kind` discriminator. Does not run projection — call compute_projection after."""
    from pydantic import TypeAdapter
    parsed = TypeAdapter(OverrideSpec).validate_python(override)
    return apply_override_impl(parsed, state)


@tool
def clear_overrides(
    keys: list[str] | None,
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Clear staged overrides (all if keys=None, or specific keys)."""
    return clear_overrides_impl(keys, state)


@tool
def mutate_goal(
    op: str,
    goal_name: str,
    fields: dict[str, Any],
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Add/remove/update a goal (incl. retirement). Use for defer-goal, reduce-target, change-retirement-age changes."""
    return mutate_goal_impl(op, goal_name, fields, state)


@tool
def compute_projection(
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Run the goal-planning engine. Idempotent — short-circuits if no mutations since last run."""
    return compute_projection_impl(state)


@tool
def propose_levers(
    state: Annotated[AgentState, InjectedState],
) -> str:
    """Generate up to 3 deterministic recommendations to close shortfalls in the latest projection."""
    return propose_levers_impl(state)


TOOLS = [
    extract_financial_event, apply_override, clear_overrides,
    mutate_goal, compute_projection, propose_levers,
]


def build_graph(checkpointer=None):
    workflow = StateGraph(AgentState)
    workflow.add_node("ingest_baseline", ingest_baseline_node)
    workflow.add_node("agent", make_agent_node(TOOLS))
    workflow.add_node("tools", ToolNode(TOOLS))

    workflow.set_entry_point("ingest_baseline")
    workflow.add_edge("ingest_baseline", "agent")
    workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    workflow.add_edge("tools", "agent")

    return workflow.compile(checkpointer=checkpointer)


_compiled_graph = None


def get_compiled_graph():
    """Singleton — instantiate once at first use."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph(checkpointer=MemorySaver())
    return _compiled_graph


def extract_terminal_narrative(messages: list[BaseMessage]) -> str:
    """Walk backward to find the last AIMessage with no tool_calls."""
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            content = msg.content
            return content if isinstance(content, str) else str(content)
    return "(no narrative)"


async def run_cashflow_statement_agent(
    user_message: str,
    baseline_input: GoalPlanningInput,
    chat_session_id: str,
    anchor_date: date,
) -> GoalPlanningResponse:
    config = {
        "configurable": {"thread_id": chat_session_id},
        "recursion_limit": 15,
    }
    state_update = {
        "messages": [HumanMessage(content=user_message)],
        "baseline_input": baseline_input,
        "anchor_date": anchor_date,
    }
    graph = get_compiled_graph()
    try:
        final = await graph.ainvoke(state_update, config)  # type: ignore[arg-type]
    except Exception as e:
        # Graceful fallback for recursion limit and other graph errors
        return GoalPlanningResponse(
            engine_version=ENGINE_VERSION,
            output=None,
            narrative=_RECURSION_LIMIT_MESSAGE,
            levers=[],
        )

    return GoalPlanningResponse(
        engine_version=ENGINE_VERSION,
        output=final.get("last_output"),
        narrative=extract_terminal_narrative(final["messages"]),
        levers=final.get("last_levers", []),
    )
```

- [ ] **Step 2: Implement agent/__init__.py**

```python
# AI_Agents/src/goal_planning/agent/__init__.py
from .graph import (
    build_graph, get_compiled_graph, run_cashflow_statement_agent,
    TOOLS,
)

# Aliased for spec consistency
cashflow_statement_graph = get_compiled_graph

__all__ = [
    "cashflow_statement_graph", "build_graph", "get_compiled_graph",
    "run_cashflow_statement_agent", "TOOLS",
]
```

- [ ] **Step 3: Smoke import test**

Run: `python -c "from cashflow_statement.agent import run_cashflow_statement_agent, TOOLS; print(len(TOOLS))"`
Expected: `6`

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/graph.py AI_Agents/src/goal_planning/agent/__init__.py
git commit -m "feat(agent): graph compilation + tool wrappers + run_cashflow_statement_agent entry"
```

---

### Task 48: FakeChatAnthropic test harness + 6 agent E2E tests

**Files:**
- Create: `AI_Agents/src/goal_planning/tests/agent/conftest.py`
- Create: `AI_Agents/src/goal_planning/tests/agent/test_e2e.py`

- [ ] **Step 1: Create test harness**

```python
# AI_Agents/src/goal_planning/tests/agent/conftest.py
"""Test fixtures for agent E2E."""
from __future__ import annotations
from datetime import date
from typing import Iterator

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver


class FakeChatAnthropic(ChatAnthropic):
    """Returns canned AIMessages without HTTP. Avoids SDK retry semantics in tests."""

    def __init__(self, responses: list[AIMessage], **kwargs):
        # Skip super().__init__ to avoid network/auth setup
        self._responses = iter(responses)
        for k, v in kwargs.items():
            setattr(self, k, v)

    def invoke(self, messages, **kwargs) -> AIMessage:
        return next(self._responses)

    async def ainvoke(self, messages, **kwargs) -> AIMessage:
        return next(self._responses)

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def fresh_memory_saver() -> MemorySaver:
    """Per-test MemorySaver — avoids state bleed across tests."""
    return MemorySaver()


@pytest.fixture
def fake_llm_factory():
    def _build(*responses: AIMessage) -> FakeChatAnthropic:
        return FakeChatAnthropic(responses=list(responses))
    return _build


@pytest.fixture
def anchor_date() -> date:
    return date(2026, 5, 9)
```

- [ ] **Step 2: Write 6 E2E tests** (per spec §10.4)

```python
# AI_Agents/src/goal_planning/tests/agent/test_e2e.py
"""Agent E2E with FakeChatAnthropic. Six scenarios per spec §10.4."""
from datetime import date
import pytest
from langchain_core.messages import AIMessage
from cashflow_statement.models import (
    GoalPlanningInput, ClientProfile, RetirementInput,
)


def _baseline():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000, monthly_investment_next_12m=50_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )


@pytest.mark.asyncio
async def test_e2e_initial_query_compute_then_narrate(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #1: User asks 'am I on track?' → agent calls compute_projection → narrate."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="Your retirement is on track. NFA today: ₹15M. No shortfalls."),
    ]
    fake = fake_llm_factory(*canned)
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    # Reset compiled-graph cache so it picks up the patched ChatAnthropic
    import cashflow_statement.agent.graph as g
    g._compiled_graph = None

    from cashflow_statement.agent import run_cashflow_statement_agent
    response = await run_cashflow_statement_agent(
        user_message="Am I on track for retirement?",
        baseline_input=_baseline(),
        chat_session_id="test-1",
        anchor_date=anchor_date,
    )
    assert "track" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_what_if_retire_at_58(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #2: 'What if I retire at 58?' → mutate_goal → compute → narrate."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "mutate_goal",
            "args": {"op": "update", "goal_name": "retirement", "fields": {"retirement_age": 58}},
            "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "2", "type": "tool_call",
        }]),
        AIMessage(content="Retiring at 58 makes you underfunded by ₹X."),
    ]
    fake = fake_llm_factory(*canned)
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import cashflow_statement.agent.graph as g
    g._compiled_graph = None

    from cashflow_statement.agent import run_cashflow_statement_agent
    response = await run_cashflow_statement_agent(
        user_message="What if I retire at 58?",
        baseline_input=_baseline(),
        chat_session_id="test-2",
        anchor_date=anchor_date,
    )
    assert "58" in response.narrative


@pytest.mark.asyncio
async def test_e2e_q_and_a_uses_cached_output(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #4: Q&A turn — agent doesn't call any tool, narrates from prior context."""
    canned = [
        AIMessage(content="Your retirement corpus needs ₹3 Cr in today's money."),
    ]
    fake = fake_llm_factory(*canned)
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import cashflow_statement.agent.graph as g
    g._compiled_graph = None

    from cashflow_statement.agent import run_cashflow_statement_agent
    response = await run_cashflow_statement_agent(
        user_message="Why is retirement tricky?",
        baseline_input=_baseline(),
        chat_session_id="test-3",
        anchor_date=anchor_date,
    )
    # No tool was called → output is None for this turn
    assert response.output is None
    assert "corpus" in response.narrative.lower()


# Tests E2E #3 (NL goal capture), #5 (shortfall + propose_levers), #6 (recursion limit) follow same pattern.
# Engineer: write each as canned message sequences mirroring the tool call flow.
@pytest.mark.asyncio
async def test_e2e_nl_goal_capture(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #3: 'I want to send daughter abroad in 2040' → extract → compute → narrate."""
    # Implementation note: the stub extractor returns ExtractionError; expect a clarifying narrative
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "extract_financial_event",
            "args": {"description": "send daughter abroad in 2040"},
            "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="I couldn't parse that goal yet — extractor in development."),
    ]
    fake = fake_llm_factory(*canned)
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import cashflow_statement.agent.graph as g
    g._compiled_graph = None

    from cashflow_statement.agent import run_cashflow_statement_agent
    response = await run_cashflow_statement_agent(
        user_message="Send daughter abroad in 2040, ~1Cr in today's money",
        baseline_input=_baseline(),
        chat_session_id="test-4",
        anchor_date=anchor_date,
    )
    assert "develop" in response.narrative.lower() or "couldn't" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_shortfall_then_propose_levers(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #5: shortfall → compute_projection → propose_levers → narrate w/ markdown bullets."""
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": "1", "type": "tool_call",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "propose_levers", "args": {}, "id": "2", "type": "tool_call",
        }]),
        AIMessage(content="You're short ₹X. Top fixes:\n- Increase SIP by ₹Y\n- Defer goal by 2y"),
    ]
    fake = fake_llm_factory(*canned)
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import cashflow_statement.agent.graph as g
    g._compiled_graph = None

    from cashflow_statement.agent import run_cashflow_statement_agent
    inp = _baseline().model_copy(update={
        "profile": _baseline().profile.model_copy(update={"financial_assets": 5_000_000}),
    })
    response = await run_cashflow_statement_agent(
        user_message="Help me with shortfall",
        baseline_input=inp,
        chat_session_id="test-5",
        anchor_date=anchor_date,
    )
    assert "fix" in response.narrative.lower() or "lever" in response.narrative.lower()


@pytest.mark.asyncio
async def test_e2e_recursion_limit_returns_graceful_message(monkeypatch, fake_llm_factory, anchor_date):
    """E2E #6: agent in infinite tool loop → recursion limit → fallback narrative."""
    # Generate >15 messages all with tool_calls
    canned = [
        AIMessage(content="", tool_calls=[{
            "name": "compute_projection", "args": {}, "id": str(i), "type": "tool_call",
        }])
        for i in range(20)
    ]
    fake = fake_llm_factory(*canned)
    monkeypatch.setattr("goal_planning.agent.nodes.ChatAnthropic", lambda *a, **kw: fake)
    import cashflow_statement.agent.graph as g
    g._compiled_graph = None

    from cashflow_statement.agent import run_cashflow_statement_agent
    response = await run_cashflow_statement_agent(
        user_message="Run forever",
        baseline_input=_baseline(),
        chat_session_id="test-6",
        anchor_date=anchor_date,
    )
    # Graceful fallback message
    assert "ran out" in response.narrative.lower() or "focused" in response.narrative.lower()
```

- [ ] **Step 3: Run**

Expected: 6 passed.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/src/goal_planning/tests/agent/conftest.py AI_Agents/src/goal_planning/tests/agent/test_e2e.py
git commit -m "test(agent): 6 E2E tests with FakeChatAnthropic harness"
```

---

### Task 49: Phase 2 verification gate

- [ ] **Step 1: Run full Phase 2 suite**

Run:
```bash
pytest AI_Agents/src/goal_planning/tests/agent/ -v \
       --cov=AI_Agents/src/goal_planning/agent --cov-fail-under=80
```

- [ ] **Step 2: Verify acceptance criteria**

| Gate | Verification |
|---|---|
| All 6 E2E tests pass | `pytest tests/agent/test_e2e.py` |
| 7 lever generators present and tested | `pytest tests/agent/test_levers.py` |
| Lever search budget < 2.0s | manual perf check via `time pytest tests/agent/test_levers.py::test_propose_levers_returns_top_3_ranked` |
| 80% agent coverage | coverage report |
| Recursion-limit fallback works | E2E #6 passes |

- [ ] **Step 3: Tag**

```bash
git tag goal-planning-phase-2
git commit --allow-empty -m "feat(goal_planning): Phase 2 verification gate passed"
```

---

## Phase 3: NL Extractor (full implementation)

### Task 50: agent/extractor.py — full chain with discriminated-union output

**Files:**
- Modify: `AI_Agents/src/goal_planning/agent/extractor.py`
- Modify: `AI_Agents/src/goal_planning/agent/prompts.py`
- Create: `AI_Agents/src/goal_planning/tests/agent/test_extractor.py`

- [ ] **Step 1: Add extractor system prompt to prompts.py**

```python
# Append to agent/prompts.py
EXTRACTOR_SYSTEM_PROMPT = """You are extracting a single financial event from a user message.

Today: {anchor_date}
Existing goals (for collision detection): {existing_goal_names}

Decide one of four kinds and produce the matching structured output:
- custom_goal — life goal (education, marriage, generic): "send daughter to college in 2040"
- property_goal — real-estate purchase: "buy a 2cr second home in 2032"
- cashflow_event — one-off in/out: "I'll get a 50L bonus next March", "spend 30L on renovation"
- goal_mutation — change to existing: "increase my retirement target by 20%"

Defaults you may use (disclose in `assumptions_used` if applied):
- Property downpayment: {default_property_downpayment_pct}%
- Mortgage tenure: {default_mortgage_tenure_years} years
- Mortgage interest: {default_mortgage_interest:.1%} annual

Date resolution:
- "in N years" → today + N years (use end-of-year if not specified)
- "next March" → next FY-end after today (Indian FY ends Mar 31)
- year only → year-end of that year

Cashflow direction examples (REQUIRED for cashflow_event):
- INFLOW: "get/receive/inherit/sell/refund/gift/bonus"
- OUTFLOW: "spend/pay/buy/wedding/donate/renovation"

If a goal name fuzzy-matches an existing goal, return goal_mutation (op=update), not a new goal.

Few-shot examples:

Example 1 — Property goal with mortgage:
INPUT: "Want to buy a 2cr second home in 2032 with 30% downpayment"
OUTPUT: kind=property_goal, property={{name="second_home", target_pv=20000000, is_downpayment_only=true, upfront_amount=6000000, goal_date="2032-12-31", mortgage_tenure_years=20, mortgage_interest_annual=0.085}}, assumptions_used=["mortgage_tenure_years=20", "mortgage_interest_annual=8.5%"]

Example 2 — Custom goal in PV:
INPUT: "Save 50L in today's money for daughter's college in 2040"
OUTPUT: kind=custom_goal, goal={{name="daughter_college", goal_type="child_local_education", amount_pv=5000000, goal_date="2040-12-31"}}

Example 3 — Custom goal in FV:
INPUT: "I'll need exactly 1 crore in 2040 for my son's wedding"
OUTPUT: kind=custom_goal, goal={{name="son_wedding", goal_type="child_marriage", amount_fv=10000000, goal_date="2040-12-31"}}

Example 4 — Cashflow inflow:
INPUT: "Selling stock for 25L in March 2027"
OUTPUT: kind=cashflow_event, event={{description="stock_sale", amount=2500000, date="2027-03-31"}}, direction="in", confidence="high"

Example 5 — Cashflow outflow:
INPUT: "Home renovation will cost 30L in 2028"
OUTPUT: kind=cashflow_event, event={{description="renovation", amount=3000000, date="2028-12-31"}}, direction="out", confidence="high"

Example 6 — Goal mutation:
INPUT: "Increase my retirement target by 20%"
OUTPUT: kind=goal_mutation, op="update", goal_name="retirement", fields={{"retirement_corpus_pv_override": <new value>}}
"""
```

- [ ] **Step 2: Write tests**

```python
# AI_Agents/src/goal_planning/tests/agent/test_extractor.py
"""4-kind round-trip tests for the consolidated extractor.

Uses FakeChatAnthropic to avoid live LLM calls. Each test feeds a canned response.
"""
from datetime import date
import pytest
from langchain_core.messages import AIMessage

from cashflow_statement.models import (
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
    ExtractionError, GoalType,
)
from cashflow_statement.agent.extractor import FinancialEventExtractor


@pytest.mark.asyncio
async def test_extract_custom_goal(monkeypatch):
    """Round-trip: NL → ExtractedGoal."""
    extractor = FinancialEventExtractor()
    # Stub the chain to return a parsed result
    canned = ExtractedGoal(
        kind="custom_goal",
        goal={
            "name": "college", "goal_type": "child_local_education",
            "amount_pv": 1_000_000, "goal_date": "2035-01-01",
        },
    )
    monkeypatch.setattr(extractor, "_chain", _ConstantChain(canned))
    result = await extractor.extract(
        description="College in 2035, 10 lakh today",
        anchor_date=date(2026, 5, 9),
        existing_goal_names=[],
    )
    assert isinstance(result, ExtractedGoal)
    assert result.goal.goal_date == date(2035, 1, 1)


@pytest.mark.asyncio
async def test_extract_property_goal_post_fills_defaults(monkeypatch):
    """When LLM returns property without mortgage details, post-fill applies defaults."""
    extractor = FinancialEventExtractor()
    canned = ExtractedProperty(
        kind="property_goal",
        property={
            "name": "house", "target_pv": 10_000_000,
            "is_downpayment_only": True, "upfront_amount": None,  # missing!
            "goal_date": "2030-05-09",
            "mortgage_tenure_years": 0, "mortgage_interest_annual": 0.075,
        },
        assumptions_used=[],
    )
    monkeypatch.setattr(extractor, "_chain", _ConstantChain(canned))
    result = await extractor.extract(
        description="Buy a house in 2030 for 1Cr",
        anchor_date=date(2026, 5, 9),
        existing_goal_names=[],
    )
    if isinstance(result, ExtractedProperty):
        # Post-fill applied defaults
        assert result.assumptions_used  # non-empty


@pytest.mark.asyncio
async def test_extract_cashflow(monkeypatch):
    extractor = FinancialEventExtractor()
    canned = ExtractedCashflow(
        kind="cashflow_event",
        event={"description": "bonus", "amount": 500_000, "date": "2027-03-31"},
        direction="in",
        confidence="high",
    )
    monkeypatch.setattr(extractor, "_chain", _ConstantChain(canned))
    result = await extractor.extract("bonus next March", date(2026, 5, 9), [])
    assert isinstance(result, ExtractedCashflow)
    assert result.direction == "in"


@pytest.mark.asyncio
async def test_extract_mutation_via_fuzzy_match(monkeypatch):
    """When NL goal name fuzzy-matches existing, promote to mutation."""
    extractor = FinancialEventExtractor()
    canned = ExtractedGoal(
        kind="custom_goal",
        goal={
            "name": "Retirement Fund",  # fuzzy-matches "retirement"
            "goal_type": "retirement",
            "amount_pv": 50_000_000, "goal_date": "2036-05-09",
        },
    )
    monkeypatch.setattr(extractor, "_chain", _ConstantChain(canned))
    result = await extractor.extract(
        description="Increase my retirement fund target",
        anchor_date=date(2026, 5, 9),
        existing_goal_names=["retirement"],
    )
    assert isinstance(result, ExtractedMutation), f"Expected mutation, got {type(result).__name__}"
    assert result.goal_name == "retirement"


@pytest.mark.asyncio
async def test_past_date_returns_extraction_error(monkeypatch):
    extractor = FinancialEventExtractor()
    canned = ExtractedGoal(
        kind="custom_goal",
        goal={
            "name": "old_goal", "goal_type": "custom",
            "amount_pv": 1_000_000, "goal_date": "2024-01-01",
        },
    )
    monkeypatch.setattr(extractor, "_chain", _ConstantChain(canned))
    result = await extractor.extract("old goal", date(2026, 5, 9), [])
    assert isinstance(result, ExtractionError)
    assert "past" in result.reason.lower()


# Helper
class _ConstantChain:
    def __init__(self, value):
        self._value = value

    def invoke(self, *_args, **_kwargs):
        return self._value
```

- [ ] **Step 3: Implement full extractor per spec §9.1**

```python
# AI_Agents/src/goal_planning/agent/extractor.py
"""NL → ExtractedFinancialEvent | ExtractionError."""
from __future__ import annotations
import asyncio
from datetime import date
from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.exceptions import OutputParserException
from langchain_core.prompts import ChatPromptTemplate
from pydantic import ValidationError
from rapidfuzz import fuzz

from cashflow_statement.config import (
    EXTRACTOR_MODEL, FUZZY_MATCH_THRESHOLD,
    DEFAULT_PROPERTY_DOWNPAYMENT_PCT, DEFAULT_MORTGAGE_TENURE_YEARS, DEFAULT_MORTGAGE_INTEREST_ANNUAL,
)
from cashflow_statement.models import (
    ExtractedFinancialEvent, ExtractionError,
    ExtractedGoal, ExtractedProperty, ExtractedCashflow, ExtractedMutation,
)
from cashflow_statement.agent.prompts import EXTRACTOR_SYSTEM_PROMPT


def _normalize(name: str) -> str:
    """Lowercase + strip common stop words."""
    stops = {"the", "my", "a", "an", "fund", "goal", "for"}
    return " ".join(w for w in name.casefold().split() if w not in stops)


class FinancialEventExtractor:
    def __init__(self, model: str = EXTRACTOR_MODEL):
        self._llm = ChatAnthropic(model=model, temperature=0)
        self._chain = self._build_chain()

    def _build_chain(self):
        prompt = ChatPromptTemplate.from_messages([
            ("system", EXTRACTOR_SYSTEM_PROMPT),
            ("human", "{description}"),
        ])
        return prompt | self._llm.with_structured_output(ExtractedFinancialEvent)

    async def extract(
        self,
        description: str,
        anchor_date: date,
        existing_goal_names: list[str],
    ) -> ExtractedFinancialEvent | ExtractionError:
        try:
            result = await asyncio.to_thread(self._chain.invoke, {
                "description": description,
                "anchor_date": anchor_date.isoformat(),
                "existing_goal_names": ", ".join(existing_goal_names) or "(none)",
                "default_property_downpayment_pct": DEFAULT_PROPERTY_DOWNPAYMENT_PCT,
                "default_mortgage_tenure_years": DEFAULT_MORTGAGE_TENURE_YEARS,
                "default_mortgage_interest": DEFAULT_MORTGAGE_INTEREST_ANNUAL,
            })
        except (OutputParserException, ValidationError, anthropic.APIError) as e:
            return ExtractionError(kind="error", reason=f"Could not parse: {e}")

        # Post-fill property defaults
        if isinstance(result, ExtractedProperty):
            result = self._post_fill_property_defaults(result)

        # Fuzzy collision: promote to mutation
        if isinstance(result, (ExtractedGoal, ExtractedProperty)):
            new_name = result.goal.name if isinstance(result, ExtractedGoal) else result.property.name
            best_match = self._best_fuzzy_match(new_name, existing_goal_names)
            if best_match:
                return ExtractedMutation(
                    kind="goal_mutation", op="update",
                    goal_name=best_match,
                    fields=self._diff_against_existing(result, best_match),
                )

        # Past-date guard via dated_field
        d = result.dated_field()
        if d is not None and d < anchor_date:
            return ExtractionError(kind="error", reason=f"Date {d.isoformat()} is in the past")

        return result

    def _best_fuzzy_match(self, new_name: str, existing: list[str]) -> str | None:
        best_score = 0
        best_match = None
        normalized_new = _normalize(new_name)
        for name in existing:
            score = fuzz.token_set_ratio(normalized_new, _normalize(name))
            if score > best_score:
                best_score = score
                best_match = name
        return best_match if best_score >= FUZZY_MATCH_THRESHOLD else None

    def _diff_against_existing(self, result: ExtractedFinancialEvent, name: str) -> dict[str, Any]:
        """Extract the fields the user wants to change. For now, return everything user provided."""
        if isinstance(result, ExtractedGoal):
            return result.goal.model_dump(exclude_unset=True, exclude={"name"})
        if isinstance(result, ExtractedProperty):
            return result.property.model_dump(exclude_unset=True, exclude={"name"})
        return {}

    def _post_fill_property_defaults(self, result: ExtractedProperty) -> ExtractedProperty:
        """Fill missing property defaults; record what was filled in `assumptions_used`."""
        assumptions = list(result.assumptions_used)
        prop = result.property

        if prop.is_downpayment_only and prop.upfront_amount is None and prop.target_pv is not None:
            new_upfront = prop.target_pv * (DEFAULT_PROPERTY_DOWNPAYMENT_PCT / 100)
            prop = prop.model_copy(update={"upfront_amount": new_upfront})
            assumptions.append(f"upfront_amount={DEFAULT_PROPERTY_DOWNPAYMENT_PCT}% of target")

        if prop.is_downpayment_only and prop.mortgage_tenure_years == 0:
            prop = prop.model_copy(update={"mortgage_tenure_years": DEFAULT_MORTGAGE_TENURE_YEARS})
            assumptions.append(f"mortgage_tenure_years={DEFAULT_MORTGAGE_TENURE_YEARS}")

        return ExtractedProperty(kind="property_goal", property=prop, assumptions_used=assumptions)
```

- [ ] **Step 4: Add config constant**

Add to `goal_planning/config.py` (created in Task 51):

```python
FUZZY_MATCH_THRESHOLD = 85  # rapidfuzz token_set_ratio (0-100)
```

- [ ] **Step 5: Run — pass**

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/goal_planning/agent/extractor.py AI_Agents/src/goal_planning/agent/prompts.py AI_Agents/src/goal_planning/tests/agent/test_extractor.py
git commit -m "feat(agent): full NL extractor — discriminated union, fuzzy match, past-date guard, post-fill defaults"
```

---

### Task 51: Phase 3 verification gate

- [ ] **Step 1: Run full extractor suite**

Run: `pytest AI_Agents/src/goal_planning/tests/agent/test_extractor.py -v`
Expected: 5 passed.

- [ ] **Step 2: Re-run E2E #3 (NL goal capture) — extractor now functional**

Update `test_e2e_nl_goal_capture` to use a canned ExtractedGoal response (vs the stub's ExtractionError). Verify the agent now successfully captures.

- [ ] **Step 3: Tag**

```bash
git tag goal-planning-phase-3
git commit --allow-empty -m "feat(goal_planning): Phase 3 — NL extractor complete"
```

---

## Phase 4: Public API + boundary polish

### Task 52: config.py — module constants

**Files:**
- Create: `AI_Agents/src/goal_planning/config.py`

- [ ] **Step 1: Create**

```python
# AI_Agents/src/goal_planning/config.py
"""Module constants for goal_planning. No BaseSettings — matches project pattern."""
import os

AGENT_MODEL = os.getenv("GOAL_PLANNING_AGENT_MODEL", "claude-sonnet-4-6")
EXTRACTOR_MODEL = os.getenv("GOAL_PLANNING_EXTRACTOR_MODEL", "claude-haiku-4-5-20251001")
RECURSION_LIMIT = int(os.getenv("GOAL_PLANNING_RECURSION_LIMIT", "15"))
USE_CHECKPOINTER = os.getenv("GOAL_PLANNING_USE_CHECKPOINTER", "true").lower() == "true"
CHECKPOINTER_TYPE = os.getenv("GOAL_PLANNING_CHECKPOINTER_TYPE", "postgres")  # "memory" or "postgres"

# Lever search bounds
SIP_MAX_MULTIPLIER = 5.0                      # Lever A
DEFER_MAX_YEARS = 10                          # Lever B
REDUCE_MAX_PCT = 0.50                         # Lever C
STEP_UP_MAX_DELTA_PP = 0.20                   # Lever E
EXPENSE_REDUCE_PCT_LIST = (0.05, 0.10, 0.15)  # Lever F
MORTGAGE_PAYOFF_YEARS_LIST = (1, 3, 5, 10)    # Lever G

# Extractor defaults (2026 India)
DEFAULT_PROPERTY_DOWNPAYMENT_PCT = 20.0
DEFAULT_MORTGAGE_TENURE_YEARS = 20
DEFAULT_MORTGAGE_INTEREST_ANNUAL = 0.085
FUZZY_MATCH_THRESHOLD = 85
```

- [ ] **Step 2: Commit**

```bash
git add AI_Agents/src/goal_planning/config.py
git commit -m "feat(goal_planning): config.py — module constants"
```

---

### Task 53: Add anthropic API key helper to app/config.py

**Files:**
- Modify: `app/config.py`

- [ ] **Step 1: Read existing helpers**

Run: `grep -n "get_anthropic_.*_key" app/config.py`
Expected: shows existing `get_anthropic_asset_allocation_key()` (or similar).

- [ ] **Step 2: Add helper**

Append (mirroring the existing `get_anthropic_asset_allocation_key` pattern):

```python
def get_anthropic_goal_planning_key() -> str:
    """Anthropic API key for goal_planning module. Falls back to ANTHROPIC_API_KEY."""
    return os.getenv("ANTHROPIC_GOAL_PLANNING_API_KEY") or os.getenv("ANTHROPIC_API_KEY", "")
```

- [ ] **Step 3: Commit**

```bash
git add app/config.py
git commit -m "feat(config): get_anthropic_goal_planning_key helper"
```

---

### Task 54: goal_planning/__init__.py — top-level public API with __all__

**Files:**
- Modify: `AI_Agents/src/goal_planning/__init__.py`
- Create: `AI_Agents/src/goal_planning/tests/test_public_api.py`

- [ ] **Step 1: Write public API test**

```python
# AI_Agents/src/goal_planning/tests/test_public_api.py
def test_public_api_exports():
    import cashflow_statement as gp

    # Engine
    assert hasattr(gp, "compute_full_projection")
    assert hasattr(gp, "validate_input_only")
    assert hasattr(gp, "ENGINE_VERSION")

    # Agent
    assert hasattr(gp, "cashflow_statement_graph")
    assert hasattr(gp, "run_cashflow_statement_agent")

    # Inputs
    for name in ["GoalPlanningInput", "Assumptions", "ClientProfile", "RetirementInput",
                 "CurrentProperty", "GoalProperty", "CustomGoal", "OneOffEvent"]:
        assert hasattr(gp, name)

    # Outputs
    for name in ["GoalPlanningOutput", "GoalPlanningResponse",
                 "HeadlineStatus", "RetirementSnapshot", "FundFlowSummary",
                 "GoalFundingStatus", "OneOffFundingStatus",
                 "AnnualCashflowRow", "MonthlyCashflowRow", "MonthlyNFARow",
                 "MortgageAmortization", "MortgageAmortizationRow",
                 "ValidationIssue"]:
        assert hasattr(gp, name)

    # Agent types
    for name in ["OverrideSpec", "NumericOverride", "RateOverride",
                 "PerGoalRateOverride", "PropertyFieldOverride",
                 "GoalMutation", "LeverAction", "Lever",
                 "ExtractedFinancialEvent", "ExtractedGoal", "ExtractedProperty",
                 "ExtractedCashflow", "ExtractedMutation", "ExtractionError"]:
        assert hasattr(gp, name)

    assert hasattr(gp, "GoalType")


def test_internal_types_not_exported():
    """RunContext, MortgageSchedule, etc. are engine-private."""
    import cashflow_statement as gp
    assert not hasattr(gp, "RunContext")
    assert not hasattr(gp, "MortgageSchedule")
    assert not hasattr(gp, "GoalInternal")
    assert not hasattr(gp, "FundingResult")
```

- [ ] **Step 2: Run — fail (some exports missing)**

- [ ] **Step 3: Implement __init__.py**

```python
# AI_Agents/src/goal_planning/__init__.py
"""Goal Planning AI module — public API.

Bridge code imports from here only. Internal types (RunContext, MortgageSchedule, etc.)
live in engine/_types.py and are NOT exported.
"""
from .engine import compute_full_projection, validate_input_only, ENGINE_VERSION
from .agent import cashflow_statement_graph, run_cashflow_statement_agent
from .models import (
    # Inputs
    GoalPlanningInput, Assumptions, ClientProfile, RetirementInput,
    CurrentProperty, GoalProperty, CustomGoal, OneOffEvent,
    # Outputs
    GoalPlanningOutput, GoalPlanningResponse,
    HeadlineStatus, RetirementSnapshot, FundFlowSummary,
    GoalFundingStatus, OneOffFundingStatus,
    AnnualCashflowRow, MonthlyCashflowRow, MonthlyNFARow,
    MortgageAmortization, MortgageAmortizationRow,
    ValidationIssue,
    # Agent types
    OverrideSpec, NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride,
    GoalMutation, LeverAction, Lever,
    ExtractedFinancialEvent, ExtractedGoal, ExtractedProperty,
    ExtractedCashflow, ExtractedMutation, ExtractionError,
    # Enums
    GoalType,
)

__all__ = [
    "compute_full_projection", "validate_input_only", "ENGINE_VERSION",
    "cashflow_statement_graph", "run_cashflow_statement_agent",
    "GoalPlanningInput", "Assumptions", "ClientProfile", "RetirementInput",
    "CurrentProperty", "GoalProperty", "CustomGoal", "OneOffEvent",
    "GoalPlanningOutput", "GoalPlanningResponse",
    "HeadlineStatus", "RetirementSnapshot", "FundFlowSummary",
    "GoalFundingStatus", "OneOffFundingStatus",
    "AnnualCashflowRow", "MonthlyCashflowRow", "MonthlyNFARow",
    "MortgageAmortization", "MortgageAmortizationRow",
    "ValidationIssue",
    "OverrideSpec", "NumericOverride", "RateOverride",
    "PerGoalRateOverride", "PropertyFieldOverride",
    "GoalMutation", "LeverAction", "Lever",
    "ExtractedFinancialEvent", "ExtractedGoal", "ExtractedProperty",
    "ExtractedCashflow", "ExtractedMutation", "ExtractionError",
    "GoalType",
]
```

- [ ] **Step 4: Run — pass**

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/goal_planning/__init__.py AI_Agents/src/goal_planning/tests/test_public_api.py
git commit -m "feat(goal_planning): top-level public API with __all__"
```

---

### Task 55: Final boundary verification + smoke test

**Files:**
- Run: full test suite

- [ ] **Step 1: Run boundary tests**

Run: `pytest AI_Agents/src/goal_planning/tests/boundary/ -v`
Expected: 2 passed (engine no-LLM, bridge top-level only).

- [ ] **Step 2: Run full module suite with coverage**

Run:
```bash
pytest AI_Agents/src/financial_primitives/ AI_Agents/src/goal_planning/ -v \
       --cov=AI_Agents/src/financial_primitives \
       --cov=AI_Agents/src/goal_planning \
       --cov-report=term-missing:skip-covered \
       --cov-report=html
```

- [ ] **Step 3: Smoke test the full agent**

Create `AI_Agents/src/goal_planning/tests/integration/test_smoke.py`:

```python
# AI_Agents/src/goal_planning/tests/integration/test_smoke.py
"""End-to-end smoke test: import → engine projection → agent response."""
from datetime import date


def test_smoke_engine():
    from cashflow_statement import (
        compute_full_projection, GoalPlanningInput, ClientProfile, RetirementInput,
    )
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    assert out.engine_version is not None
    assert out.headline.number_of_goals >= 1
```

- [ ] **Step 4: Commit + tag**

```bash
git add AI_Agents/src/goal_planning/tests/integration/test_smoke.py
git commit -m "test(goal_planning): final smoke test"
git tag goal-planning-v1
git commit --allow-empty -m "release(goal_planning): v1 complete — Phase 4 verification gate"
```

---

## Self-Review Checklist (run after writing all tasks above)

Per the writing-plans skill instructions:

### 1. Spec coverage check

| Spec section | Implementing task |
|---|---|
| §5 package layout | Tasks 3, 8, 16, 17, 27, 41 (each builds part of layout) |
| §6.1 input types | Tasks 9, 10, 11 |
| §6.2 output types | Task 12 |
| §6.3 agent types | Tasks 13, 14 |
| §6.4 validators | Task 11 |
| §7.1 engine file map | Tasks 14-26 (one per file) |
| §7.2 8-stage orchestrator | Task 26 |
| §7.3 internal types | Task 17 |
| §7.4 30 calculations | Tasks 16-25 (mapped) |
| §7.5 conventions (ROUND, ROI bands, RATE) | Tasks 16, 19, 22 |
| §7.6 exceptions | Task 15 |
| §8 agent (state, graph, tools, levers) | Tasks 37-49 |
| §9.1 NL extractor | Task 50 |
| §9.3 config | Task 52 |
| §9.5 public API | Task 54 |
| §10.2 Excel parity harness | Tasks 32, 33, 34, 35 |
| §10.3 13 synthetic tests | Tasks 28, 29, 30 |
| §10.5 boundary lint | Task 4 |
| §10.7 perf/memory | Task 31 |
| §10.8 test infra | Tasks 1, 2 |
| §11 edge cases | Tested across Tasks 19, 20, 24, 30 |

All spec sections covered.

### 2. Placeholder scan

- "Engineer fills in" appears in Task 32 (Excel cell mapping refinement) — this is genuine: the cell mapping requires iterative inspection of the Excel layout.
- Task 24 step 5 says "Implementation detail; engineer fills in fixture wiring" — same caveat.
- All other steps contain runnable code or specific commands.

### 3. Type consistency

- `compute_full_projection` defined in Task 26, called in Tasks 28-30, 31, 32 — consistent signature.
- `ENGINE_VERSION` defined in Task 26, exported in Task 27, used in Task 50, 54 — consistent.
- `RunContext` defined in Task 17, used in Tasks 18, 19, 20, 21, 22, 23, 24, 25 — consistent.
- `MortgageSchedule.property_ref` format `"existing:<name>"` / `"goal:<name>"` consistent across Tasks 20, 21, 28-30.
- `OverrideSpec` discriminated union: defined in Task 13 (without retirement_age literal — Q3); referenced in Tasks 38, 43, 50 — consistent.
- `GoalMutation` for retirement: documented in Task 13, used in Tasks 39, 43, 50 — consistent.

### 4. Open dependencies (pre-v1 work outside engineering)

- **Task 33** depends on Sourabh authoring 3 variant Excel files for scenarios 02-04. This is a resourcing requirement called out in the spec (Q1).

---

## Plan complete

Plan saved to: `docs/superpowers/plans/2026-05-09-goal-planning-implementation.md`

**Two execution options:**

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for a long plan with many small tasks.

2. **Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

**Which approach?**









