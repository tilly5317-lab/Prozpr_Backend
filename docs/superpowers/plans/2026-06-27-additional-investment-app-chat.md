# Additional Investment — App Integration (Chat Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the existing additional_investment engine into the FastAPI chat flow so a deploy question gets a reply that names the funds to buy.

**Architecture:** New app/domains/additional_investment/ domain mirroring rebalancing/: an ainv_engine bridge (input_builder + service + chat handler) that builds AdditionalInvestmentInput from the practical allocation, goal funding and fund ranking, runs the pure engine, and formats the reply through the shared question-aware formatter; wired via one flow_additional_investment + one FLOWS row.

**Tech Stack:** FastAPI, SQLAlchemy async, Pydantic v2, Alembic, pytest; AI_Agents engine via sys.path injection.

## Global Constraints

- **Mirror rebalancing/**: every file mirrors its app/domains/rebalancing/ counterpart; engine subfolder is `ainv_engine` (`ai_engine` is taken by the chat-brain domain).
- **Engine is frozen**: `AI_Agents/src/additional_investment/` already exists and is tested — import `run_additional_investment`/`AdditionalInvestmentInput`/`AdditionalInvestmentOutput` via sys.path injection (`ensure_ai_agents_path()`); do not modify the engine.
- **Money = float** (allocation family), never Decimal; do not import `_to_decimal` in the persist service.
- **I/O naming = Input/Output** (allocation family), not ComputeRequest/Response.
- **LLM calls go through LangChain / the shared formatter** — the chat reply routes through `format_with_telemetry`; the Invest-page headline is a deterministic computed-field (the only sanctioned exception).
- **Tests**: `.venv-mac/bin/python -m pytest <path> -v` from /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend; new ORM model → register in BOTH `app/all_models.py` and the domain `models/__init__.py`; sqlite tests create only the table(s) under test.
- **Intent already routes**: the classifier emits `additional_investment` (Phase 2 complete).

---

### Task 1: Domain skeleton (additional_investment package)

Create the `app/domains/additional_investment/` package skeleton, mirroring `app/domains/rebalancing/`. This task lays down only the import-only scaffolding: package markers, the engine subfolder (named `ainv_engine` because `ai_engine` is already taken by the chat orchestrator domain), the tests package, the routers marker, and the domain `CLAUDE.md`. `models/` and `schemas/` are deliberately deferred to Plan 3b.

Decided-default notes (state-and-move-on):
- The engine subfolder is `ainv_engine`, NOT `ai_engine` (that name is taken by `app/domains/ai_engine/`).
- `ainv_engine/__init__.py` is docstring-only and must NOT eager-import `chat` — eager import triggers a circular import via `chat_core.turn_context`, exactly as documented for `rebal_engine` (`app/domains/rebalancing/services/rebal_engine/CLAUDE.md`). The `@register("additional_investment")` side-effect is landed by a lazy `from ...ainv_engine import chat` inside `additional_investment_module_service.run` (added in a later 3a task).

**Files:**
- Create: `app/domains/additional_investment/__init__.py` — empty package marker (0 bytes).
- Create: `app/domains/additional_investment/services/__init__.py` — empty package marker (0 bytes).
- Create: `app/domains/additional_investment/services/ainv_engine/__init__.py` — docstring-only; no eager chat import.
- Create: `app/domains/additional_investment/services/ainv_engine/tests/__init__.py` — empty package marker (0 bytes).
- Create: `app/domains/additional_investment/routers/__init__.py` — empty marker (0 bytes); the `/additional-investment` router export is added in Plan 3b.
- Create: `app/domains/additional_investment/CLAUDE.md` — domain context doc.
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_skeleton.py` — the import-contract test.

**Interfaces:**
- Consumes: nothing from earlier tasks (first task in Plan 3a). Relies only on the existing `app.domains` package chain.
- Produces (later 3a/3b tasks depend on these exact import paths):
  - `app.domains.additional_investment` — importable package.
  - `app.domains.additional_investment.services` — importable package.
  - `app.domains.additional_investment.services.ainv_engine` — importable, docstring-only package that does NOT pull in `chat` on import (the lazy-chat invariant later tasks rely on for `additional_investment_module_service.run`).
  - `app.domains.additional_investment.services.ainv_engine.tests` — importable test package.
  - `app.domains.additional_investment.routers` — importable package (router export filled in 3b).

---

- [ ] **Step 1: Write the failing test** — create `app/domains/additional_investment/services/ainv_engine/tests/test_skeleton.py`. All app imports are done INSIDE the test bodies via `importlib`, so the test module itself collects even before the skeleton exists, and the red state is a clean `ModuleNotFoundError` at call time. Mirrors the rebalancing register/lazy-import style (`app/domains/rebalancing/services/rebal_engine/tests/test_chat.py` uses `importlib` + `sys.modules`).

```python
"""Skeleton import contract for the additional_investment app domain.

Mirrors the rebalancing engine's lazy-chat invariant: importing the
``ainv_engine`` package must NOT eagerly import its ``chat`` submodule —
eager import risks a circular import via ``chat_core.turn_context``, exactly
as documented for ``rebal_engine``
(``app/domains/rebalancing/services/rebal_engine/CLAUDE.md``).

App imports are performed inside the test bodies (via importlib) so this test
module collects cleanly even before the skeleton exists; the red state is a
ModuleNotFoundError raised when each test runs.
"""

from __future__ import annotations

import importlib
import sys

_DOMAIN = "app.domains.additional_investment"
_SERVICES = _DOMAIN + ".services"
_AINV_ENGINE = _SERVICES + ".ainv_engine"
_AINV_TESTS = _AINV_ENGINE + ".tests"
_ROUTERS = _DOMAIN + ".routers"
_AINV_CHAT = _AINV_ENGINE + ".chat"


def test_package_and_subpackages_import():
    """Every skeleton package imports without error."""
    pkg = importlib.import_module(_DOMAIN)
    importlib.import_module(_SERVICES)
    importlib.import_module(_AINV_ENGINE)
    importlib.import_module(_AINV_TESTS)
    importlib.import_module(_ROUTERS)
    assert pkg is not None


def test_ainv_engine_init_does_not_import_chat_eagerly():
    """Importing the ainv_engine package must not pull in its chat submodule.

    Drop any cached copies first so we observe a genuinely fresh import of the
    package __init__, then assert the chat module name never landed in
    sys.modules as a side-effect.
    """
    for name in (_AINV_CHAT, _AINV_ENGINE):
        sys.modules.pop(name, None)

    importlib.import_module(_AINV_ENGINE)

    assert _AINV_CHAT not in sys.modules, (
        "ainv_engine/__init__.py must stay docstring-only — eagerly importing "
        "chat risks a circular import via chat_core.turn_context"
    )
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_skeleton.py -v
```

Expected failure: the module collects, then both tests error at runtime with `ModuleNotFoundError: No module named 'app.domains.additional_investment'` → `2 failed`.

- [ ] **Step 3: Write minimal implementation** — create the six skeleton files.

Create the four empty package markers (0-byte files):

```
: > app/domains/additional_investment/__init__.py
: > app/domains/additional_investment/services/__init__.py
: > app/domains/additional_investment/services/ainv_engine/tests/__init__.py
: > app/domains/additional_investment/routers/__init__.py
```

`app/domains/additional_investment/services/ainv_engine/__init__.py` — docstring-only (no code), mirroring `rebal_engine/__init__.py`:

```python
"""Additional-investment engine adapter, chat handler, and input builder.

Wraps the pure ``AI_Agents.additional_investment`` engine
(``run_additional_investment``) for the FastAPI app. The ``chat`` submodule is
**not** auto-imported here: doing so triggers a circular import via
``chat_core.turn_context``. Callers that need its ``@register`` side-effect
must import ``chat`` lazily (e.g. inside ``additional_investment_module_service.run``).

NOTE: ``service.compute_additional_investment_result`` /
``AdditionalInvestmentRunOutcome``, ``input_builder``, ``fund_rank`` and
``chat`` are added in later Plan-3a tasks. Until then this package is an
import-only marker. The engine subfolder is named ``ainv_engine`` because
``ai_engine`` is already taken by the chat orchestrator domain.
"""
```

`app/domains/additional_investment/CLAUDE.md` — domain context doc (convention v2: `# path/ — purpose` → `## Entry / contract` → `## Layers` → `## Gotchas & invariants` → `## Don't read`), mirroring `app/domains/rebalancing/CLAUDE.md`:

```markdown
# app/domains/additional_investment/ — deploy fresh money (lumpsum/SIP) into specific funds to BUY

## Entry / contract
- `additional_investment_module_service` is the ONLY gateway to the additional-investment AI module — the brain calls its `run(turn, ctx, prior)`. The pure engine that picks the funds lives in `AI_Agents/src/additional_investment` (`run_additional_investment`) and is reached only through this domain. The AI bridge that *produces* the source allocation lives in `app/domains/ai_engine`.

## Layers
- **models/** — `AdditionalInvestmentRun` (table `additional_investment_runs`) and its children `AdditionalInvestmentTarget` / `AdditionalInvestmentBuy` (added in Plan 3b).
- **schemas/** — the run-API contract (pydantic views over the run tables) including the read-time computed `summary` Invest-page headline (added in Plan 3b).
- **routers/** — the `/additional-investment` router (added in Plan 3b).
- **services/** — `additional_investment_persist_service` (the write surface, called by the ai_engine bridge); `additional_investment_module_service` (the gateway above); `additional_investment_summary` (deterministic Invest-page headline builder); `ainv_engine/` — the compute orchestration (named `ainv_engine` because `ai_engine` is taken) that wraps the pure `AI_Agents.additional_investment` engine.

## Gotchas & invariants
- **Import `chat` lazily.** `ainv_engine/__init__.py` is docstring-only and must NOT re-export `chat` — eager import triggers a circular import via `chat_core.turn_context` (mirrors `rebal_engine`). The `@register("additional_investment")` side-effect is landed by a lazy `from ...ainv_engine import chat` inside `additional_investment_module_service.run`.
- **BUY-only, write-once.** A run only adds new money; there is no sell/status lifecycle and no update-status route (`models/`, Plan 3b).
- **Money is `float`, not `Decimal`.** This domain follows the allocation family (`practical_asset_allocation`), not Rebalancing — floats flow straight into `Numeric(18,2)`; do NOT import `_to_decimal` in the persist service (`services/additional_investment_persist_service.py`, Plan 3b).
- A run always deploys against the persisted practical-allocation run it was derived from — `source_allocation_run_id` (FK to `practical_asset_allocation_runs.id`) is required (Plan 3b).

## Don't read
- `__pycache__/`.
```

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_skeleton.py -v
```

Expected: `2 passed` — `test_package_and_subpackages_import` and `test_ainv_engine_init_does_not_import_chat_eagerly` both PASS.

- [ ] **Step 5: Commit**

```
git add app/domains/additional_investment/
git commit -m "feat(additional_investment): add app domain package skeleton

Mirror app/domains/rebalancing/: empty package markers, docstring-only
ainv_engine/ (no eager chat import — circular-import guard), routers marker,
tests package, and the domain CLAUDE.md. models/ and schemas/ land in Plan 3b.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 2: scheme_code-capable fund ranking

The additional_investment engine's `RankedFund` requires a non-empty `scheme_code` (`AI_Agents/src/additional_investment/models.py:50` — `scheme_code: str`), but the rebalancing fund-rank loader silently drops the CSV's `scheme_code` column. Extend `FundRankRow` and `get_fund_ranking()` in the rebalancing engine so the column is captured once, at the single CSV parse that is the source of truth. The later ainv input-builder task maps `get_fund_ranking()` rows -> `RankedFund(..., scheme_code=...)`.

**Decided default (one-line note):** scheme_code is captured by extending the EXISTING `rebal_engine/fund_rank.py` loader (one parse, source of truth) — NOT by adding a second loader. The new field goes LAST on `FundRankRow` (after `selection_reason=""`) so it is non-breaking: both existing constructors use keyword args and the held-fund constructor at `input_builder.py:301` does not pass `scheme_code` (it receives `""`). This loader stays: beyond feeding the engine's `RankedFund.scheme_code`, scheme_code is now also persisted downstream — Plan 3b stores it on each `AdditionalInvestmentBuy` row.

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/fund_rank.py:63` (the `FundRankRow` frozen dataclass — add trailing `scheme_code: str = ""`)
- Modify: `app/domains/rebalancing/services/rebal_engine/fund_rank.py:99` (the `FundRankRow(...)` construction inside `get_fund_ranking()` — populate `scheme_code` from the CSV)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_fund_rank.py` (append two tests)

**Interfaces:**
- Consumes (from earlier tasks): None. This extends an existing rebalancing-engine file.
- Produces (later tasks rely on these EXACT shapes):
  - `FundRankRow(asset_subgroup: str, sub_category: str, rank: int, isin: str, fund_name: str, selection_reason: str = "", scheme_code: str = "")` — frozen dataclass; `scheme_code` is the new trailing field.
  - `get_fund_ranking() -> dict[str, list[FundRankRow]]` — signature unchanged; every ranked `FundRankRow` now carries a non-empty `.scheme_code` lifted from the CSV `scheme_code` column.

---

- [ ] **Step 1: Write the failing test** — append two tests to `app/domains/rebalancing/services/rebal_engine/tests/test_fund_rank.py`. Mirror the existing pin-the-CSV style (the suite already reads the real production CSV; no DB/LLM involved). The canonical first row of `low_beta_equities` (ICICI Prudential, ISIN `INF109K016L0`) has `scheme_code == "120586"` in `AI_Agents/Reference_docs/prozpr_fund_ranking_may_2026.csv`.

```python
def test_every_ranked_row_exposes_non_empty_scheme_code():
    """RankedFund (additional_investment) needs scheme_code; the CSV carries it
    in a 'scheme_code' column between 'isin' and 'recommended_fund'. Every
    recommended row must surface it as a non-empty string."""
    ranking = get_fund_ranking()
    assert ranking, "expected the production fund-rank CSV to load"
    for subgroup, rows in ranking.items():
        for r in rows:
            assert isinstance(r.scheme_code, str), (
                f"{subgroup} rank {r.rank} ({r.isin}): scheme_code not a str"
            )
            assert r.scheme_code.strip(), (
                f"{subgroup} rank {r.rank} ({r.isin}): empty scheme_code"
            )


def test_first_row_low_beta_equities_pins_scheme_code():
    """Pin the canonical row 0 scheme_code to catch an accidental CSV swap or a
    column-misread (scheme_code vs isin)."""
    first = get_fund_ranking()["low_beta_equities"][0]
    assert first.isin == "INF109K016L0"
    assert first.scheme_code == "120586"
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_fund_rank.py -v
```

Expected failure: `AttributeError: 'FundRankRow' object has no attribute 'scheme_code'` raised by both `test_every_ranked_row_exposes_non_empty_scheme_code` and `test_first_row_low_beta_equities_pins_scheme_code` (the four pre-existing tests still pass).

- [ ] **Step 3: Write minimal implementation** — two edits in `app/domains/rebalancing/services/rebal_engine/fund_rank.py`. Add `scheme_code` as the LAST field of the dataclass (it must follow `selection_reason` because a defaulted field cannot precede the no-default `fund_name`), and populate it from the CSV in `get_fund_ranking()`.

Edit 1 — the `FundRankRow` dataclass. Replace the complete class definition:

```python
@dataclass(frozen=True)
class FundRankRow:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    fund_name: str
    selection_reason: str = ""
```

with:

```python
@dataclass(frozen=True)
class FundRankRow:
    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    fund_name: str
    selection_reason: str = ""
    scheme_code: str = ""
```

Edit 2 — the `FundRankRow(...)` construction inside `get_fund_ranking()`. Replace:

```python
            by_sg[row["asset_subgroup"]].append(
                FundRankRow(
                    asset_subgroup=row["asset_subgroup"],
                    sub_category=row["sub_category"],
                    rank=rank_int,
                    isin=row["isin"],
                    fund_name=row["recommended_fund"],
                    selection_reason=(row.get("selection_reason") or "").strip(),
                )
            )
```

with:

```python
            by_sg[row["asset_subgroup"]].append(
                FundRankRow(
                    asset_subgroup=row["asset_subgroup"],
                    sub_category=row["sub_category"],
                    rank=rank_int,
                    isin=row["isin"],
                    fund_name=row["recommended_fund"],
                    selection_reason=(row.get("selection_reason") or "").strip(),
                    scheme_code=(row.get("scheme_code") or "").strip(),
                )
            )
```

No other change: `get_fund_ranking()`'s signature, sorting, force-exit handling, and the `get_force_exit_isins()` / `get_rejection_reasons()` loaders are untouched. The held-fund constructor at `input_builder.py:301` keeps working — it omits `scheme_code` and receives the `""` default.

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_fund_rank.py -v
```

Expected: all 6 tests PASS (4 pre-existing + the 2 new). To confirm the additive field broke no rebalancing caller, also run:

```
.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests -v
```

Expected: the full rebal_engine suite stays green.

- [ ] **Step 5: Commit**

```
git add app/domains/rebalancing/services/rebal_engine/fund_rank.py \
        app/domains/rebalancing/services/rebal_engine/tests/test_fund_rank.py
git commit -m "feat(rebal_engine): capture CSV scheme_code on FundRankRow

Add a trailing scheme_code field to FundRankRow and populate it from the
CSV 'scheme_code' column in get_fund_ranking(), so the additional_investment
input builder can map ranked rows to RankedFund.scheme_code from one parse.
Additive default-'' field; existing keyword constructors unaffected.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 3: Engine input builder

Materialise an `AdditionalInvestmentInput` from `TurnContext` + the practical-allocation output, mirroring `rebal_engine/input_builder.py`. The engine is now **HOLDING-AGNOSTIC** and BUY-only and follows the allocation family, so this builder is deliberately much simpler than rebalancing's: **money is `float`** (no `Decimal`, no `_to_decimal`), there is **NO holdings fetch at all** (no DB ledger, no NAV pricing, no per-holding classify/force-exit mapping), and there is **no corpus total at all** — the per-fund caps key off the DEPLOY amount, so the builder reads no existing-corpus total off the allocation output and computes no resulting-corpus figure. ALL practical-allocation subgroup rows are passed through verbatim; the two synthetic rows (`tax_efficient_equities`, `non_mf_equities`) are NOT hand-dropped here — instead the builder sets `exclude_subgroups={"tax_efficient_equities","non_mf_equities"}` and the engine gives them zero weight and renormalises the split onto the remaining subgroups.

**Decided defaults applied (one-liners):**
- I/O = `AdditionalInvestmentInput` (allocation family), money is `float`.
- `short_term_fulfilled = all(g.is_funded for g in goals where months_to(goal_date) < 36)` — `True` when there are no such goals.
- `medium_term_fulfilled = all(g.is_funded for g in goals where 36 <= months_to(goal_date) < 72)` — `True` when there are no such goals.
- Pass ALL `aggregated_subgroups` rows into `SubgroupBucketAmounts` (no hand-drop); set `exclude_subgroups={"tax_efficient_equities","non_mf_equities"}` and let the engine zero-weight + renormalise them off the split.
- Per-fund caps key off the DEPLOY amount (the engine's `cap_pct × deploy_amount`), so the builder reads no existing-corpus total and computes no resulting-corpus figure.
- `cap_pct_by_subgroup = {row.subgroup: cap_pct_for(row.subgroup) for eligible rows}`; `default_cap_pct = OTHERS_FUND_CAP_PCT`.
- `scheme_code` on each `RankedFund` comes from the T2-extended `FundRankRow.scheme_code`.

**Files:**
- Create: `app/domains/additional_investment/services/ainv_engine/input_builder.py`
- Create (test): `app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py`
- Create (package markers, only if an earlier task has not already): `app/domains/additional_investment/__init__.py` (empty), `app/domains/additional_investment/services/__init__.py` (empty), `app/domains/additional_investment/services/ainv_engine/__init__.py` (docstring-only — do NOT eager-import `chat`), `app/domains/additional_investment/services/ainv_engine/tests/__init__.py` (empty)

**Interfaces:**
- Consumes: `FundRankRow.scheme_code` + `get_fund_ranking()` rows carrying `scheme_code` (Task 3a-T2); engine models `AdditionalInvestmentInput` / `SubgroupBucketAmounts` / `RankedFund` / `Cadence` (already implemented — the holding-agnostic engine no longer has a `Holding` model); `get_fund_ranking`; `run_cashflow_projection_for_user`; `cap_pct_for` / `OTHERS_FUND_CAP_PCT`; `MEDIUM_TERM_BOUNDARY_MONTHS` / `LONG_TERM_BOUNDARY_MONTHS`; the practical-allocation output's `.aggregated_subgroups`. (No existing-corpus total is read — the per-fund caps key off the deploy amount; no `build_holdings_ledger` / `HoldingLedgerEntry` / `get_force_exit_isins` / `classify_holding` / NAV pricing — there is no holdings path.)
- Produces: `build_additional_investment_input_for_user(ctx, allocation_output, *, deploy_amount_inr: float, cadence: Cadence) -> tuple[AdditionalInvestmentInput, dict[str, Any]]` — consumed by Task 3a-T4 (orchestrator `service.py`'s `compute_additional_investment_result`).

---

- [ ] **Step 1: Write the failing test** — every collaborator (fund-ranking CSV, cashflow projection) is replaced with an in-memory stand-in via `monkeypatch`; the allocation output is a plain stand-in; no real DB or LLM, and no holdings path at all. Mirrors `rebal_engine/tests/test_input_builder.py`'s structure (helper + one assertion-focused function per concern).

```python
# app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py
"""Unit tests for the additional-investment engine input builder.

Mirrors rebal_engine/tests/test_input_builder.py, but the engine is now
HOLDING-AGNOSTIC: the only collaborators are the fund-ranking CSV and the
cashflow projection (both replaced with plain in-memory stand-ins), plus a
stand-in allocation output. There is no DB ledger / NAV / classifier path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from app.domains.additional_investment.services.ainv_engine import input_builder as ib
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    build_additional_investment_input_for_user,
)


# ── stand-ins ──────────────────────────────────────────────────────────────
@dataclass
class _Row:
    """Stand-in for AggregatedSubgroupRow (only .subgroup + .model_dump consumed)."""

    subgroup: str
    emergency: float = 0.0
    short_term: float = 0.0
    medium_term: float = 0.0
    long_term: float = 0.0
    total: float = 0.0

    def model_dump(self) -> dict:
        return {
            "subgroup": self.subgroup,
            "emergency": self.emergency,
            "short_term": self.short_term,
            "medium_term": self.medium_term,
            "long_term": self.long_term,
            "total": self.total,
        }


@dataclass
class _RankRow:
    """Stand-in for the T2-extended FundRankRow (carries scheme_code)."""

    asset_subgroup: str
    sub_category: str
    rank: int
    isin: str
    scheme_code: str
    fund_name: str


@dataclass
class _Goal:
    goal_date: date
    is_funded: bool


# ── helpers ────────────────────────────────────────────────────────────────
def _months_from_now(months: int) -> date:
    """A 1st-of-month date exactly `months` whole months ahead of today."""
    today = date.today()
    total = today.year * 12 + (today.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(user_ctx=SimpleNamespace(id=uuid.uuid4()))


def _alloc(rows) -> SimpleNamespace:
    """Stand-in for the practical-allocation output: just the per-subgroup rows.
    The builder reads no corpus total — the per-fund caps key off the deploy
    amount, so there is no existing-corpus field to stand in for."""
    return SimpleNamespace(aggregated_subgroups=rows)


def _patch(monkeypatch, *, ranking=None, goals=()):
    """Patch the only two real collaborators: the fund-ranking CSV loader and the
    cashflow projection. No ledger / NAV / classifier — the engine is
    holding-agnostic."""
    ranking = ranking or {}

    monkeypatch.setattr(ib, "get_fund_ranking", lambda: ranking)

    async def _fake_cashflow(user, *, anchor_date=None):
        return SimpleNamespace(goals=list(goals))

    monkeypatch.setattr(ib, "run_cashflow_projection_for_user", _fake_cashflow)


# ── tests ──────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_subgroups_map_all_rows_and_set_exclude(monkeypatch):
    """ALL practical-allocation rows (incl. the two synthetic ones) pass through
    verbatim — the 6 bucket fields map 1:1 — and the builder hands the engine
    ``exclude_subgroups`` so IT drops the synthetic rows from the split."""
    rows = [
        _Row("large_cap", long_term=300000.0, total=300000.0),
        _Row("short_debt", short_term=100000.0, total=100000.0),
        _Row("tax_efficient_equities", long_term=50000.0, total=50000.0),
        _Row("non_mf_equities", long_term=40000.0, total=40000.0),
    ]
    _patch(monkeypatch)

    inp, _debug = await build_additional_investment_input_for_user(
        _ctx(), _alloc(rows), deploy_amount_inr=100000.0, cadence=ib.Cadence.LUMPSUM
    )

    # No hand-drop: all four rows are present in subgroups.
    assert [s.subgroup for s in inp.subgroups] == [
        "large_cap",
        "short_debt",
        "tax_efficient_equities",
        "non_mf_equities",
    ]
    lc = next(s for s in inp.subgroups if s.subgroup == "large_cap")
    assert lc.long_term == 300000.0
    assert lc.total == 300000.0

    # The engine drops the synthetic rows via exclude_subgroups (zero weight).
    assert inp.exclude_subgroups == {"tax_efficient_equities", "non_mf_equities"}


@pytest.mark.asyncio
async def test_ranked_funds_flattened_with_scheme_code(monkeypatch):
    """get_fund_ranking() is flattened across subgroups; scheme_code carries through."""
    ranking = {
        "large_cap": [
            _RankRow("large_cap", "Large Cap Fund", 1, "INF_LC1", "120001", "LC One"),
            _RankRow("large_cap", "Large Cap Fund", 2, "INF_LC2", "120002", "LC Two"),
        ],
        "short_debt": [
            _RankRow("short_debt", "Low Duration", 1, "INF_SD1", "120003", "SD One"),
        ],
    }
    _patch(monkeypatch, ranking=ranking)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=50000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert len(inp.ranked_funds) == 3
    by_isin = {r.isin: r for r in inp.ranked_funds}
    assert by_isin["INF_LC1"].scheme_code == "120001"
    assert by_isin["INF_LC1"].rank == 1
    assert by_isin["INF_LC1"].recommended_fund == "LC One"
    assert by_isin["INF_SD1"].asset_subgroup == "short_debt"


@pytest.mark.asyncio
async def test_short_term_unfunded_sets_flag_false(monkeypatch):
    """An unfunded <36-month goal -> short_term_fulfilled is False; with the only
    medium goal funded, medium_term_fulfilled stays True."""
    goals = [
        _Goal(_months_from_now(12), is_funded=False),  # short-term, unfunded
        _Goal(_months_from_now(48), is_funded=True),   # medium-term, funded
    ]
    _patch(monkeypatch, goals=goals)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.short_term_fulfilled is False
    assert inp.medium_term_fulfilled is True


@pytest.mark.asyncio
async def test_medium_term_unfunded_sets_flag_false(monkeypatch):
    """An unfunded 36–72-month goal -> medium_term_fulfilled is False; with the
    short goal funded, short_term_fulfilled stays True. (Long-term goal ignored.)"""
    goals = [
        _Goal(_months_from_now(12), is_funded=True),   # short-term, funded
        _Goal(_months_from_now(48), is_funded=False),  # medium-term, unfunded
        _Goal(_months_from_now(84), is_funded=False),  # long-term, ignored
    ]
    _patch(monkeypatch, goals=goals)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.short_term_fulfilled is True
    assert inp.medium_term_fulfilled is False


@pytest.mark.asyncio
async def test_both_flags_true_when_funded_or_none(monkeypatch):
    """A funded short goal + a funded medium goal -> both flags True (long-term
    goal ignored; True is also the no-goals default for each bucket)."""
    goals = [
        _Goal(_months_from_now(12), is_funded=True),   # short-term, funded
        _Goal(_months_from_now(48), is_funded=True),   # medium-term, funded
        _Goal(_months_from_now(84), is_funded=False),  # long-term, ignored
    ]
    _patch(monkeypatch, goals=goals)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc([_Row("large_cap", long_term=1.0, total=1.0)]),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.short_term_fulfilled is True
    assert inp.medium_term_fulfilled is True


@pytest.mark.asyncio
async def test_caps_use_cap_pct_for_and_others_default(monkeypatch):
    """cap_pct_by_subgroup routes through cap_pct_for; default_cap_pct is OTHERS_FUND_CAP_PCT."""
    _patch(monkeypatch)

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(),
        _alloc(
            [
                _Row("large_cap", long_term=1.0, total=1.0),
                _Row("short_debt", short_term=1.0, total=1.0),
            ]
        ),
        deploy_amount_inr=100000.0,
        cadence=ib.Cadence.LUMPSUM,
    )

    assert inp.cap_pct_by_subgroup["large_cap"] == ib.cap_pct_for("large_cap")
    assert inp.cap_pct_by_subgroup["short_debt"] == ib.cap_pct_for("short_debt")
    assert inp.default_cap_pct == ib.OTHERS_FUND_CAP_PCT
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -v
```

Expected failure: collection error `ModuleNotFoundError: No module named 'app.domains.additional_investment'` (the package + `input_builder.py` do not exist yet).

- [ ] **Step 3: Write minimal implementation** — create the package markers (only if an earlier task hasn't), then `input_builder.py` in full.

Package markers (empty unless noted):

```python
# app/domains/additional_investment/__init__.py
```

```python
# app/domains/additional_investment/services/__init__.py
```

```python
# app/domains/additional_investment/services/ainv_engine/__init__.py
"""Additional-investment compute engine: cache-first orchestration → engine
inputs → BUY list → chat markdown. Reached only through the domain's
additional_investment_module_service.

Do NOT eager-import ``chat`` here — that triggers a circular import via
chat_core.turn_context (same rule as rebal_engine/__init__.py).
"""
```

```python
# app/domains/additional_investment/services/ainv_engine/tests/__init__.py
```

The builder:

```python
# app/domains/additional_investment/services/ainv_engine/input_builder.py
"""Materialise an AdditionalInvestmentInput from TurnContext + allocation output.

Mirrors rebal_engine/input_builder.py, but for the HOLDING-AGNOSTIC, BUY-only
additional-investment engine: money is plain ``float`` (allocation family, not
Decimal), and there is NO holdings path at all — no DB ledger, no NAV pricing,
no per-holding classify/force-exit mapping. The engine recommends purely from
the ranked-fund list, and the per-fund caps key off the DEPLOY amount, so the
builder reads no existing-corpus total and computes no resulting-corpus figure.
ALL practical-allocation subgroup rows are passed through verbatim; the two
synthetic rows (ELSS + non-MF equity) are NOT hand-dropped here — the builder
sets ``exclude_subgroups`` and the engine gives them zero weight and renormalises
the split onto the remaining (eligible) subgroups.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.cashflow.services.cashflow_compute_service import (
    run_cashflow_projection_for_user,
)
from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentInput,
    Cadence,
    RankedFund,
    SubgroupBucketAmounts,
)
from asset_allocation_pydantic.tables import (  # type: ignore[import-not-found]  # noqa: E402
    LONG_TERM_BOUNDARY_MONTHS,
    MEDIUM_TERM_BOUNDARY_MONTHS,
)
from Rebalancing.config import OTHERS_FUND_CAP_PCT  # type: ignore[import-not-found]  # noqa: E402
from Rebalancing.tables import cap_pct_for  # type: ignore[import-not-found]  # noqa: E402


# Synthetic practical-allocation rows the additional-investment engine never
# buys into: ELSS (SEBI 3-yr lock-in) and non-MF equity (direct stocks / PMS).
# This engine is MF-BUY-only, so they are handed to the engine as
# ``exclude_subgroups`` — NOT hand-dropped from the subgroup list. The engine
# zero-weights them and renormalises the split onto the remaining (eligible)
# subgroups.
_EXCLUDE_SUBGROUPS = frozenset({"tax_efficient_equities", "non_mf_equities"})


def _months_to(asof: date, goal_date: date) -> int:
    """Whole calendar months from ``asof`` to ``goal_date`` (day-of-month ignored)."""
    return (goal_date.year - asof.year) * 12 + (goal_date.month - asof.month)


async def _goal_funding_flags(user, asof: date) -> tuple[bool, bool]:
    """Return ``(short_term_fulfilled, medium_term_fulfilled)``.

    short_term_fulfilled is True when every goal under MEDIUM_TERM_BOUNDARY_MONTHS
    (36) is funded — or there are none. medium_term_fulfilled is True when every
    goal in [36, LONG_TERM_BOUNDARY_MONTHS=72) is funded — or there are none. The
    engine targets the nearest unfunded bucket (short → medium → long), so
    long-term needs no flag (it is always the fallback target).
    """
    snapshot = await run_cashflow_projection_for_user(user, anchor_date=asof)
    short_goals = [
        g
        for g in snapshot.goals
        if _months_to(asof, g.goal_date) < MEDIUM_TERM_BOUNDARY_MONTHS
    ]
    medium_goals = [
        g
        for g in snapshot.goals
        if MEDIUM_TERM_BOUNDARY_MONTHS
        <= _months_to(asof, g.goal_date)
        < LONG_TERM_BOUNDARY_MONTHS
    ]
    short_term_fulfilled = all(g.is_funded for g in short_goals)
    medium_term_fulfilled = all(g.is_funded for g in medium_goals)
    return short_term_fulfilled, medium_term_fulfilled


async def build_additional_investment_input_for_user(
    ctx: "TurnContext",
    allocation_output: Any,
    *,
    deploy_amount_inr: float,
    cadence: Cadence,
) -> tuple[AdditionalInvestmentInput, dict[str, Any]]:
    """Return ``(input, debug_dict)`` for ``run_additional_investment(...)``.

    Holding-agnostic: the only DB-backed collaborator is the cashflow projection
    (for the short/medium-term goal-funding flags); the BUY list is derived purely
    from the ranked-fund CSV, and the per-fund caps key off the deploy amount, so
    no corpus total is read.
    """
    user = ctx.user_ctx
    asof = date.today()

    # 1. Per-subgroup bucket amounts from the practical allocation — ALL rows pass
    #    through verbatim. The synthetic rows are dropped by the engine via
    #    exclude_subgroups (below), NOT hand-filtered here.
    subgroups = [
        SubgroupBucketAmounts(**row.model_dump())
        for row in allocation_output.aggregated_subgroups
    ]

    # 2. Goal-funding flags (nearest-unfunded-bucket targeting: short → medium → long).
    short_term_fulfilled, medium_term_fulfilled = await _goal_funding_flags(user, asof)

    # 3. Ranked funds: flatten the per-subgroup ranking, carrying scheme_code (T2).
    ranking = get_fund_ranking()
    ranked_funds = [
        RankedFund(
            asset_subgroup=rr.asset_subgroup,
            sub_category=rr.sub_category,
            rank=rr.rank,
            isin=rr.isin,
            scheme_code=rr.scheme_code,
            recommended_fund=rr.fund_name,
        )
        for rows in ranking.values()
        for rr in rows
    ]

    # 4. Per-subgroup caps over the ELIGIBLE rows (OTHERS default for unmapped
    #    subgroups). The cap is a percent of the DEPLOY amount, applied inside the
    #    engine — the builder reads no corpus total.
    cap_pct_by_subgroup = {
        s.subgroup: cap_pct_for(s.subgroup)
        for s in subgroups
        if s.subgroup not in _EXCLUDE_SUBGROUPS
    }

    inp = AdditionalInvestmentInput(
        deploy_amount_inr=deploy_amount_inr,
        cadence=cadence,
        subgroups=subgroups,
        short_term_fulfilled=short_term_fulfilled,
        medium_term_fulfilled=medium_term_fulfilled,
        ranked_funds=ranked_funds,
        cap_pct_by_subgroup=cap_pct_by_subgroup,
        default_cap_pct=OTHERS_FUND_CAP_PCT,
        exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
    )
    debug = {
        "subgroup_count": len(subgroups),
        "ranked_fund_count": len(ranked_funds),
        "short_term_fulfilled": short_term_fulfilled,
        "medium_term_fulfilled": medium_term_fulfilled,
        "exclude_subgroups": sorted(_EXCLUDE_SUBGROUPS),
    }
    return inp, debug
```

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -v
```

Expected: PASS — `6 passed` (`test_subgroups_map_all_rows_and_set_exclude`, `test_ranked_funds_flattened_with_scheme_code`, `test_short_term_unfunded_sets_flag_false`, `test_medium_term_unfunded_sets_flag_false`, `test_both_flags_true_when_funded_or_none`, `test_caps_use_cap_pct_for_and_others_default`).

- [ ] **Step 5: Commit**

```
git add app/domains/additional_investment/__init__.py \
        app/domains/additional_investment/services/__init__.py \
        app/domains/additional_investment/services/ainv_engine/__init__.py \
        app/domains/additional_investment/services/ainv_engine/input_builder.py \
        app/domains/additional_investment/services/ainv_engine/tests/__init__.py \
        app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py
git commit -m "additional_investment(3a-T3): engine input builder

Map TurnContext + practical allocation into AdditionalInvestmentInput for the
holding-agnostic engine: pass ALL subgroup rows through and let the engine drop
the two synthetic rows via exclude_subgroups; short_term_fulfilled +
medium_term_fulfilled via the goal-bucket rules; ranked funds flattened with
scheme_code; caps via cap_pct_for / OTHERS default keyed off the deploy amount
(no corpus total, no holdings fetch).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 4: Additional-investment engine service (orchestrator)

Mirrors `rebal_engine.service.compute_rebalancing_result`: prime the practical (holdings-aware) allocation, materialise the engine input, run the pure additional-investment engine on a worker thread, build the chat facts pack, and (Plan 3b) persist. This task ONLY adds the orchestrator + its outcome dataclass; the input builder (`build_additional_investment_input_for_user`), the facts-pack builder (`build_ainv_facts_pack`), and the package scaffolding are earlier 3a tasks consumed here.

One-line defaults stated here: the engine uses the **allocation I/O family** (money = `float`, wrappers `AdditionalInvestmentInput`/`AdditionalInvestmentOutput`, NOT Rebalancing's `Decimal`/`ComputeRequest`), and the **persist hook is left OFF in 3a** (`persist=False`; Plan 3b flips it and calls `persist_additional_investment_recommendation`).

- **Files:**
  - Create: `app/domains/additional_investment/services/ainv_engine/service.py`
  - Modify: none.
  - Test: `app/domains/additional_investment/services/ainv_engine/tests/test_service.py`

- **Interfaces:**
  - **Consumes (from earlier tasks / existing code):**
    - `compute_practical_allocation_result(user, user_question, *, chat_ctx) -> PracticalAllocationRunOutcome` — existing, `app/domains/practical_asset_allocation/services/paa_engine/service.py`; `.result` is the `PracticalAllocationOutput` carrying `.aggregated_subgroups`, `.blocking_message` is set on failure.
    - `async build_additional_investment_input_for_user(ctx, allocation_output, *, deploy_amount_inr: float, cadence: Cadence) -> tuple[AdditionalInvestmentInput, dict]` — Task: input_builder. Async (awaits the cashflow projection for the short/medium-term goal-funding flags; no holdings/NAV reads — the engine is holding-agnostic).
    - `build_ainv_facts_pack(output: AdditionalInvestmentOutput) -> dict` — Task: chat. Imported **lazily** to dodge the chat↔service circular import.
    - `run_additional_investment(inp: AdditionalInvestmentInput) -> AdditionalInvestmentOutput` — existing pure engine, `additional_investment.pipeline`.
    - `ensure_ai_agents_path`, `trace_line` — `app/domains/ai_engine/common.py`.
    - Package scaffolding: `ainv_engine/__init__.py` (docstring-only, must NOT eager-import chat) and `ainv_engine/tests/__init__.py`.
  - **Produces (later tasks rely on exactly these):**
    - `compute_additional_investment_result(user, user_question: str, *, db: AsyncSession, acting_user_id: uuid.UUID, chat_session_id: Optional[uuid.UUID], deploy_amount_inr: float, cadence: Cadence, chat_ctx: TurnContext, persist: bool = False) -> AdditionalInvestmentRunOutcome` — on the happy path the returned outcome has `output` + `facts_pack` set and `blocking_message=None`; when a pre-check fails (the practical allocation could not be produced / incomplete profile) it returns `output=None, facts_pack=None, blocking_message=<gate text>` instead of raising.
    - `@dataclass(frozen=True) AdditionalInvestmentRunOutcome(output: "AdditionalInvestmentOutput | None", facts_pack: dict | None, run_id: "uuid.UUID | None" = None, used_cached_allocation: bool = False, blocking_message: str | None = None)`

---

- [ ] **Step 1: Write the failing test** — e2e with plain fakes (no DB/LLM): patch the practical-allocation primer and the input builder, let the REAL pure engine run, stub the facts-pack builder. Assert the buys name funds and `deployed_inr + undeployed_inr == deploy_amount_inr`.

```python
"""Additional-investment orchestrator e2e: allocation primed → input built →
pure engine run → facts pack threaded through. Uses plain stand-ins/fakes — no
real DB, no LLM (mirrors rebal_engine/tests/test_service.py's patching style)."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()


def _fake_ainv_input(deploy_amount_inr: float):
    """A real AdditionalInvestmentInput the pure engine can run on: one subgroup
    with a long-term amount, one rank-1 fund, 100% cap — so the engine emits a
    single BUY that fully deploys and names the fund."""
    from additional_investment.models import (
        AdditionalInvestmentInput,
        Cadence,
        RankedFund,
        SubgroupBucketAmounts,
    )

    return AdditionalInvestmentInput(
        deploy_amount_inr=deploy_amount_inr,
        cadence=Cadence.LUMPSUM,
        subgroups=[
            SubgroupBucketAmounts(
                subgroup="low_beta_equities",
                emergency=0.0,
                short_term=0.0,
                medium_term=0.0,
                long_term=1_000_000.0,
                total=1_000_000.0,
            ),
        ],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                rank=1,
                isin="INF000000001",
                scheme_code="100001",
                recommended_fund="ICICI Bluechip",
            ),
        ],
        cap_pct_by_subgroup={"low_beta_equities": 100.0},
        default_cap_pct=100.0,
        rounding_multiple_inr=100,
        exclude_subgroups=set(),
    )


@pytest.mark.asyncio
async def test_e2e_buys_name_funds_and_deploy_accounting_balances():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    deploy = 100_000.0
    fake_input = _fake_ainv_input(deploy)
    fake_alloc = SimpleNamespace(
        result=SimpleNamespace(aggregated_subgroups=[]),
        blocking_message=None,
    )
    user = SimpleNamespace(id=uuid.uuid4())
    sentinel_facts = {"buys": [{"fund_name": "ICICI Bluechip"}]}

    with patch.object(
        svc,
        "compute_practical_allocation_result",
        new=AsyncMock(return_value=fake_alloc),
    ), patch.object(
        svc,
        "build_additional_investment_input_for_user",
        new=AsyncMock(return_value=(fake_input, {"debug": "fake"})),
    ), patch(
        "app.domains.additional_investment.services.ainv_engine.chat.build_ainv_facts_pack",
        return_value=sentinel_facts,
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "invest 1 lakh as lumpsum",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=deploy,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    # The real engine ran and named funds.
    assert outcome.output is not None
    assert len(outcome.output.buys) >= 1
    assert all(b.recommended_fund for b in outcome.output.buys)
    assert any(b.recommended_fund == "ICICI Bluechip" for b in outcome.output.buys)

    # Deploy accounting balances exactly: deployed + undeployed == deploy_amount.
    assert outcome.output.deploy_amount_inr == deploy
    assert outcome.output.deployed_inr + outcome.output.undeployed_inr == deploy

    # Persist OFF in Plan 3a → no run id; allocation isn't cached; facts pack threaded.
    assert outcome.run_id is None
    assert outcome.used_cached_allocation is False
    assert outcome.facts_pack == sentinel_facts
    # Happy path → no gate.
    assert outcome.blocking_message is None


@pytest.mark.asyncio
async def test_blocking_when_allocation_pre_check_fails():
    """When the practical allocation can't be produced, the orchestrator returns
    a blocking outcome (output/facts_pack None, blocking_message set) instead of
    raising — the chat handler relays it. The input builder / facts pack are
    never reached, so only the primer is patched."""
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    blocked_alloc = SimpleNamespace(
        result=None,
        blocking_message="I need your date of birth before I can plan this.",
    )
    user = SimpleNamespace(id=uuid.uuid4())

    with patch.object(
        svc,
        "compute_practical_allocation_result",
        new=AsyncMock(return_value=blocked_alloc),
    ):
        outcome = await svc.compute_additional_investment_result(
            user,
            "invest 1 lakh",
            db=SimpleNamespace(),
            acting_user_id=user.id,
            chat_session_id=None,
            deploy_amount_inr=100_000.0,
            cadence=Cadence.LUMPSUM,
            chat_ctx=SimpleNamespace(),
            persist=False,
        )

    assert outcome.output is None
    assert outcome.facts_pack is None
    assert outcome.run_id is None
    assert outcome.blocking_message == (
        "I need your date of birth before I can plan this."
    )
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service.py -v
```

Expected failure: the test errors on import of the not-yet-created module —
`ModuleNotFoundError: No module named 'app.domains.additional_investment.services.ainv_engine.service'` (1 failed/error).

- [ ] **Step 3: Write minimal implementation** — the COMPLETE `app/domains/additional_investment/services/ainv_engine/service.py`:

```python
"""Additional-investment orchestrator.

Mirrors ``rebal_engine.service.compute_rebalancing_result``: primes the
practical (holdings-aware) allocation, materialises the engine input, runs the
pure additional-investment engine on a worker thread, and builds the chat facts
pack. Persistence is gated behind ``persist`` (left OFF in Plan 3a; Plan 3b
flips the default and wires ``persist_additional_investment_recommendation``).

The additional-investment engine follows the *allocation* I/O family — money is
plain ``float`` and the wrappers are ``AdditionalInvestmentInput`` /
``AdditionalInvestmentOutput`` (NOT Rebalancing's ``Decimal`` +
``ComputeRequest``/``Response``) — there is no tax-lot arithmetic here.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

from app.domains.ai_engine.common import ensure_ai_agents_path, trace_line
from app.domains.additional_investment.services.ainv_engine.input_builder import (
    build_additional_investment_input_for_user,
)
from app.domains.practical_asset_allocation.services.paa_engine.service import (
    compute_practical_allocation_result,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
)
from additional_investment.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
    run_additional_investment,
)


_MSG_ENGINE_ERROR = (
    "I couldn't work out where to invest your money right now. Try again in a "
    "moment, and if it keeps happening let us know via the help option."
)


@dataclass(frozen=True)
class AdditionalInvestmentRunOutcome:
    """Immutable outcome of one additional-investment orchestration run.

    On the happy path ``output`` + ``facts_pack`` are set and
    ``blocking_message`` is None. When the input builder refuses (incomplete
    profile) or a pre-check fails, ``output`` and ``facts_pack`` are None and
    ``blocking_message`` carries the customer-facing gate text — the chat
    handler relays it via ``format_relay_or_canned`` instead of formatting a
    BUY list (so the orchestrator never raises on a gate).

    ``run_id`` is None whenever ``persist=False`` (the only mode in Plan 3a);
    Plan 3b flips ``persist`` and fills it with the persisted run's id.
    ``used_cached_allocation`` is always False today — the practical-allocation
    service recomputes fresh each call (no cache layer yet); the field exists
    for parity with ``RebalancingRunOutcome`` and future caching.
    """

    output: "AdditionalInvestmentOutput | None"
    facts_pack: dict | None
    run_id: "uuid.UUID | None" = None
    used_cached_allocation: bool = False
    blocking_message: str | None = None


async def compute_additional_investment_result(
    user,
    user_question: str,
    *,
    db: AsyncSession,
    acting_user_id: uuid.UUID,
    chat_session_id: Optional[uuid.UUID],
    deploy_amount_inr: float,
    cadence: Cadence,
    chat_ctx: "TurnContext",
    persist: bool = False,
) -> AdditionalInvestmentRunOutcome:
    """Prime allocation → build input → run engine → build facts pack.

    Mirrors ``compute_rebalancing_result``: the practical allocation is primed
    first (its ``aggregated_subgroups`` feed the per-subgroup deploy split; the
    per-fund caps key off the deploy amount, so no corpus total is read), the
    engine input is materialised from that allocation (holding-agnostic — no
    holdings fetch), the pure engine runs on a worker thread, and the chat facts
    pack is built.

    Persistence is gated behind ``persist`` (False in Plan 3a; Plan 3b flips the
    default and calls ``persist_additional_investment_recommendation``).
    """
    trace_line("module: additional_investment — start")

    paa_outcome = await compute_practical_allocation_result(
        user,
        user_question,
        chat_ctx=chat_ctx,
    )
    if paa_outcome.result is None:
        # Pre-check failed (practical allocation could not be produced /
        # incomplete profile): return a blocking outcome the chat handler relays
        # via format_relay_or_canned — never an engine BUY list, never a raise.
        return AdditionalInvestmentRunOutcome(
            output=None,
            facts_pack=None,
            blocking_message=paa_outcome.blocking_message or _MSG_ENGINE_ERROR,
        )

    inp, debug = await build_additional_investment_input_for_user(
        chat_ctx,
        paa_outcome.result,
        deploy_amount_inr=deploy_amount_inr,
        cadence=cadence,
    )
    trace_line(f"additional_investment input debug: {debug}")

    response: AdditionalInvestmentOutput = await asyncio.to_thread(
        run_additional_investment,
        inp,
    )

    # ``build_ainv_facts_pack`` lives in chat.py; import it lazily so loading the
    # orchestrator never eager-imports chat (chat.handle imports this function —
    # eager import would be a circular dependency via chat_core.turn_context).
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts_pack = build_ainv_facts_pack(response)

    run_id: Optional[uuid.UUID] = None
    if persist:
        # Plan 3a leaves this OFF (persist defaults False). Plan 3b flips the
        # default, implements the persist service, and wires the real
        # source_allocation_run_id (FK -> practical_asset_allocation_runs).
        from app.domains.additional_investment.services.additional_investment_persist_service import (
            persist_additional_investment_recommendation,
        )

        run_id = await persist_additional_investment_recommendation(
            db,
            acting_user_id,
            response,
            source_allocation_run_id=None,
            chat_session_id=chat_session_id,
            used_cached_allocation=False,
            user_question=user_question,
        )

    return AdditionalInvestmentRunOutcome(
        output=response,
        facts_pack=facts_pack,
        run_id=run_id,
        used_cached_allocation=False,
    )
```

- [ ] **Step 4: Run test to verify it passes** — same command:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service.py -v
```

Expected: `2 passed` — `test_e2e_buys_name_funds_and_deploy_accounting_balances` and `test_blocking_when_allocation_pre_check_fails`. In the e2e the patched primer + input builder feed a real `AdditionalInvestmentInput`; the real `run_additional_investment` deploys ₹100,000 into "ICICI Bluechip" (deployed=100,000, undeployed=0), and the stubbed `build_ainv_facts_pack` is threaded onto the outcome. In the blocking case the patched primer returns `result=None`, so the orchestrator returns `output=None, facts_pack=None, blocking_message=<gate>` without raising.

- [ ] **Step 5: Commit**

```
git add app/domains/additional_investment/services/ainv_engine/service.py \
        app/domains/additional_investment/services/ainv_engine/tests/test_service.py
git commit -m "$(cat <<'EOF'
feat(additional_investment): add ainv_engine orchestrator (Plan 3a Task 4)

compute_additional_investment_result mirrors compute_rebalancing_result:
prime practical allocation -> build input -> run pure engine on a worker
thread -> build facts pack. Adds AdditionalInvestmentRunOutcome. Persist
hook gated OFF (Plan 3b flips persist=True).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 5: Additional-investment chat handler + formatter body + facts pack

Wires the existing pure `additional_investment` engine into the chat path. On every turn of the `additional_investment` intent the handler runs the compute orchestrator (Task 3a-T4), builds a facts pack that **names the funds to buy**, and renders the answer through the SHARED question-aware formatter (`format_with_telemetry`) — falling back to a deterministic fund-naming brief if the LLM call fails. This is the additional-investment peer of `rebal_engine/chat.py`, but simpler: it is **BUY-only / write-once**, so there is no follow-up classifier, no counterfactual/override machinery, and the only `ActionMode` used is `compute`.

Decided defaults restated here (one line each, from the Phase 3 contract):
- I/O naming = `AdditionalInvestmentInput`/`Output` (allocation family), money is plain `float` — the facts pack casts to `float(...)` and formats with `format_inr_indian`, never `Decimal`.
- Engine subfolder is `ainv_engine` (because `ai_engine` is taken); chat is **not** re-exported from `ainv_engine/__init__.py` (circular-import risk) — it is imported directly only by tests here and lazily by the module-service in Task 3a-T4.

**Files:**
- **Create** `app/domains/additional_investment/services/ainv_engine/chat.py` — the deterministic `parse_deploy_request` parser, the `@register("additional_investment")` handler, `_AINV_FORMATTER_BODY`, `build_ainv_facts_pack`, `_format_or_fallback_ainv`, `_build_fallback_ainv_brief`.
- **Create (Test)** `app/domains/additional_investment/services/ainv_engine/tests/test_parse_deploy_request.py` — the `parse_deploy_request` input→expected table (Cycle 1).
- **Create (Test)** `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py` — register-side-effect lock + facts-pack/fallback/handler smoke tests, including the missing-amount clarify and blocking-message relay paths (plain fakes, no real DB/LLM).
- **Precondition (from earlier 3a tasks, already present):** `app/domains/additional_investment/__init__.py`, `.../services/__init__.py`, `.../services/ainv_engine/__init__.py` (docstring-only), `.../services/ainv_engine/tests/__init__.py`, and `.../services/ainv_engine/service.py` exporting `compute_additional_investment_result` + `AdditionalInvestmentRunOutcome` (Task 3a-T4). If `tests/__init__.py` is missing, create it empty.

**Interfaces:**
- **Consumes:**
  - `compute_additional_investment_result(user, user_question: str, *, db, acting_user_id, chat_session_id, deploy_amount_inr: float, cadence: Cadence, chat_ctx: TurnContext, persist: bool = False) -> AdditionalInvestmentRunOutcome` and `AdditionalInvestmentRunOutcome(output, facts_pack, run_id, used_cached_allocation, blocking_message)` — from `services/ainv_engine/service.py` (Task 3a-T4). On the happy path `output`/`facts_pack` are set and `blocking_message is None`; on a failed pre-check `output`/`facts_pack` are `None` and `blocking_message` carries the gate text.
  - `format_with_telemetry(*, ctx, facts_pack, body_prompt, module_name, action_mode, profile, build_fallback) -> str`, `format_relay_or_canned(*, ctx, module_name, message, action_mode="redirect") -> str`, `ActionMode` (`"compute"` is a member), `FormatterFailure` — from `app/domains/ai_engine/answer_formatter`.
  - `register`, `ChatHandlerResult(text, ...)` — from `app/domains/ai_engine/chat_dispatcher`; `TurnContext` — from `app/domains/ai_engine/turn_context`.
  - `ensure_ai_agents_path`, `format_inr_indian` — from `app/domains/ai_engine/common`.
  - `AdditionalInvestmentOutput`, `FundBuy`, `SubgroupTarget`, `Cadence`, `TargetBucket` — from `additional_investment.models` (via `sys.path` injection).
- **Produces (later tasks rely on these exact names):**
  - `parse_deploy_request(question: str) -> tuple[float | None, Cadence]` — deterministic deploy amount + cadence parser (Cycle 1); returns `(None, cadence)` when the question carries no amount.
  - `@register("additional_investment") async def handle(ctx: TurnContext) -> ChatHandlerResult` — the chat handler; importing the module registers it (`"additional_investment" in _HANDLERS`).
  - `_AINV_FORMATTER_BODY: str` — formatter body prompt (compute mode only).
  - `build_ainv_facts_pack(output: AdditionalInvestmentOutput) -> dict[str, Any]` — flat facts pack used by the formatter; imported **lazily** by `service.compute_additional_investment_result` via `from app.domains.additional_investment.services.ainv_engine.chat import build_ainv_facts_pack` (D6 — tests must patch this chat-module path, never a service-module attribute).
  - `_format_or_fallback_ainv(ctx: TurnContext, output: AdditionalInvestmentOutput) -> str` — called as `_format_or_fallback_ainv(ctx, outcome.output)`.
  - `_build_fallback_ainv_brief(output: AdditionalInvestmentOutput) -> str`.

---

#### Cycle 1 — `parse_deploy_request` (deterministic amount + cadence parser)

This is the FIRST cycle: a self-contained red→green for the deploy-request parser
that the handler (Cycle 2) wires in. It lands a minimal `chat.py` stub (imports +
the parser only); Cycle 2 Step 3 overwrites that stub with the COMPLETE module
(handler, facts pack, formatter body, fallback).

- [ ] **Step 1: Write the failing test** — create `app/domains/additional_investment/services/ainv_engine/tests/test_parse_deploy_request.py`:

```python
"""Input→expected table for parse_deploy_request — deterministic deploy amount +
cadence parsing (no LLM). Indian money expressions map to a float amount; SIP /
monthly phrasing selects Cadence.SIP_MONTHLY, else Cadence.LUMPSUM."""

from __future__ import annotations

import pytest

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from additional_investment.models import Cadence  # type: ignore[import-not-found]  # noqa: E402

from app.domains.additional_investment.services.ainv_engine.chat import (  # noqa: E402
    parse_deploy_request,
)


@pytest.mark.parametrize(
    "question, expected_amount",
    [
        ("₹5L", 500000.0),
        ("2 lakh", 200000.0),
        ("50k", 50000.0),
        ("1 crore", 10000000.0),
        ("Rs 2,00,000", 200000.0),
        ("invest 75000", 75000.0),
        ("where should I put my money?", None),
    ],
)
def test_parse_amount(question, expected_amount):
    amount, _cadence = parse_deploy_request(question)
    assert amount == expected_amount


@pytest.mark.parametrize(
    "question, expected_cadence",
    [
        ("invest 50k monthly", Cadence.SIP_MONTHLY),
        ("start a SIP of 10000", Cadence.SIP_MONTHLY),
        ("put 5000 per month", Cadence.SIP_MONTHLY),
        ("deploy 5000/month", Cadence.SIP_MONTHLY),
        ("invest 5L as lumpsum", Cadence.LUMPSUM),
        ("invest 5L", Cadence.LUMPSUM),
    ],
)
def test_parse_cadence(question, expected_cadence):
    _amount, cadence = parse_deploy_request(question)
    assert cadence == expected_cadence
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_parse_deploy_request.py -v
```

Expected failure: collection error `ModuleNotFoundError: No module named 'app.domains.additional_investment.services.ainv_engine.chat'` — `chat.py` does not exist yet.

- [ ] **Step 3: Write minimal implementation** — create `app/domains/additional_investment/services/ainv_engine/chat.py` as a minimal stub: the module docstring, the parser imports (`re` + `Cadence` via `ensure_ai_agents_path`), the unit/SIP/amount regex constants, and `parse_deploy_request`. (Cycle 2 Step 3 OVERWRITES this file with the COMPLETE module — handler, facts pack, formatter body, and fallback.)

```python
# app/domains/additional_investment/services/ainv_engine/chat.py
"""Single chat handler for the ADDITIONAL_INVESTMENT intent.

BUY-only / write-once flow: on every turn of this intent the handler parses the
deploy amount + cadence from the question, runs the compute orchestrator, builds
a facts pack that NAMES the funds to buy, and formats the answer through the
SHARED question-aware formatter. There is no follow-up classifier or
counterfactual/override machinery here — additional-investment runs are
write-once, so each turn simply recomputes the BUY list in `compute` mode. When
the question carries no amount the handler asks for it; when the orchestrator
returns a blocking_message it relays that instead. Peer of rebal_engine/chat.py,
minus the follow-up branches.

Not re-exported from ainv_engine/__init__.py (circular-import risk via
turn_context); imported lazily by the module-service to trigger @register.
"""

from __future__ import annotations

import re

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from additional_investment.models import Cadence  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Deploy-amount + cadence parser (deterministic; no LLM)
# ---------------------------------------------------------------------------

# Indian money shorthand multipliers (lower-cased suffix -> factor).
_UNIT_MULTIPLIER = {
    "k": 1_000.0,
    "thousand": 1_000.0,
    "l": 100_000.0,
    "lac": 100_000.0,
    "lacs": 100_000.0,
    "lakh": 100_000.0,
    "lakhs": 100_000.0,
    "cr": 10_000_000.0,
    "crore": 10_000_000.0,
    "crores": 10_000_000.0,
}

# A number (optional currency prefix, optional thousands separators / decimal)
# followed by an optional Indian unit suffix.
_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)?\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(crores?|cr|lakhs?|lacs?|l|thousand|k)?",
    re.IGNORECASE,
)

# Recurring / monthly phrasing selects a monthly SIP.
_SIP_RE = re.compile(
    r"\b(?:sip|monthly|per ?month|every month|each month)\b",
    re.IGNORECASE,
)


def parse_deploy_request(question: str) -> tuple[float | None, Cadence]:
    """Pull the deploy amount (INR float) and cadence from a free-text question.

    Deterministic, no LLM. The amount understands Indian money shorthand —
    thousands separators and the k/thousand, l/lac/lakh, cr/crore suffixes
    (e.g. "₹5L" -> 500000.0, "50k" -> 50000.0, "Rs 2,00,000" -> 200000.0,
    "invest 75000" -> 75000.0). Returns ``(None, cadence)`` when no amount is
    present. Cadence is ``Cadence.SIP_MONTHLY`` when the text reads like a
    recurring/monthly plan (or contains "/month"), else ``Cadence.LUMPSUM``.
    """
    cadence = (
        Cadence.SIP_MONTHLY
        if (_SIP_RE.search(question) or "/month" in question.lower())
        else Cadence.LUMPSUM
    )

    match = _AMOUNT_RE.search(question)
    if match is None:
        return None, cadence
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None, cadence
    value *= _UNIT_MULTIPLIER.get((match.group(2) or "").lower(), 1.0)
    return value, cadence
```

- [ ] **Step 4: Run test to verify it passes** — same command:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_parse_deploy_request.py -v
```

Expected: `13 passed` — every row of the amount table (7) and the cadence table (6).

#### Cycle 2 — chat handler, facts pack, formatter body, fallback

- [ ] **Step 1: Write the failing test** — create `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py`:

```python
"""Mirror of rebalancing's @register lock test, plus ainv handler/facts smoke tests.

BUY-only / write-once intent: the handler always recomputes in `compute` mode,
so the tests cover the register side-effect, the facts pack naming the buy funds,
the under-deploy nudge, SIP monthly amounts, the deterministic fund-naming
fallback, and the first-turn handler path. All stand-ins are plain fakes — no
real DB or LLM.
"""

from __future__ import annotations

import asyncio
import importlib
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.domains.ai_engine.chat_dispatcher import _HANDLERS
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.ai_engine.turn_context import TurnContext

ensure_ai_agents_path()

from additional_investment.models import (  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
    FundBuy,
    SubgroupTarget,
    TargetBucket,
)


def _output(
    *, cadence: Cadence = Cadence.LUMPSUM, undeployed: float = 0.0
) -> AdditionalInvestmentOutput:
    sip = cadence == Cadence.SIP_MONTHLY
    return AdditionalInvestmentOutput(
        target_bucket=TargetBucket.LONG_TERM,
        cadence=cadence,
        deploy_amount_inr=100000.0,
        deployed_inr=100000.0 - undeployed,
        undeployed_inr=undeployed,
        per_subgroup_target=[
            SubgroupTarget(subgroup="low_beta_equities", ratio=0.6, target_inr=60000.0),
            SubgroupTarget(subgroup="short_debt", ratio=0.4, target_inr=40000.0),
        ],
        buys=[
            FundBuy(
                recommended_fund="HDFC Top 100",
                isin="INF111",
                sub_category="Large Cap Fund",
                asset_subgroup="low_beta_equities",
                amount_inr=60000.0,
                monthly_amount_inr=5000.0 if sip else None,
                reason="Recommended fund for this category",
            ),
            FundBuy(
                recommended_fund="ICICI Ultra Short",
                isin="INF222",
                sub_category="Ultra Short Duration Fund",
                asset_subgroup="short_debt",
                amount_inr=40000.0,
                monthly_amount_inr=3000.0 if sip else None,
                reason="Recommended fund for this category",
            ),
        ],
    )


def _ctx(question: str = "invest 1 lakh", *, last_run=None) -> TurnContext:
    last_runs = {"additional_investment": last_run} if last_run else {}
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=date(1986, 1, 1), first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs=last_runs,
        active_intent="additional_investment",
        awaiting_save=False,
    )


def test_register_side_effect_for_additional_investment():
    """Importing ainv_engine.chat must register the 'additional_investment' handler."""
    import app.domains.additional_investment.services.ainv_engine.chat as mod

    importlib.reload(mod)
    assert (
        "additional_investment" in _HANDLERS
    ), "@register('additional_investment') side-effect missing"


def test_facts_pack_carries_buy_fund_names():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output())
    names = [b["recommended_fund"] for b in facts["buys"]]
    assert "HDFC Top 100" in names
    assert "ICICI Ultra Short" in names
    assert facts["deploy_amount_indian"]  # non-empty formatted string
    assert facts["cadence"] == "lumpsum"
    assert facts["target_bucket"] == "long_term"
    assert [t["subgroup"] for t in facts["per_subgroup_target"]] == [
        "low_beta_equities",
        "short_debt",
    ]
    # No leftover → no under-deploy nudge.
    assert facts["undeployed_inr"] == 0.0
    assert "under_deploy_note" not in facts


def test_facts_pack_adds_under_deploy_note_when_leftover():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(undeployed=15000.0))
    assert facts["undeployed_inr"] == 15000.0
    assert "under_deploy_note" in facts
    assert facts["under_deploy_note"]  # non-empty one-liner


def test_facts_pack_sip_carries_monthly_amounts():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        build_ainv_facts_pack,
    )

    facts = build_ainv_facts_pack(_output(cadence=Cadence.SIP_MONTHLY))
    assert facts["cadence"] == "sip_monthly"
    assert all(b["monthly_amount_inr"] is not None for b in facts["buys"])
    assert all(b["monthly_amount_indian"] for b in facts["buys"])


def test_build_fallback_brief_names_funds():
    from app.domains.additional_investment.services.ainv_engine.chat import (
        _build_fallback_ainv_brief,
    )

    brief = _build_fallback_ainv_brief(_output())
    assert "HDFC Top 100" in brief
    assert "ICICI Ultra Short" in brief


def test_handle_runs_engine_and_calls_formatter():
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=_output(),
        facts_pack={},
        run_id=uuid.uuid4(),
        used_cached_allocation=True,
    )
    with (
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.format_answer",
            new=AsyncMock(return_value="tailored ainv answer"),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx()))
    assert result.text == "tailored ainv answer"


def test_handle_falls_back_to_fund_naming_brief_on_formatter_failure():
    from app.domains.ai_engine.answer_formatter import FormatterFailure
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=_output(),
        facts_pack={},
        run_id=None,
        used_cached_allocation=False,
    )
    with (
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.format_answer",
            new=AsyncMock(side_effect=FormatterFailure("api_down")),
        ),
        patch(
            "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx()))
    # Formatter failed → deterministic fallback brief, which names the buy funds.
    assert "HDFC Top 100" in result.text
    assert "ICICI Ultra Short" in result.text


def test_handle_asks_for_amount_when_question_has_no_number():
    """No parseable amount → clarify reply via the shared relay; the orchestrator
    is never called."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat

    compute = AsyncMock()
    with (
        patch.object(ainv_chat, "compute_additional_investment_result", new=compute),
        patch.object(
            ainv_chat,
            "format_relay_or_canned",
            new=AsyncMock(return_value="how much, and lumpsum or SIP?"),
        ),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("where should I put my money?")))

    assert result.text == "how much, and lumpsum or SIP?"
    compute.assert_not_awaited()


def test_handle_relays_blocking_message_instead_of_buys():
    """A blocking outcome (output None, blocking_message set) is relayed via the
    shared relay rather than formatted as a BUY list."""
    from app.domains.additional_investment.services.ainv_engine import chat as ainv_chat
    from app.domains.additional_investment.services.ainv_engine.service import (
        AdditionalInvestmentRunOutcome,
    )

    outcome = AdditionalInvestmentRunOutcome(
        output=None,
        facts_pack=None,
        blocking_message="I need your date of birth before I can plan this.",
    )
    relay = AsyncMock(return_value="relayed gate text")
    with (
        patch.object(
            ainv_chat,
            "compute_additional_investment_result",
            new=AsyncMock(return_value=outcome),
        ),
        patch.object(ainv_chat, "format_relay_or_canned", new=relay),
    ):
        result = asyncio.run(ainv_chat.handle(_ctx("invest 1 lakh")))

    assert result.text == "relayed gate text"
    relay.assert_awaited_once()
    assert relay.await_args.kwargs["module_name"] == "additional_investment"
    assert relay.await_args.kwargs["message"] == (
        "I need your date of birth before I can plan this."
    )
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -v
```

Expected failure: `chat.py` now exists (the Cycle 1 stub) but defines only `parse_deploy_request`, so the handler/facts symbols are missing — `test_register_side_effect_for_additional_investment` fails its `"additional_investment" in _HANDLERS` assertion (no `@register` yet), the `build_ainv_facts_pack` / `_build_fallback_ainv_brief` imports raise `ImportError`, and the `handle(...)` smoke tests raise `AttributeError`.

- [ ] **Step 3: Write minimal implementation** — create `app/domains/additional_investment/services/ainv_engine/chat.py`:

```python
"""Single chat handler for the ADDITIONAL_INVESTMENT intent.

BUY-only / write-once flow: on every turn of this intent the handler parses the
deploy amount + cadence from the question, runs the compute orchestrator, builds
a facts pack that NAMES the funds to buy, and formats the answer through the
SHARED question-aware formatter. There is no follow-up classifier or
counterfactual/override machinery here — additional-investment runs are
write-once, so each turn simply recomputes the BUY list in `compute` mode. When
the question carries no amount the handler asks for it; when the orchestrator
returns a blocking_message it relays that instead. Peer of rebal_engine/chat.py,
minus the follow-up branches.

Not re-exported from ainv_engine/__init__.py (circular-import risk via
turn_context); imported lazily by the module-service to trigger @register.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult, register
from app.domains.ai_engine.turn_context import TurnContext
from app.domains.ai_engine.answer_formatter import (
    format_relay_or_canned,
    format_with_telemetry,
)
from app.domains.ai_engine.common import ensure_ai_agents_path, format_inr_indian
from app.domains.additional_investment.services.ainv_engine.service import (
    compute_additional_investment_result,
)

ensure_ai_agents_path()

from additional_investment.models import (  # type: ignore[import-not-found]  # noqa: E402
    AdditionalInvestmentOutput,
    Cadence,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deploy-amount + cadence parser (deterministic; no LLM)
# ---------------------------------------------------------------------------

# Indian money shorthand multipliers (lower-cased suffix -> factor).
_UNIT_MULTIPLIER = {
    "k": 1_000.0,
    "thousand": 1_000.0,
    "l": 100_000.0,
    "lac": 100_000.0,
    "lacs": 100_000.0,
    "lakh": 100_000.0,
    "lakhs": 100_000.0,
    "cr": 10_000_000.0,
    "crore": 10_000_000.0,
    "crores": 10_000_000.0,
}

# A number (optional currency prefix, optional thousands separators / decimal)
# followed by an optional Indian unit suffix.
_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)?\s*"
    r"(\d[\d,]*(?:\.\d+)?)\s*"
    r"(crores?|cr|lakhs?|lacs?|l|thousand|k)?",
    re.IGNORECASE,
)

# Recurring / monthly phrasing selects a monthly SIP.
_SIP_RE = re.compile(
    r"\b(?:sip|monthly|per ?month|every month|each month)\b",
    re.IGNORECASE,
)


def parse_deploy_request(question: str) -> tuple[float | None, Cadence]:
    """Pull the deploy amount (INR float) and cadence from a free-text question.

    Deterministic, no LLM. The amount understands Indian money shorthand —
    thousands separators and the k/thousand, l/lac/lakh, cr/crore suffixes
    (e.g. "₹5L" -> 500000.0, "50k" -> 50000.0, "Rs 2,00,000" -> 200000.0,
    "invest 75000" -> 75000.0). Returns ``(None, cadence)`` when no amount is
    present. Cadence is ``Cadence.SIP_MONTHLY`` when the text reads like a
    recurring/monthly plan (or contains "/month"), else ``Cadence.LUMPSUM``.
    """
    cadence = (
        Cadence.SIP_MONTHLY
        if (_SIP_RE.search(question) or "/month" in question.lower())
        else Cadence.LUMPSUM
    )

    match = _AMOUNT_RE.search(question)
    if match is None:
        return None, cadence
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None, cadence
    value *= _UNIT_MULTIPLIER.get((match.group(2) or "").lower(), 1.0)
    return value, cadence


# ---------------------------------------------------------------------------
# Formatter body prompt (mirrors _REBAL_FORMATTER_BODY: documents the
# FACTS_PACK shape + per-ActionMode lead/length budget; no narrative prose)
# ---------------------------------------------------------------------------

_AINV_FORMATTER_BODY = """You are answering a customer's question about a fresh
additional-investment recommendation — NEW money being deployed (a one-time
lumpsum or a monthly SIP) into specific funds. This is BUY-only: nothing is ever
sold. The shared house-style rules above apply.

The FACTS_PACK has this shape (treat fields not present as unknown):

  deploy_amount_inr / deploy_amount_indian — total fresh money being deployed.
  cadence: "lumpsum" or "sip_monthly". lumpsum = a single one-time deployment;
           sip_monthly = the same plan repeats every month. When cadence is
           sip_monthly, frame amounts per-month (use each buy's
           monthly_amount_indian), not the one-time amount.
  target_bucket: "short_term", "medium_term", or "long_term" — the horizon the
           deploy amount was weighted toward, i.e. the customer's NEAREST UNFUNDED
           goal. "short_term" means a goal under ~3 years is still unfunded, so the
           money leans toward short-term subgroups; "medium_term" means short-term
           is covered but a ~3-6 year goal is unfunded; "long_term" means the short
           and medium goals are funded (or there are none) so the money builds the
           long-term subgroups. This is engine context — explain the WHY in plain
           English; never surface the raw label.
  undeployed_inr / undeployed_indian — money that could NOT be placed (per-fund
           caps bound, or a subgroup lacked eligible funds). 0 when fully placed.
  under_deploy_note — present ONLY when undeployed_inr > 0: a one-line plain-
           English nudge explaining the leftover and that the emergency reserve
           is always excluded from fresh deployment. Surface it when present.

  per_subgroup_target: list, one entry per subgroup the deploy was split into:
      subgroup    — internal engine grouping (e.g. "low_beta_equities"). DO NOT
                    surface this raw label; it is context only.
      ratio       — this subgroup's renormalised share of the deploy (0-1).
      target_inr  — the rupee amount targeted at this subgroup.

  buys: list of the specific funds to BUY — this is the substance of the answer:
      recommended_fund — the customer-facing scheme name (e.g. "HDFC Top 100").
                    Cite this VERBATIM; naming the funds is the point of the reply.
      sub_category  — SEBI category for context (e.g. "Large Cap Fund").
      amount_inr / amount_indian — the one-time amount to put into this fund.
      monthly_amount_inr / monthly_amount_indian — the per-month amount when
                    cadence is sip_monthly (null for lumpsum).

ACTION_MODE tells you the situation. ACTION_MODE is `compute` here — it is set
by the system on a fresh first-turn recommendation (it is not produced by a
classifier). Per-mode behavior:

  compute    — first-time additional-investment recommendation; introduce it
               shaped by the customer's question. Lead with the headline
               (deploy_amount_indian with the cadence framing — one-time for
               lumpsum, per-month for sip_monthly), then NAME the 1-3 biggest
               buys with their amounts (monthly_amount_indian when sip_monthly,
               else amount_indian), and give one plain-English line on why the
               split leans the way it does (derived from target_bucket). Always
               name at least the largest fund(s) — the customer asked where
               their money is going. If undeployed_inr > 0, close with the
               under_deploy_note. Length: 6-10 sentences (fewer when there is a
               single buy).
"""


# ---------------------------------------------------------------------------
# Facts pack
# ---------------------------------------------------------------------------


def build_ainv_facts_pack(output: AdditionalInvestmentOutput) -> dict[str, Any]:
    """Curated facts the formatter LLM may cite. Customer-tellable only — no ISIN.

    Flat dict: deploy accounting, cadence/target-bucket context, the per-subgroup
    targets, and the BUY list with each fund named. Money is cast to float and
    formatted with format_inr_indian (the allocation family is float, not
    Decimal). When undeployed_inr > 0 an `under_deploy_note` one-liner is added
    (O6: the emergency reserve is excluded and caps / fund-scarcity can leave a
    remainder).
    """
    deploy_inr = float(output.deploy_amount_inr)
    undeployed_inr = float(output.undeployed_inr)

    buys: list[dict[str, Any]] = []
    for b in output.buys:
        amount_inr = float(b.amount_inr)
        monthly_inr = (
            float(b.monthly_amount_inr) if b.monthly_amount_inr is not None else None
        )
        buys.append(
            {
                "recommended_fund": b.recommended_fund,
                "sub_category": b.sub_category,
                "amount_inr": amount_inr,
                "amount_indian": format_inr_indian(amount_inr),
                "monthly_amount_inr": monthly_inr,
                "monthly_amount_indian": (
                    format_inr_indian(monthly_inr) if monthly_inr is not None else None
                ),
            }
        )

    per_subgroup_target = [
        {
            "subgroup": t.subgroup,
            "ratio": float(t.ratio),
            "target_inr": float(t.target_inr),
        }
        for t in output.per_subgroup_target
    ]

    facts: dict[str, Any] = {
        "deploy_amount_inr": deploy_inr,
        "deploy_amount_indian": format_inr_indian(deploy_inr),
        "cadence": output.cadence.value,
        "target_bucket": output.target_bucket.value,
        "buys": buys,
        "per_subgroup_target": per_subgroup_target,
        "undeployed_inr": undeployed_inr,
        "undeployed_indian": format_inr_indian(undeployed_inr),
    }
    if undeployed_inr > 0:
        facts["under_deploy_note"] = (
            f"{format_inr_indian(undeployed_inr)} of your "
            f"{format_inr_indian(deploy_inr)} couldn't be placed — per-fund caps "
            "or a shortage of eligible funds left a remainder, and your emergency "
            "reserve is always kept out of fresh deployment."
        )
    return facts


# ---------------------------------------------------------------------------
# Deterministic fallback (used when the formatter LLM call fails)
# ---------------------------------------------------------------------------


def _build_fallback_ainv_brief(output: AdditionalInvestmentOutput) -> str:
    """Render the engine output as a chat-ready markdown brief that NAMES the
    funds to buy. BUY-only — no sells, no tax math. Used when the formatter
    fails so the customer always sees where their money is going."""
    deploy_indian = format_inr_indian(float(output.deploy_amount_inr))
    is_sip = output.cadence == Cadence.SIP_MONTHLY

    out: list[str] = []
    if is_sip:
        out.append(
            f"Here's how I'd put your **{deploy_indian}/month** SIP to work, "
            "split across these funds:"
        )
    else:
        out.append(
            f"Here's how I'd deploy your **{deploy_indian}** across these funds:"
        )
    out.append("")

    if is_sip:
        out.append("| Buy into | Monthly | One-time |")
        out.append("| --- | ---: | ---: |")
    else:
        out.append("| Buy into | Amount |")
        out.append("| --- | ---: |")

    for b in sorted(output.buys, key=lambda x: -float(x.amount_inr)):
        amount_indian = format_inr_indian(float(b.amount_inr))
        if is_sip:
            monthly_indian = (
                format_inr_indian(float(b.monthly_amount_inr))
                if b.monthly_amount_inr is not None
                else "—"
            )
            out.append(
                f"| {b.recommended_fund} | {monthly_indian} | {amount_indian} |"
            )
        else:
            out.append(f"| {b.recommended_fund} | {amount_indian} |")
    out.append("")

    if float(output.undeployed_inr) > 0:
        out.append(
            f"_{format_inr_indian(float(output.undeployed_inr))} couldn't be placed "
            "under the per-fund caps — small enough to top up later. Your emergency "
            "reserve is kept out of fresh deployment._"
        )
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Formatter wrapper
# ---------------------------------------------------------------------------


async def _format_or_fallback_ainv(
    ctx: TurnContext,
    output: AdditionalInvestmentOutput,
) -> str:
    """Run the SHARED formatter on the engine output; fall back to the
    deterministic fund-naming brief on FormatterFailure."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_ainv_facts_pack(output),
        body_prompt=_AINV_FORMATTER_BODY,
        module_name="additional_investment",
        action_mode="compute",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: _build_fallback_ainv_brief(output),
    )


# ---------------------------------------------------------------------------
# Public handler
# ---------------------------------------------------------------------------


_MSG_ASK_AMOUNT = (
    "Happy to help you put fresh money to work — how much would you like to "
    "invest, and should it be a one-time lumpsum or a monthly SIP?"
)


@register("additional_investment")
async def handle(ctx: TurnContext) -> ChatHandlerResult:
    """Parse the deploy request, compute the BUY list, and format it.

    BUY-only / write-once: there is no follow-up classifier, so every turn on
    this intent recomputes the deployment and re-formats it in `compute` mode.
    First the deploy amount + cadence are parsed from the question; a missing
    amount short-circuits to a clarify reply (amount + lumpsum/SIP). When the
    orchestrator returns a ``blocking_message`` (failed pre-check / incomplete
    profile) the handler relays that gate text via ``format_relay_or_canned``
    rather than formatting a BUY list. ChatHandlerResult has no ainv-specific id
    field, so only ``text`` is set; persistence/telemetry of the run is handled
    inside the orchestrator (Task 3a-T4) and the persist service (Task 3b).
    """
    amount, cadence = parse_deploy_request(ctx.user_question)
    if amount is None:
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=_MSG_ASK_AMOUNT,
        )
        return ChatHandlerResult(text=text)

    outcome = await compute_additional_investment_result(
        ctx.user_ctx,
        ctx.user_question,
        db=ctx.db,
        acting_user_id=ctx.effective_user_id,
        chat_session_id=ctx.session_id,
        deploy_amount_inr=amount,
        cadence=cadence,
        chat_ctx=ctx,
    )
    if outcome.blocking_message:
        text = await format_relay_or_canned(
            ctx=ctx,
            module_name="additional_investment",
            message=outcome.blocking_message,
        )
        return ChatHandlerResult(text=text)

    text = await _format_or_fallback_ainv(ctx, outcome.output)
    return ChatHandlerResult(text=text)
```

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -v
```

Expected: PASS — `test_register_side_effect_for_additional_investment` (handler registered), `test_facts_pack_carries_buy_fund_names`, `test_facts_pack_adds_under_deploy_note_when_leftover`, `test_facts_pack_sip_carries_monthly_amounts`, `test_build_fallback_brief_names_funds`, `test_handle_runs_engine_and_calls_formatter`, `test_handle_falls_back_to_fund_naming_brief_on_formatter_failure`, `test_handle_asks_for_amount_when_question_has_no_number`, and `test_handle_relays_blocking_message_instead_of_buys` all green (9 passed). The Cycle 1 parser suite stays green too.

- [ ] **Step 5: Commit**

```
git add app/domains/additional_investment/services/ainv_engine/chat.py \
        app/domains/additional_investment/services/ainv_engine/tests/test_parse_deploy_request.py \
        app/domains/additional_investment/services/ainv_engine/tests/test_chat.py
git commit -m "feat(additional_investment): chat handler + deploy parser + facts pack

Add parse_deploy_request (deterministic Indian-money amount + lumpsum/SIP
cadence parsing), the additional_investment chat handler (compute-only,
BUY-only) that parses the deploy request, asks for the amount when missing,
and relays a blocking_message gate when the orchestrator returns one;
build_ainv_facts_pack naming the funds to buy, the _AINV_FORMATTER_BODY
prompt, and a deterministic fund-naming fallback brief. Routes through the
shared format_with_telemetry formatter; mirrors rebal_engine/chat.py.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task 6: Module-service gateway (additional_investment)

The ONLY gateway to the additional-investment AI engine: `ChatBrain` reaches it via `flow_additional_investment` and calls `run(turn, ctx, prior)`. It lazy-imports the chat handler for its `@register("additional_investment")` side-effect, dispatches on the intent, and wraps the returned `ChatHandlerResult` into the uniform `ModuleOutput`. Mirrors `rebalancing_module_service.run` in shape; like `cashflow_module_service.run` it maps only the fields the ainv `ChatHandlerResult` actually populates.

**DECIDED DEFAULT (state at the call site):** `persisted_run_id` stays `None` in 3a — the `AdditionalInvestmentRun` write surface is a 3b deliverable, so this gateway maps only `text` (the one `ChatHandlerResult` field ainv populates today) plus `chart_payloads`, a not-yet-populated forward hook carried through verbatim for when the engine starts emitting charts. `snapshot_id` is NOT mapped — the additional-investment engine produces no snapshot (Finding 6) — and `rebalancing_recommendation_id` is rebalancing-only and is likewise deliberately NOT mapped.

**Files:**
- Create: `app/domains/additional_investment/__init__.py` (empty package marker; idempotent if a scaffolding task already added it)
- Create: `app/domains/additional_investment/services/__init__.py` (empty package marker)
- Create: `app/domains/additional_investment/services/additional_investment_module_service.py`
- Create: `app/domains/additional_investment/services/tests/__init__.py` (empty package marker)
- Test: `app/domains/additional_investment/services/tests/test_additional_investment_module_service.py`

**Interfaces:**
- Consumes:
  - `ModuleOutput` — `from app.domains.ai_engine.types import ModuleOutput` (fields: `text`, `payload`, `persisted_run_id`, `snapshot_id`, `rebalancing_recommendation_id`, `chart_payloads`, `side_effects`).
  - `dispatch_chat`, `ChatHandlerResult`, `register` — `app/domains/ai_engine/chat_dispatcher.py` (`async def dispatch_chat(intent, turn_context) -> ChatHandlerResult`; `ChatHandlerResult(text, snapshot_id, asset_allocation_run_id, rebalancing_recommendation_id, rebalancing_run_id, rebalancing_response, chart_payloads)`).
  - `from app.domains.additional_investment.services.ainv_engine import chat` — the lazy `@register` side-effect import; the real module is delivered by the chat-handler task (`@register("additional_investment") async def handle(ctx: TurnContext) -> ChatHandlerResult`).
- Produces (later tasks rely on these EXACT symbols):
  - `async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput` in `app/domains/additional_investment/services/additional_investment_module_service.py` — called by `flow_additional_investment` (`app/domains/ai_engine/services/flow.py`).
  - `__all__ = ["run"]`.
  - The package markers `app.domains.additional_investment` and `app.domains.additional_investment.services`.

---

- [ ] **Step 1: Write the failing test** — create `app/domains/additional_investment/services/tests/test_additional_investment_module_service.py` with the full code below. It registers a plain in-registry fake handler (no LLM) and stubs the not-yet-written `ainv_engine.chat` import via `sys.modules`, so the task is testable in isolation. Asserts `run(...)` returns a `ModuleOutput` whose `.text` is non-empty and whose other fields are wrapped verbatim, with `persisted_run_id is None` (3a).

```python
"""additional_investment_module_service.run: wraps the chat handler result into a ModuleOutput.

Mirrors the rebalancing/cashflow module-service contract: lazy-import the chat
module for its @register side-effect, dispatch on the intent, wrap the
ChatHandlerResult into a ModuleOutput. Uses a plain in-registry fake handler
(no real LLM, no DB) and stubs the ainv_engine.chat import via sys.modules so
this task is testable before chat.py exists.
"""

from __future__ import annotations

import asyncio
import sys
import types as _types
import unittest
import uuid
from unittest.mock import MagicMock

from app.domains.ai_engine import chat_dispatcher as cd
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult
from app.domains.ai_engine.types import ModuleOutput

_AINV_PKG = "app.domains.additional_investment.services.ainv_engine"
_AINV_CHAT = _AINV_PKG + ".chat"


class AdditionalInvestmentModuleServiceTests(unittest.TestCase):
    def setUp(self):
        # run() lazy-imports ainv_engine.chat for its @register side-effect.
        # chat.py is a later task and is LLM-backed, so stub the package +
        # submodule in sys.modules. We register our own fake handler below, so
        # the real registration side-effect is irrelevant to this unit test.
        self._saved = {name: sys.modules.get(name) for name in (_AINV_PKG, _AINV_CHAT)}
        fake_pkg = _types.ModuleType(_AINV_PKG)
        fake_chat = _types.ModuleType(_AINV_CHAT)
        fake_pkg.chat = fake_chat
        sys.modules[_AINV_PKG] = fake_pkg
        sys.modules[_AINV_CHAT] = fake_chat
        cd._HANDLERS.pop("additional_investment", None)

    def tearDown(self):
        for name, mod in self._saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
        cd._HANDLERS.pop("additional_investment", None)

    def test_run_wraps_chat_result_into_module_output(self):
        from app.domains.additional_investment.services import (
            additional_investment_module_service as svc,
        )

        snap = uuid.uuid4()
        charts = [{"kind": "fund_buys", "series": []}]
        fake_result = ChatHandlerResult(
            text="Deploy ₹1,00,000: buy Fund A (₹60,000) and Fund B (₹40,000).",
            snapshot_id=snap,
            chart_payloads=charts,
        )

        captured = {}

        @cd.register("additional_investment")
        async def fake_handler(turn_context):
            captured["ctx"] = turn_context
            return fake_result

        ctx = MagicMock()
        out = asyncio.run(svc.run(MagicMock(), ctx, {}))

        # Routed to the additional_investment handler with the turn's ctx.
        self.assertIs(captured["ctx"], ctx)

        # Wrapped verbatim into a ModuleOutput.
        self.assertIsInstance(out, ModuleOutput)
        self.assertTrue(out.text)  # non-empty
        self.assertEqual(out.text, fake_result.text)
        self.assertIs(out.payload, fake_result)
        # Finding 6: the gateway deliberately does NOT map snapshot_id (the
        # additional-investment engine produces no snapshot), so it stays None
        # even though the handler result carries one.
        self.assertIsNone(out.snapshot_id)
        self.assertEqual(out.chart_payloads, charts)
        # 3a: no AdditionalInvestmentRun persisted yet.
        self.assertIsNone(out.persisted_run_id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/tests/test_additional_investment_module_service.py -v
```

Expected: collection/import error — `ModuleNotFoundError: No module named 'app.domains.additional_investment'` (the domain package and the module-service file do not exist yet).

- [ ] **Step 3: Write minimal implementation** — create the package markers and the gateway with the COMPLETE code below.

`app/domains/additional_investment/__init__.py` (empty file):

```python
```

`app/domains/additional_investment/services/__init__.py` (empty file):

```python
```

`app/domains/additional_investment/services/tests/__init__.py` (empty file):

```python
```

`app/domains/additional_investment/services/additional_investment_module_service.py`:

```python
"""Additional-investment AI module — the ONLY gateway to the
additional-investment engine.

Per the AI-module rule, nothing else in the codebase imports the
additional-investment engine — they call ``run(turn, ctx, prior)`` here.

Sequence position: the brain runs this AFTER ``practical_asset_allocation`` for
the "additional_investment" intent (``flow_additional_investment`` =
[asset_allocation, additional_investment]). The additional-investment engine
runs the practical (holdings-aware) allocation as its own first step and lifts
those per-subgroup targets onto the deploy plan, so it does not consume
``prior`` — the upstream module's payload is informational only.
"""

from __future__ import annotations

from app.domains.ai_engine.types import ModuleOutput


async def run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput:
    """Run the additional-investment engine via the registered chat handler.

    The handler self-registers under ``"additional_investment"`` in the chat
    dispatcher when its ``chat.py`` is imported. We do that lazy import here so
    the side-effect lands before ``dispatch_chat`` looks it up.
    """
    # Lazy imports for the @register side-effect and to keep brain startup light.
    from app.domains.additional_investment.services.ainv_engine import chat as _ainv_chat  # noqa: F401
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat

    result = await dispatch_chat("additional_investment", ctx)
    return ModuleOutput(
        text=result.text,
        payload=result,  # the structured additional-investment chat result for the HTTP layer
        persisted_run_id=None,  # 3a: AdditionalInvestmentRun persistence lands in 3b
        chart_payloads=result.chart_payloads,  # forward hook: the ainv engine does not populate this yet
    )


__all__ = ["run"]
```

- [ ] **Step 4: Run test to verify it passes** — from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

```
.venv-mac/bin/python -m pytest app/domains/additional_investment/services/tests/test_additional_investment_module_service.py -v
```

Expected: `1 passed` — `test_run_wraps_chat_result_into_module_output PASSED`.

- [ ] **Step 5: Commit**

```
git add app/domains/additional_investment/__init__.py \
        app/domains/additional_investment/services/__init__.py \
        app/domains/additional_investment/services/additional_investment_module_service.py \
        app/domains/additional_investment/services/tests/__init__.py \
        app/domains/additional_investment/services/tests/test_additional_investment_module_service.py
git commit -m "$(cat <<'EOF'
feat(additional_investment): add module-service gateway (Plan 3a T6)

Mirror rebalancing_module_service.run: lazy-import ainv_engine.chat for the
@register side-effect, dispatch the "additional_investment" intent, and wrap
the ChatHandlerResult into a ModuleOutput (persisted_run_id None until 3b).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 7: additional_investment Flow Wiring + FLOWS Row

Add `flow_additional_investment` to the ai_engine flow table so the `additional_investment` intent (already emitted by the intent classifier in Phase 2) routes through a real flow. It mirrors `flow_rebalancing` exactly: lazily run `practical_asset_allocation` first, then hand that allocation forward into the additional_investment domain via `prior[AIModule.ASSET_ALLOCATION.value]` — the additional_investment domain reads its target from that slot rather than recomputing PAA. All domain imports stay function-local (lazy) so importing `flow.py` never pulls a heavy agent package at app boot. `brain.py` is NOT changed — `_flow_for` already does `FLOWS.get(intent.name, flow_general_chat)`, so adding the row is sufficient.

Decided defaults applied here (one-liners): the flow mirrors `flow_rebalancing` (run PAA once, pass it forward — do NOT recompute PAA twice); the allocation is passed into the additional_investment slot as `{AIModule.ASSET_ALLOCATION.value: paa}`; the additional_investment domain owns the final reply (its `ModuleOutput` is returned unchanged).

**Files:**
- Modify: `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/domains/ai_engine/services/flow.py:101` (add `flow_additional_investment` after `flow_general_chat`) and `app/domains/ai_engine/services/flow.py:116` (add the `"additional_investment"` row to the `FLOWS` dict).
- Test (Create): `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/domains/ai_engine/tests/test_flow_additional_investment.py`

**Interfaces:**
- Consumes (from earlier 3a tasks / existing code):
  - `app.domains.additional_investment.services.additional_investment_module_service.run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput` — the additional_investment domain gateway (authored in an earlier 3a task).
  - `app.domains.practical_asset_allocation.services.practical_asset_allocation_module_service.run(turn, ctx, prior: dict[str, ModuleOutput]) -> ModuleOutput` — existing PAA gateway (already used by `flow_rebalancing`).
  - `app.domains.ai_engine.types.AIModule`, `app.domains.ai_engine.types.ModuleOutput`.
- Produces (later tasks / the brain rely on these exact names):
  - `async def flow_additional_investment(turn, ctx) -> ModuleOutput` in `app/domains/ai_engine/services/flow.py`.
  - `FLOWS["additional_investment"] = flow_additional_investment`.

---

- [ ] **Step 1: Write the failing test**

  Create `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/domains/ai_engine/tests/test_flow_additional_investment.py`. The flow's domain imports are function-local (lazy), so the test injects fake module-service modules into `sys.modules` (each exposing an async `run`) via `patch.dict`. This keeps the test self-contained — no real DB, no LLM, and no dependency on the additional_investment domain being importable — while still proving the wiring: PAA runs first with an empty prior, its `ModuleOutput` is forwarded into the `ASSET_ALLOCATION` slot, and the additional_investment domain's `ModuleOutput` is returned unchanged.

  ```python
  """flow_additional_investment: PAA first, then deploy fresh money into funds.

  Mirrors the flow_rebalancing recipe documented in flow.py: run the practical
  (holdings-aware) asset allocation first, hand it forward via
  prior[AIModule.ASSET_ALLOCATION.value], and let the additional_investment
  domain own the final reply. The flow's domain imports are lazy (function-local),
  so we inject fake module-service modules into sys.modules instead of patching a
  real symbol — that keeps this a pure wiring unit test with no DB/LLM and no
  dependency on the additional_investment domain being importable yet.
  """

  from __future__ import annotations

  import sys
  import types
  import unittest
  from unittest.mock import AsyncMock, MagicMock, patch

  from app.domains.ai_engine.services.flow import FLOWS, flow_additional_investment
  from app.domains.ai_engine.types import AIModule, ModuleOutput

  _PAA_MODULE = (
      "app.domains.practical_asset_allocation.services."
      "practical_asset_allocation_module_service"
  )
  _AINV_MODULE = (
      "app.domains.additional_investment.services."
      "additional_investment_module_service"
  )


  class AdditionalInvestmentFlowTests(unittest.IsolatedAsyncioTestCase):
      def test_flows_row_points_at_flow_additional_investment(self):
          self.assertIs(FLOWS["additional_investment"], flow_additional_investment)

      async def test_flow_runs_paa_then_additional_investment(self):
          turn = MagicMock(name="turn")
          ctx = MagicMock(name="ctx")

          paa_output = ModuleOutput(payload="PAA_TARGET")
          ainv_output = ModuleOutput(text="Buy fund X with your fresh deploy amount.")

          paa_run = AsyncMock(return_value=paa_output)
          ainv_run = AsyncMock(return_value=ainv_output)

          fake_paa_mod = types.ModuleType(_PAA_MODULE)
          fake_paa_mod.run = paa_run
          fake_ainv_mod = types.ModuleType(_AINV_MODULE)
          fake_ainv_mod.run = ainv_run

          with patch.dict(
              sys.modules,
              {_PAA_MODULE: fake_paa_mod, _AINV_MODULE: fake_ainv_mod},
          ):
              result = await flow_additional_investment(turn, ctx)

          # additional_investment owns the reply — returned unchanged.
          self.assertIs(result, ainv_output)
          # PAA ran first with an empty prior dict.
          paa_run.assert_awaited_once_with(turn, ctx, {})
          # The allocation was passed forward into the asset-allocation slot
          # (PAA is run once; not recomputed by the additional_investment domain).
          ainv_run.assert_awaited_once_with(
              turn, ctx, {AIModule.ASSET_ALLOCATION.value: paa_output}
          )


  if __name__ == "__main__":
      unittest.main()
  ```

- [ ] **Step 2: Run test to verify it fails**

  Run from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

  ```bash
  .venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_flow_additional_investment.py -v
  ```

  Expected: collection error — `ImportError: cannot import name 'flow_additional_investment' from 'app.domains.ai_engine.services.flow'` (the symbol and the `FLOWS` row do not exist yet).

- [ ] **Step 3: Write minimal implementation**

  Edit `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend/app/domains/ai_engine/services/flow.py`.

  (a) Add the new flow function immediately after `flow_general_chat` (after line 100, before the `# ---` switch comment block at line 103). Imports are function-local/lazy, mirroring `flow_rebalancing`:

  ```python
  async def flow_additional_investment(turn, ctx) -> ModuleOutput:
      # Deploy fresh money (lumpsum/SIP) into specific funds, holdings-aware.
      # Like rebalancing, this needs the practical (holdings-aware) asset
      # allocation first; that allocation takes the asset-allocation slot so the
      # additional_investment domain reads its target from
      # ``prior[ASSET_ALLOCATION]`` rather than recomputing PAA itself.
      from app.domains.additional_investment.services.additional_investment_module_service import (
          run as run_additional_investment,
      )
      from app.domains.practical_asset_allocation.services.practical_asset_allocation_module_service import (
          run as run_practical_asset_allocation,
      )

      paa = await run_practical_asset_allocation(turn, ctx, {})
      return await run_additional_investment(
          turn, ctx, {AIModule.ASSET_ALLOCATION.value: paa}
      )
  ```

  (b) Add the row to the `FLOWS` dict (the block beginning at line 109). The dict becomes:

  ```python
  FLOWS = {
      "asset_allocation": flow_asset_allocation,
      "portfolio_query": flow_portfolio_query,
      "general_chat": flow_general_chat,
      "rebalancing": flow_rebalancing,
      "goal_planning": flow_goal_planning,
      "general_market_query": flow_market,
      "additional_investment": flow_additional_investment,
  }
  ```

  Do NOT touch `brain.py`: `_flow_for` already resolves new rows via `FLOWS.get(intent.name, flow_general_chat)`.

- [ ] **Step 4: Run test to verify it passes**

  Run from `/Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend`:

  ```bash
  .venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_flow_additional_investment.py -v
  ```

  Expected: `2 passed` — `test_flows_row_points_at_flow_additional_investment` and `test_flow_runs_paa_then_additional_investment`.

- [ ] **Step 5: Commit**

  ```bash
  git add app/domains/ai_engine/services/flow.py \
          app/domains/ai_engine/tests/test_flow_additional_investment.py
  git commit -m "feat(ai_engine): wire flow_additional_investment + FLOWS row

  Mirror flow_rebalancing: run practical_asset_allocation first, then deploy
  fresh money into funds via the additional_investment domain, passing the
  allocation forward in prior[ASSET_ALLOCATION]. Domain imports stay lazy;
  brain.py unchanged. The 'additional_investment' intent (emitted by the
  classifier in Phase 2) now reaches a real flow.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
  ```

## Self-Review

Stub: the orchestrator will run the consistency check across this plan (sequential task numbering, interface produce/consume alignment, and Global-Constraints adherence) after assembly.
