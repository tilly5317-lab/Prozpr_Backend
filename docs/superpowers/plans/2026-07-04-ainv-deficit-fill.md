# Additional-Investment Deficit-Fill (Lumpsum) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For lumpsum deployments, replace single-bucket targeting with deficit fill: run PAA on (actual holdings + lumpsum), deploy the money into the per-subgroup gaps between that ideal and current holdings. SIP path stays bit-for-bit identical.

**Architecture:** One new pure engine function (`compute_deficit_targets` + `dominant_bucket` in `ratio.py`), one new app-layer helper (`holdings_snapshot.py`), a `CorpusPin` threading through the PAA input builder, and mode-aware persist/facts-pack plumbing. Everything downstream (fund selection, caps, tables) is reused unchanged.

**Tech Stack:** Python 3.12 (`.venv-mac`), pydantic v2, SQLAlchemy async, pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-07-03-ainv-deficit-fill-design.md` — read it first; it carries every decision and edge case.

## Global Constraints

- Money is **float** everywhere in this domain — do NOT import `_to_decimal` (domain CLAUDE.md).
- Engine (`AI_Agents/src/additional_investment/`) stays **pure**: no LLM, no I/O, no cross-agent imports.
- **No new DB columns / migrations** — mode metadata goes into the existing `request_input` JSONB, MERGED over the engine-input dump.
- **Do NOT git commit** — the user commits themselves. Leave changes in the working tree (this overrides the plan template's commit steps; each task ends at "tests pass").
- Test runner: `.venv-mac/bin/python -m pytest` from `Prozpr_Backend/`.
- App-layer ainv tests use **fakes, not a real DB** (house style — see `ainv_engine/tests/test_persist.py`).
- Iteration direction is contractual: deficit math iterates IDEAL rows, `current.get(subgroup, 0.0)` — never the reverse.

---

### Task 1: Engine — `compute_deficit_targets` + `dominant_bucket`

**Files:**
- Modify: `AI_Agents/src/additional_investment/ratio.py`
- Test: `AI_Agents/src/additional_investment/Testing/test_ratio.py` (append)

**Interfaces:**
- Consumes: existing `SubgroupBucketAmounts`, `SubgroupTarget`, `TargetBucket` from `.models`.
- Produces: `compute_deficit_targets(subgroups: list[SubgroupBucketAmounts], current_by_subgroup: dict[str, float], deploy_amount: float, exclude_subgroups: set[str] = frozenset()) -> list[SubgroupTarget]` and `dominant_bucket(targets: list[SubgroupTarget], subgroups: list[SubgroupBucketAmounts]) -> TargetBucket`. Task 2's pipeline branch calls both.

- [ ] **Step 1: Write the failing tests** — append to `Testing/test_ratio.py`:

```python
# ---------------------------------------------------------------------------
# Deficit-fill (lumpsum) — compute_deficit_targets + dominant_bucket
# ---------------------------------------------------------------------------

import pytest

from additional_investment.ratio import compute_deficit_targets, dominant_bucket
from additional_investment.models import SubgroupBucketAmounts, TargetBucket


def _row(subgroup, short=0.0, medium=0.0, long=0.0, emergency=0.0):
    total = emergency + short + medium + long
    return SubgroupBucketAmounts(
        subgroup=subgroup, emergency=emergency, short_term=short,
        medium_term=medium, long_term=long, total=total,
    )


def test_deficit_clamps_overweight_and_scales_proportionally():
    subgroups = [
        _row("low_beta_equities", long=100000.0),    # ideal 1L
        _row("high_beta_equities", long=50000.0),    # ideal 50k
    ]
    current = {"low_beta_equities": 120000.0,        # overweight -> clamp to 0
               "high_beta_equities": 10000.0}        # gap 40k
    targets = compute_deficit_targets(subgroups, current, 20000.0)
    assert [t.subgroup for t in targets] == ["high_beta_equities"]
    assert targets[0].target_inr == pytest.approx(20000.0)
    assert targets[0].ratio == pytest.approx(1.0)


def test_deficit_ratio_identity_and_sum():
    subgroups = [_row("a", long=60000.0), _row("b", long=40000.0)]
    current = {"a": 30000.0, "b": 30000.0}           # gaps: 30k, 10k
    deploy = 20000.0
    targets = compute_deficit_targets(subgroups, current, deploy)
    assert sum(t.ratio for t in targets) == pytest.approx(1.0)
    for t in targets:
        assert t.target_inr == pytest.approx(t.ratio * deploy)
    by = {t.subgroup: t.target_inr for t in targets}
    assert by["a"] == pytest.approx(15000.0)          # 30k/40k of 20k
    assert by["b"] == pytest.approx(5000.0)           # 10k/40k of 20k


def test_deficit_held_subgroup_absent_from_ideal_is_ignored():
    # Held subgroup with NO ideal row: overweight by construction — no buy,
    # no KeyError (contract: iterate ideal rows, current.get(subgroup, 0.0)).
    subgroups = [_row("a", long=50000.0)]
    current = {"a": 10000.0, "gold_something_unmapped": 99999.0}
    targets = compute_deficit_targets(subgroups, current, 10000.0)
    assert [t.subgroup for t in targets] == ["a"]
    assert targets[0].target_inr == pytest.approx(10000.0)


def test_deficit_zero_holdings_spreads_full_ideal():
    subgroups = [_row("a", long=75000.0), _row("b", short=25000.0)]
    targets = compute_deficit_targets(subgroups, {}, 100000.0)
    by = {t.subgroup: t.target_inr for t in targets}
    assert by["a"] == pytest.approx(75000.0)
    assert by["b"] == pytest.approx(25000.0)


def test_deficit_excluded_subgroups_get_no_target():
    subgroups = [_row("a", long=50000.0), _row("tax_efficient_equities", long=50000.0)]
    targets = compute_deficit_targets(
        subgroups, {}, 10000.0, exclude_subgroups={"tax_efficient_equities"}
    )
    assert [t.subgroup for t in targets] == ["a"]


def test_deficit_all_at_ideal_falls_back_to_ideal_ratios():
    subgroups = [_row("a", long=60000.0), _row("b", long=40000.0)]
    current = {"a": 60000.0, "b": 40000.0}           # gaps all zero
    targets = compute_deficit_targets(subgroups, current, 10000.0)
    by = {t.subgroup: t.target_inr for t in targets}
    assert by["a"] == pytest.approx(6000.0)
    assert by["b"] == pytest.approx(4000.0)


def test_dominant_bucket_weights_deployed_money_by_horizon():
    subgroups = [
        _row("a", short=80000.0, long=20000.0),      # 80% short
        _row("b", long=50000.0),                     # 100% long
    ]
    targets = compute_deficit_targets(subgroups, {}, 150000.0)
    # a gets 100k (80k short-weighted), b gets 50k (long) + a's 20k long
    assert dominant_bucket(targets, subgroups) is TargetBucket.SHORT_TERM


def test_dominant_bucket_empty_targets_defaults_long_term():
    assert dominant_bucket([], []) is TargetBucket.LONG_TERM
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_ratio.py -q`
Expected: FAIL — `ImportError: cannot import name 'compute_deficit_targets'`

- [ ] **Step 3: Implement** — append to `AI_Agents/src/additional_investment/ratio.py`:

```python
def compute_deficit_targets(
    subgroups: list[SubgroupBucketAmounts],
    current_by_subgroup: dict[str, float],
    deploy_amount: float,
    exclude_subgroups: set[str] = frozenset(),
) -> list[SubgroupTarget]:
    """Deficit-fill split for a one-time lumpsum (holdings-aware, buy-only).

    ideal_i is each ELIGIBLE subgroup's ``total`` column (the post-investment
    practical allocation — the caller ran PAA at corpus + deploy_amount).
    deficit_i = max(0, ideal_i - current_i); the deploy amount is split across
    positive deficits proportionally. ratio_i = target_i / deploy_amount, so the
    legacy identity ``target_inr = ratio * deploy_amount`` holds in both modes.

    CONTRACT: iterate the IDEAL rows and look up current values with
    ``current_by_subgroup.get(subgroup, 0.0)`` — never the reverse. A held
    subgroup with no ideal row is thereby overweight by construction (no buy,
    no error); its value still shaped the caller's corpus total.

    Fallback: when every eligible deficit is zero (at/above ideal everywhere),
    distribute by the eligible ideal ratios instead — keeps building toward the
    ideal rather than deploying nothing.
    """
    eligible = [r for r in subgroups if r.subgroup not in exclude_subgroups]
    deficits = {
        r.subgroup: max(0.0, r.total - current_by_subgroup.get(r.subgroup, 0.0))
        for r in eligible
    }
    total_deficit = sum(deficits.values())
    if total_deficit <= 0:
        total_ideal = sum(max(r.total, 0.0) for r in eligible)
        if total_ideal <= 0:
            return []
        return [
            SubgroupTarget(
                subgroup=r.subgroup,
                ratio=max(r.total, 0.0) / total_ideal,
                target_inr=(max(r.total, 0.0) / total_ideal) * deploy_amount,
            )
            for r in eligible
            if max(r.total, 0.0) > 0
        ]
    targets: list[SubgroupTarget] = []
    for r in eligible:
        d = deficits[r.subgroup]
        if d <= 0:
            continue
        ratio = d / total_deficit
        targets.append(
            SubgroupTarget(subgroup=r.subgroup, ratio=ratio, target_inr=ratio * deploy_amount)
        )
    return targets


def dominant_bucket(
    targets: list[SubgroupTarget],
    subgroups: list[SubgroupBucketAmounts],
) -> TargetBucket:
    """Horizon that receives the most deployed money — the deficit-mode label.

    Each target's rupees are apportioned to short/medium/long by its subgroup's
    horizon composition (bucket column / total). Deterministic tie-break: the
    iteration order below means LONG_TERM wins ties (and the empty case).
    """
    rows = {r.subgroup: r for r in subgroups}
    order = (TargetBucket.LONG_TERM, TargetBucket.MEDIUM_TERM, TargetBucket.SHORT_TERM)
    scores = {b: 0.0 for b in order}
    for t in targets:
        row = rows.get(t.subgroup)
        if row is None or row.total <= 0:
            continue
        for b in order:
            scores[b] += t.target_inr * (max(getattr(row, b.value), 0.0) / row.total)
    best = order[0]
    for b in order:
        if scores[b] > scores[best]:
            best = b
    return best
```

Also update the module docstring's first line to: `"""Subgroup splits for additional investment. Pure, no state, no I/O."""` (it now hosts both the bucket-targeted and deficit-fill splits).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_ratio.py -q`
Expected: PASS (all — including the pre-existing `compute_targets` tests, untouched)

---

### Task 2: Engine — input field, defaults, pipeline branch

**Files:**
- Modify: `AI_Agents/src/additional_investment/models.py:57-83` (input model)
- Modify: `AI_Agents/src/additional_investment/pipeline.py`
- Test: `AI_Agents/src/additional_investment/Testing/test_pipeline.py` (append)

**Interfaces:**
- Consumes: `compute_deficit_targets`, `dominant_bucket` from Task 1.
- Produces: `AdditionalInvestmentInput.current_value_by_subgroup: Optional[dict[str, float]] = None`; `short_term_fulfilled`/`medium_term_fulfilled` now default `False`. Deficit mode triggers iff `cadence is Cadence.LUMPSUM and current_value_by_subgroup is not None`. Tasks 5-6 rely on these exact names.

- [ ] **Step 1: Write the failing tests** — append to `Testing/test_pipeline.py`:

```python
# ---------------------------------------------------------------------------
# Deficit mode (lumpsum + current_value_by_subgroup)
# ---------------------------------------------------------------------------

import pytest

from additional_investment.models import (
    AdditionalInvestmentInput, Cadence, RankedFund, SubgroupBucketAmounts, TargetBucket,
)
from additional_investment.pipeline import run_additional_investment


def _deficit_input(cadence=Cadence.LUMPSUM, current=None):
    return AdditionalInvestmentInput(
        deploy_amount_inr=100000.0,
        cadence=cadence,
        subgroups=[
            SubgroupBucketAmounts(subgroup="low_beta_equities", long_term=150000.0, total=150000.0),
            SubgroupBucketAmounts(subgroup="short_debt", short_term=50000.0, total=50000.0),
        ],
        ranked_funds=[
            RankedFund(asset_subgroup="low_beta_equities", sub_category="Large Cap Fund",
                       rank=1, isin="INF000000001", scheme_code="100001",
                       recommended_fund="Alpha Large Cap"),
            RankedFund(asset_subgroup="short_debt", sub_category="Short Duration Fund",
                       rank=1, isin="INF000000002", scheme_code="100002",
                       recommended_fund="Beta Short Debt"),
        ],
        cap_pct_by_subgroup={"low_beta_equities": 100.0, "short_debt": 100.0},
        current_value_by_subgroup=current,
    )


def test_lumpsum_with_current_map_uses_deficit_split():
    # low_beta gap = 150k-100k = 50k ; short_debt gap = 50k-0 = 50k -> 50/50 split
    out = run_additional_investment(
        _deficit_input(current={"low_beta_equities": 100000.0})
    )
    by = {t.subgroup: t.target_inr for t in out.per_subgroup_target}
    assert by["low_beta_equities"] == pytest.approx(50000.0)
    assert by["short_debt"] == pytest.approx(50000.0)


def test_deficit_target_bucket_is_dominant_horizon():
    # All money into short_debt (low_beta at ideal) -> label short_term.
    out = run_additional_investment(
        _deficit_input(current={"low_beta_equities": 150000.0})
    )
    assert out.target_bucket is TargetBucket.SHORT_TERM


def test_lumpsum_without_current_map_keeps_legacy_single_bucket():
    # No map -> legacy path: booleans default False -> SHORT_TERM bucket only.
    out = run_additional_investment(_deficit_input(current=None))
    assert out.target_bucket is TargetBucket.SHORT_TERM
    assert [t.subgroup for t in out.per_subgroup_target] == ["short_debt"]


def test_sip_ignores_current_map_entirely():
    # SIP + map: map must be ignored — legacy path, monthly framing intact.
    out = run_additional_investment(
        _deficit_input(cadence=Cadence.SIP_MONTHLY,
                       current={"low_beta_equities": 100000.0})
    )
    assert [t.subgroup for t in out.per_subgroup_target] == ["short_debt"]
    assert all(b.monthly_amount_inr is not None for b in out.buys)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_pipeline.py -q`
Expected: FAIL — `ValidationError` (unknown field `current_value_by_subgroup`)

- [ ] **Step 3: Implement the model change** — in `models.py`, inside `AdditionalInvestmentInput`, change the two booleans and add the new optional field directly after them:

```python
    # Goal-funding status (from the caller). Drives ONLY the legacy single-bucket
    # path (SIP, or lumpsum without a holdings map): the deposit targets the
    # nearest unfunded goal. Defaults exist because the deficit path ignores
    # them (deficit-fill needs no goal flags — the post-investment ideal already
    # encodes goal priority). long_term_fulfilled is intentionally not needed:
    # long-term is always the fallback target.
    short_term_fulfilled: bool = False
    medium_term_fulfilled: bool = False
    # Current holdings value per canonical asset subgroup (scheme_classification
    # vocabulary). When set AND cadence is LUMPSUM, the engine runs DEFICIT FILL:
    # deploy into max(0, ideal_total - current) gaps, proportionally. None (the
    # default) preserves legacy behavior exactly.
    current_value_by_subgroup: Optional[dict[str, float]] = None
```

- [ ] **Step 4: Implement the pipeline branch** — replace the `compute_targets` call block in `pipeline.py` (lines 20-23) with:

```python
    if inp.cadence is Cadence.LUMPSUM and inp.current_value_by_subgroup is not None:
        # Deficit fill (spec 2026-07-03): deploy into the gaps between the
        # post-investment ideal (caller ran PAA at corpus + deploy) and current
        # holdings. target_bucket becomes the dominant horizon of the deployed
        # money — a truthful label, not the split driver.
        targets = compute_deficit_targets(
            inp.subgroups, inp.current_value_by_subgroup,
            inp.deploy_amount_inr, inp.exclude_subgroups,
        )
        bucket = dominant_bucket(targets, inp.subgroups)
    else:
        bucket, targets = compute_targets(
            inp.subgroups, inp.short_term_fulfilled, inp.medium_term_fulfilled,
            inp.deploy_amount_inr, inp.exclude_subgroups,
        )
```

and extend the import: `from .ratio import compute_deficit_targets, compute_targets, dominant_bucket`.

- [ ] **Step 5: Run the FULL engine suite (SIP + legacy regression gate)**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/ -q`
Expected: PASS — every pre-existing test green (defaults keep legacy behavior), new tests green.

---

### Task 3: App — holdings snapshot helper

**Files:**
- Create: `app/domains/additional_investment/services/ainv_engine/holdings_snapshot.py`
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_holdings_snapshot.py`

**Interfaces:**
- Consumes: `PortfolioHolding`/`Portfolio` (portfolio domain models; cross-domain read follows the `get_fund_ranking` precedent), `classify_holding` from `app.domains.mutual_funds.services.scheme_classification`.
- Produces: `HoldingsSnapshot` (frozen dataclass: `by_subgroup: dict[str, float]`, `unknown_inr: float`; properties `total_inr`, `elss_inr`, `non_mf_equity_inr`), `aggregate_holdings(rows) -> HoldingsSnapshot` (pure), `load_holdings_snapshot(db, user_id) -> HoldingsSnapshot` (async). Task 6 calls `load_holdings_snapshot`.

- [ ] **Step 1: Write the failing tests** — create `tests/test_holdings_snapshot.py`:

```python
"""Pure aggregation tests for the holdings snapshot (fakes only, no DB —
house style; the async loader is a thin query wrapper exercised in service
tests via monkeypatch)."""

import pytest

from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    aggregate_holdings,
)


def test_classifies_mf_rows_to_subgroups_and_sums():
    rows = [
        # (instrument_type, current_value, sub_category, scheme_name)
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
        ("mutual_fund", 50000.0, "Large Cap Fund", "Beta Large Cap"),
        ("mutual_fund", 30000.0, "Small Cap Fund", "Gamma Small Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.by_subgroup["low_beta_equities"] == pytest.approx(150000.0)
    assert snap.by_subgroup["high_beta_equities"] == pytest.approx(30000.0)
    assert snap.unknown_inr == 0.0
    assert snap.total_inr == pytest.approx(180000.0)


def test_direct_stocks_bucket_to_non_mf_equities_not_unknown():
    rows = [
        ("equity", 200000.0, None, "RELIANCE"),
        ("stock", 50000.0, None, "TCS"),
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.non_mf_equity_inr == pytest.approx(250000.0)
    assert snap.unknown_inr == 0.0


def test_elss_lands_in_frozen_property():
    rows = [("mutual_fund", 60000.0, "ELSS Tax Saver Fund", "Tax Saver X")]
    snap = aggregate_holdings(rows)
    assert snap.elss_inr == pytest.approx(60000.0)


def test_unclassifiable_counts_in_total_but_not_map():
    rows = [
        ("mutual_fund", 40000.0, None, "Mystery Scheme 42"),
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.unknown_inr == pytest.approx(40000.0)
    assert "unknown" not in snap.by_subgroup
    assert snap.total_inr == pytest.approx(140000.0)


def test_zero_and_negative_values_skipped():
    snap = aggregate_holdings([
        ("mutual_fund", 0.0, "Large Cap Fund", "Alpha"),
        ("mutual_fund", -5.0, "Large Cap Fund", "Beta"),
    ])
    assert snap.total_inr == 0.0
    assert snap.by_subgroup == {}
```

NOTE for the implementer: the `low_beta_equities` / `high_beta_equities` labels
assert the CANONICAL mapping from `scheme_classification` ("Large Cap Fund" →
low_beta, "Small Cap Fund" → high_beta, "ELSS Tax Saver Fund" →
tax_efficient_equities). If an assertion fails, check the actual return of
`classify_holding("Large Cap Fund", ...)` and fix the TEST to the canonical
label — never hand-map in the helper.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_holdings_snapshot.py -q`
Expected: FAIL — `ModuleNotFoundError: ... holdings_snapshot`

- [ ] **Step 3: Implement** — create `holdings_snapshot.py`:

```python
"""Current-holdings snapshot aggregated to canonical asset subgroups.

Feeds the deficit-fill lumpsum path (spec 2026-07-03): the per-subgroup values
are the `current` side of ``deficit = ideal - current``, and the frozen values
(held ELSS, direct stocks) pin PAA's ``elss_corpus`` / ``non_mf_equity_corpus``
so locked money is not spread over buyable subgroups. Valuation source is the
precomputed ``PortfolioHolding.current_value`` (product decision 2026-07-03:
lighter than the transaction ledger; both sides of the deficit share this one
snapshot, so staleness cancels). Classification goes through the canonical
``classify_holding`` — the same vocabulary as PAA's subgroup rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.mutual_funds.services.scheme_classification import classify_holding
from app.domains.portfolio.models.portfolio import Portfolio, PortfolioHolding

# allocation_rollup convention: these instrument types are direct equity.
_EQUITY_INSTRUMENT_TYPES = frozenset({"equity", "stock", "share"})

_SUBGROUP_NON_MF_EQUITIES = "non_mf_equities"
_SUBGROUP_ELSS = "tax_efficient_equities"


@dataclass(frozen=True)
class HoldingsSnapshot:
    """Classified current-value totals. ``by_subgroup`` uses the canonical
    scheme_classification vocabulary (frozen subgroups included); unclassifiable
    value is carried only in ``unknown_inr`` (in the total, no gap row)."""

    by_subgroup: dict[str, float] = field(default_factory=dict)
    unknown_inr: float = 0.0

    @property
    def total_inr(self) -> float:
        return sum(self.by_subgroup.values()) + self.unknown_inr

    @property
    def elss_inr(self) -> float:
        return self.by_subgroup.get(_SUBGROUP_ELSS, 0.0)

    @property
    def non_mf_equity_inr(self) -> float:
        return self.by_subgroup.get(_SUBGROUP_NON_MF_EQUITIES, 0.0)


def aggregate_holdings(
    rows: list[tuple[str | None, float, str | None, str | None]],
) -> HoldingsSnapshot:
    """Pure aggregation over ``(instrument_type, current_value, sub_category,
    scheme_name)`` tuples. Direct-stock rows (instrument_type in the equity set)
    bucket to non_mf_equities WITHOUT classification; everything else classifies
    via ``classify_holding``; ``(None, None)`` results accrue to unknown_inr."""
    by_subgroup: dict[str, float] = {}
    unknown = 0.0
    for instrument_type, current_value, sub_category, scheme_name in rows:
        value = float(current_value or 0.0)
        if value <= 0:
            continue
        if (instrument_type or "").strip().lower() in _EQUITY_INSTRUMENT_TYPES:
            key: str | None = _SUBGROUP_NON_MF_EQUITIES
        else:
            _asset_class, key = classify_holding(sub_category, scheme_name)
        if key is None:
            unknown += value
            continue
        by_subgroup[key] = by_subgroup.get(key, 0.0) + value
    return HoldingsSnapshot(by_subgroup=by_subgroup, unknown_inr=unknown)


async def load_holdings_snapshot(
    db: AsyncSession, user_id: uuid.UUID
) -> HoldingsSnapshot:
    """Load + classify the user's holdings across their portfolios.

    ``fund_metadata`` joins on scheme_code (see PortfolioHolding relationship);
    rows without metadata fall back to the instrument name so name-based
    classification overrides still get a chance."""
    stmt = (
        select(PortfolioHolding)
        .join(Portfolio, PortfolioHolding.portfolio_id == Portfolio.id)
        .where(Portfolio.user_id == user_id)
        .options(selectinload(PortfolioHolding.fund_metadata))
    )
    holdings = (await db.execute(stmt)).scalars().all()
    rows = [
        (
            h.instrument_type,
            float(h.current_value or 0.0),
            h.fund_metadata.sub_category if h.fund_metadata else None,
            h.fund_metadata.scheme_name if h.fund_metadata else h.instrument_name,
        )
        for h in holdings
    ]
    return aggregate_holdings(rows)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_holdings_snapshot.py -q`
Expected: PASS

---

### Task 4: App — `CorpusPin` through the PAA input builder

**Files:**
- Modify: `app/domains/practical_asset_allocation/services/paa_engine/input_builder.py:40-60`
- Modify: `app/domains/practical_asset_allocation/services/paa_engine/service.py:49-66`
- Test: `app/domains/practical_asset_allocation/services/paa_engine/tests/test_input_builder.py` (append; if the file doesn't exist, create it beside the module's existing tests — check `ls app/domains/practical_asset_allocation/services/paa_engine/tests/` first and follow whatever naming exists)

**Interfaces:**
- Produces: `CorpusPin` frozen dataclass (`total_corpus: float, mf_corpus: float, non_mf_equity_corpus: float, elss_corpus: float`) exported from `paa_engine.input_builder`; `build_practical_allocation_input_for_user(ctx, corpus_pin=None)`; `compute_practical_allocation_result(user, user_question, *, chat_ctx, corpus_pin=None)`. Task 6 constructs the pin.

- [ ] **Step 1: Write the failing tests** — test at the CAPTURE SEAM: monkeypatch
`PracticalAllocationInput` inside the builder module with a kwargs recorder, so
no fully-valid allocation input has to be fabricated and no validation runs:

```python
import types

import pytest

from app.domains.practical_asset_allocation.services.paa_engine import input_builder


def _fake_base(total_corpus: float):
    """Every shared AllocationInput field present (the builder getattr's each of
    model_fields); values are irrelevant — PracticalAllocationInput is replaced
    by a kwargs recorder below, so nothing validates them."""
    from asset_allocation_pydantic.models import AllocationInput

    base = types.SimpleNamespace(**{k: 0.0 for k in AllocationInput.model_fields})
    base.total_corpus = total_corpus
    return base


@pytest.fixture
def captured(monkeypatch):
    """Replace PracticalAllocationInput (in the builder module) with a recorder.
    Sets attributes too, because the builder's debug dict reads them back."""
    seen = {}

    class _Capture:
        def __init__(self, **kw):
            seen.update(kw)
            self.__dict__.update(kw)

    monkeypatch.setattr(input_builder, "PracticalAllocationInput", _Capture)
    return seen


def test_corpus_pin_overrides_all_four_scalars(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(999999.0), {}),   # profile figure — must lose
    )
    pin = input_builder.CorpusPin(
        total_corpus=550000.0, mf_corpus=500000.0,
        non_mf_equity_corpus=30000.0, elss_corpus=20000.0,
    )
    input_builder.build_practical_allocation_input_for_user(None, corpus_pin=pin)
    assert captured["total_corpus"] == 550000.0
    assert captured["mf_corpus"] == 500000.0
    assert captured["non_mf_equity_corpus"] == 30000.0
    assert captured["elss_corpus"] == 20000.0


def test_no_pin_keeps_profile_defaults(monkeypatch, captured):
    monkeypatch.setattr(
        input_builder, "build_goal_allocation_input_for_user",
        lambda ctx: (_fake_base(999999.0), {}),
    )
    input_builder.build_practical_allocation_input_for_user(None)
    assert captured["total_corpus"] == 999999.0
    assert captured["mf_corpus"] == 999999.0
    assert captured["non_mf_equity_corpus"] == 0.0
    assert captured["elss_corpus"] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/practical_asset_allocation/services/paa_engine/tests/ -q -k corpus_pin`
Expected: FAIL — `AttributeError: ... no attribute 'CorpusPin'`

- [ ] **Step 3: Implement the builder change** — in `paa_engine/input_builder.py`, add above the builder:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusPin:
    """Explicit corpus override for the practical allocation (deficit-fill path).

    Pins the ideal to actual holdings + fresh money instead of the
    profile-declared corpus (spec 2026-07-03). Threaded as an explicit
    parameter — NOT the additional_cash_inr chat-override — so the what-if key
    never leaks into the normal flow."""

    total_corpus: float
    mf_corpus: float
    non_mf_equity_corpus: float
    elss_corpus: float
```

and change the builder signature + body:

```python
def build_practical_allocation_input_for_user(
    ctx: "TurnContext",
    corpus_pin: CorpusPin | None = None,
) -> tuple[PracticalAllocationInput, Dict[str, Any]]:
    """Return ``(PracticalAllocationInput, debug)`` for the User in ``ctx``."""
    base_input, debug = build_goal_allocation_input_for_user(ctx)

    shared = {k: getattr(base_input, k) for k in AllocationInput.model_fields}
    if corpus_pin is not None:
        shared["total_corpus"] = corpus_pin.total_corpus

    practical_input = PracticalAllocationInput(
        # Every shared AllocationInput field, verbatim (total_corpus included —
        # overridden above when a CorpusPin is supplied).
        **shared,
        # Practical-only scalars. Default source is the profile (stocks/ELSS 0 →
        # whole corpus treated as MF); a CorpusPin supplies holdings-derived
        # values for all four (deficit-fill lumpsum path).
        mf_corpus=(corpus_pin.mf_corpus if corpus_pin else base_input.total_corpus),
        non_mf_equity_corpus=(corpus_pin.non_mf_equity_corpus if corpus_pin else 0.0),
        elss_corpus=(corpus_pin.elss_corpus if corpus_pin else 0.0),
        max_non_mf_equity_pct_client_input=None,
    )
```

and update the debug dict lines that hardcode the old values to read from
`practical_input` (`"mf_corpus": practical_input.mf_corpus`, etc.).

- [ ] **Step 4: Implement the service pass-through** — in `paa_engine/service.py`:

```python
async def compute_practical_allocation_result(
    user,
    user_question: str,
    *,
    chat_ctx: "TurnContext",
    corpus_pin: "CorpusPin | None" = None,
) -> PracticalAllocationRunOutcome:
```

and pass it through: `build_practical_allocation_input_for_user(chat_ctx, corpus_pin=corpus_pin)`. Import `CorpusPin` under `TYPE_CHECKING` (or directly — it is a plain dataclass with no import cycle).

- [ ] **Step 5: Run the PAA + rebalancing suites (regression: default None must change nothing)**

Run: `.venv-mac/bin/python -m pytest app/domains/practical_asset_allocation app/domains/rebalancing -q`
Expected: PASS — all pre-existing tests green.

---

### Task 5: App — ainv input builder: deficit params, skip the projection

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/input_builder.py:86-156`
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py` (append)

**Interfaces:**
- Consumes: `current_value_by_subgroup` engine field (Task 2).
- Produces: `build_additional_investment_input_for_user(ctx, allocation_output, *, deploy_amount_inr, cadence, current_value_by_subgroup=None)`. When the map is provided AND cadence is LUMPSUM: `_goal_funding_flags` is NOT called, booleans stay `False`, map is attached to the input. Task 6 passes the map.

- [ ] **Step 1: Write the failing tests** — append to `test_input_builder.py`, following its existing fake-allocation/ctx fixtures (reuse whatever fake `allocation_output` object the file already builds; the new tests only need `aggregated_subgroups`):

```python
async def test_deficit_map_skips_cashflow_projection(monkeypatch, fake_ctx, fake_allocation):
    calls = []

    async def _boom(user, asof):
        calls.append(1)
        raise AssertionError("_goal_funding_flags must not run on the deficit path")

    monkeypatch.setattr(input_builder, "_goal_funding_flags", _boom)
    inp, debug = await input_builder.build_additional_investment_input_for_user(
        fake_ctx, fake_allocation,
        deploy_amount_inr=500000.0,
        cadence=Cadence.LUMPSUM,
        current_value_by_subgroup={"low_beta_equities": 100000.0},
    )
    assert calls == []
    assert inp.current_value_by_subgroup == {"low_beta_equities": 100000.0}
    assert inp.short_term_fulfilled is False and inp.medium_term_fulfilled is False
    assert debug["deployment_mode"] == "deficit_fill"


async def test_sip_never_attaches_the_map_and_still_runs_flags(monkeypatch, fake_ctx, fake_allocation):
    async def _flags(user, asof):
        return True, False

    monkeypatch.setattr(input_builder, "_goal_funding_flags", _flags)
    inp, debug = await input_builder.build_additional_investment_input_for_user(
        fake_ctx, fake_allocation,
        deploy_amount_inr=25000.0,
        cadence=Cadence.SIP_MONTHLY,
        current_value_by_subgroup={"low_beta_equities": 100000.0},  # must be dropped
    )
    assert inp.current_value_by_subgroup is None
    assert inp.short_term_fulfilled is True and inp.medium_term_fulfilled is False
```

(Adapt fixture names to the file's actual fixtures — read the file top before writing; the two behavioral assertions are the contract.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -q`
Expected: FAIL — unexpected keyword `current_value_by_subgroup`

- [ ] **Step 3: Implement** — change the builder signature and the flags step:

```python
async def build_additional_investment_input_for_user(
    ctx: "TurnContext",
    allocation_output: Any,
    *,
    deploy_amount_inr: float,
    cadence: Cadence,
    current_value_by_subgroup: dict[str, float] | None = None,
) -> tuple[AdditionalInvestmentInput, dict[str, Any]]:
```

Replace step 2 (the `_goal_funding_flags` call) with:

```python
    # 2. Goal-funding flags — LEGACY path only (SIP, or lumpsum without a
    #    holdings map). The deficit path skips the cashflow projection entirely:
    #    its only consumer here was the nearest-unfunded label, which deficit
    #    mode derives from the deployed money instead (spec 2026-07-03).
    deficit_mode = (
        cadence is Cadence.LUMPSUM and current_value_by_subgroup is not None
    )
    if deficit_mode:
        short_term_fulfilled, medium_term_fulfilled = False, False
    else:
        short_term_fulfilled, medium_term_fulfilled = await _goal_funding_flags(
            user, asof
        )
```

Attach to the input + debug:

```python
        current_value_by_subgroup=(current_value_by_subgroup if deficit_mode else None),
```
```python
        "deployment_mode": "deficit_fill" if deficit_mode else "single_bucket",
```

Also update the module docstring's "no holdings path at all" paragraph: it now
reads "no holdings path on the LEGACY (SIP / single-bucket) path; the lumpsum
deficit path receives a pre-aggregated ``current_value_by_subgroup`` map from
the service (see ``holdings_snapshot.py``) — the builder itself still reads no
DB ledger and no NAV."

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -q`
Expected: PASS

---

### Task 6: App — service wiring + persist merge + engine version

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py`
- Modify: `app/domains/additional_investment/services/additional_investment_persist_service.py:70-90`
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_service.py` (append), `tests/test_persist.py` (append)

**Interfaces:**
- Consumes: `load_holdings_snapshot` (Task 3), `CorpusPin` (Task 4), builder param (Task 5).
- Produces: `AdditionalInvestmentRunOutcome.deficit_facts: list[dict] | None` (rows `{subgroup, ideal_inr, current_inr, gap_inr, buy_inr}` — Task 7 consumes); `persist_additional_investment_recommendation(..., request_extras: Optional[dict[str, Any]] = None)`; `AINV_ENGINE_VERSION = "ainv-2.0.0"`.

- [ ] **Step 1: Write the failing persist test** — append to `test_persist.py` (reuse its `_FakeSession` / fake output helpers):

```python
async def test_request_extras_merge_over_engine_dump(monkeypatch):
    # arrange exactly as the existing lumpsum persist test does, then:
    run_id = await persist_additional_investment_recommendation(
        db, user_id, output,
        source_allocation_run_id=uuid.uuid4(),
        chat_session_id=uuid.uuid4(),
        used_cached_allocation=False,
        user_question="q",
        request=request,
        request_extras={"deployment_mode": "deficit_fill", "base_corpus_inr": 750000.0},
    )
    run = next(o for o in db.added if isinstance(o, AdditionalInvestmentRun))
    # engine-input keys preserved AND mode keys merged:
    assert run.request_input["deploy_amount_inr"] == request.deploy_amount_inr
    assert run.request_input["deployment_mode"] == "deficit_fill"
    assert run.request_input["base_corpus_inr"] == 750000.0
```

- [ ] **Step 2: Write the failing service tests** — append to `test_service.py` (follow its existing monkeypatch style for `compute_practical_allocation_result` / the engine):

```python
async def test_lumpsum_pins_corpus_and_passes_map(monkeypatch):
    seen = {}

    async def _fake_snapshot(db, user_id):
        return HoldingsSnapshot(
            by_subgroup={"low_beta_equities": 400000.0,
                         "tax_efficient_equities": 50000.0,
                         "non_mf_equities": 30000.0},
            unknown_inr=20000.0,
        )  # total 500k

    async def _fake_paa(user, q, *, chat_ctx, corpus_pin=None):
        seen["pin"] = corpus_pin
        return _fake_paa_outcome()  # reuse the file's existing fake outcome

    monkeypatch.setattr(service_mod, "load_holdings_snapshot", _fake_snapshot)
    monkeypatch.setattr(service_mod, "compute_practical_allocation_result", _fake_paa)
    # ... existing fakes for input builder + engine ...

    await compute_additional_investment_result(
        user, "invest 5L", db=db, acting_user_id=uid, chat_session_id=None,
        deploy_amount_inr=500000.0, cadence=Cadence.LUMPSUM, chat_ctx=ctx,
    )
    pin = seen["pin"]
    assert pin.total_corpus == pytest.approx(1000000.0)       # 500k + 5L
    assert pin.elss_corpus == pytest.approx(50000.0)
    assert pin.non_mf_equity_corpus == pytest.approx(30000.0)
    assert pin.mf_corpus == pytest.approx(970000.0)           # total - stocks + X


async def test_sip_takes_no_snapshot_and_no_pin(monkeypatch):
    async def _fail_snapshot(db, user_id):
        raise AssertionError("snapshot must not load on the SIP path")

    async def _fake_paa(user, q, *, chat_ctx, corpus_pin=None):
        assert corpus_pin is None
        return _fake_paa_outcome()

    monkeypatch.setattr(service_mod, "load_holdings_snapshot", _fail_snapshot)
    monkeypatch.setattr(service_mod, "compute_practical_allocation_result", _fake_paa)
    # ... existing fakes ...
    await compute_additional_investment_result(
        user, "start a sip", db=db, acting_user_id=uid, chat_session_id=None,
        deploy_amount_inr=25000.0, cadence=Cadence.SIP_MONTHLY, chat_ctx=ctx,
    )
```

- [ ] **Step 3: Run both to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service.py app/domains/additional_investment/services/ainv_engine/tests/test_persist.py -q`
Expected: FAIL — unexpected kwargs (`request_extras`, `corpus_pin` not forwarded, snapshot not imported)

- [ ] **Step 4: Implement the persist merge** — in `additional_investment_persist_service.py`, add the parameter and merge:

```python
    request_extras: Optional[dict[str, Any]] = None,
```
```python
    # ``request`` is optional but recommended so the per-call engine input is
    # captured for audit. Serialise to JSON-safe primitives for the JSONB column.
    # ``request_extras`` (deficit-fill mode metadata) is MERGED over the dump —
    # the stored dict is a superset of the engine input, no longer a pure
    # round-trippable model dump (spec 2026-07-03).
    request_input: Optional[dict[str, Any]] = (
        request.model_dump(mode="json") if request is not None else None
    )
    if request_extras:
        request_input = {**(request_input or {}), **request_extras}
```

- [ ] **Step 5: Implement the service wiring** — in `ainv_engine/service.py`:

Imports:
```python
from app.domains.additional_investment.services.ainv_engine.holdings_snapshot import (
    HoldingsSnapshot,
    load_holdings_snapshot,
)
from app.domains.practical_asset_allocation.services.paa_engine.input_builder import (
    CorpusPin,
)
```

Bump the version (behavioral contract change for lumpsum):
```python
AINV_ENGINE_VERSION = "ainv-2.0.0"
```
Then `grep -rn "ainv-1.0.0" app AI_Agents` and update any test that asserts the
old version string.

Add to the outcome dataclass:
```python
    # Deficit-fill facts for the chat formatter (lumpsum only): one row per
    # deployed subgroup {subgroup, ideal_inr, current_inr, gap_inr, buy_inr}.
    # None on the SIP / legacy path.
    deficit_facts: "list[dict] | None" = None
```

At the top of `compute_additional_investment_result`, before the PAA call:
```python
    snapshot: HoldingsSnapshot | None = None
    corpus_pin: CorpusPin | None = None
    if cadence is Cadence.LUMPSUM:
        # Deficit fill (spec 2026-07-03): ideal = PAA at actual holdings + X.
        snapshot = await load_holdings_snapshot(db, acting_user_id)
        corpus_pin = CorpusPin(
            total_corpus=snapshot.total_inr + deploy_amount_inr,
            mf_corpus=snapshot.total_inr - snapshot.non_mf_equity_inr + deploy_amount_inr,
            non_mf_equity_corpus=snapshot.non_mf_equity_inr,
            elss_corpus=snapshot.elss_inr,
        )
```

Pass the pin: `compute_practical_allocation_result(user, user_question, chat_ctx=chat_ctx, corpus_pin=corpus_pin)`.

Pass the map to the builder:
```python
        inp, debug = await build_additional_investment_input_for_user(
            chat_ctx,
            paa_outcome.result,
            deploy_amount_inr=deploy_amount_inr,
            cadence=cadence,
            current_value_by_subgroup=(
                snapshot.by_subgroup if snapshot is not None else None
            ),
        )
```

After the engine run, build the facts rows (pure, no I/O):
```python
    deficit_facts: list[dict] | None = None
    if snapshot is not None:
        rows_by = {r.subgroup: r for r in paa_outcome.result.aggregated_subgroups}
        buys_by: dict[str, float] = {}
        for b in response.buys:
            buys_by[b.asset_subgroup] = buys_by.get(b.asset_subgroup, 0.0) + float(b.amount_inr)
        deficit_facts = []
        for t in response.per_subgroup_target:
            row = rows_by.get(t.subgroup)
            ideal = float(row.total) if row is not None else 0.0
            current = snapshot.by_subgroup.get(t.subgroup, 0.0)
            deficit_facts.append({
                "subgroup": t.subgroup,
                "ideal_inr": ideal,
                "current_inr": current,
                "gap_inr": max(0.0, ideal - current),
                "buy_inr": buys_by.get(t.subgroup, 0.0),
            })
```

Persist call gains:
```python
                request_extras=(
                    {"deployment_mode": "deficit_fill",
                     "base_corpus_inr": snapshot.total_inr}
                    if snapshot is not None
                    else None
                ),
```

The success return site carries `deficit_facts=deficit_facts` (the blocking-outcome returns keep the default `None`).

- [ ] **Step 6: Run to verify pass, then the whole domain**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment -q`
Expected: PASS — new tests green, all pre-existing service/persist tests green (SIP unchanged, `request_extras` defaults to None).

---

### Task 7: Chat — deficit facts rows + new body prompt + trim the SIP-only legacy body

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/chat.py` (`_AINV_FORMATTER_BODY`, new `_AINV_DEFICIT_FORMATTER_BODY`, `build_ainv_facts_pack`, `_format_or_fallback_ainv`, `handle`)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py` (append)

**Interfaces:**
- Consumes: `outcome.deficit_facts` (Task 6).
- Produces: `build_ainv_facts_pack(output, deficit_rows=None)` — adds `facts["deficit_rows"]` when provided; `_format_or_fallback_ainv(ctx, output, deficit_facts=None)` — picks the deficit body prompt when `deficit_facts is not None`.

- [ ] **Step 1: Write the failing tests** — append to `test_chat.py`:

```python
def test_facts_pack_carries_deficit_rows_when_provided():
    rows = [{"subgroup": "low_beta_equities", "ideal_inr": 150000.0,
             "current_inr": 100000.0, "gap_inr": 50000.0, "buy_inr": 50000.0}]
    facts = build_ainv_facts_pack(_lumpsum_output(), deficit_rows=rows)
    assert facts["deficit_rows"] == rows


def test_facts_pack_omits_deficit_rows_by_default():
    facts = build_ainv_facts_pack(_lumpsum_output())
    assert "deficit_rows" not in facts


def test_legacy_body_is_sip_only_and_deficit_body_exists():
    from app.domains.additional_investment.services.ainv_engine import chat as chat_mod
    assert "lumpsum" not in chat_mod._AINV_FORMATTER_BODY.lower()
    assert "gap" in chat_mod._AINV_DEFICIT_FORMATTER_BODY.lower()
    assert "emergency" in chat_mod._AINV_DEFICIT_FORMATTER_BODY.lower()
```

(`_lumpsum_output()` — reuse/adapt the file's existing output fixture.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -q`
Expected: FAIL — unexpected kwarg `deficit_rows`, missing `_AINV_DEFICIT_FORMATTER_BODY`

- [ ] **Step 3: Implement.** Four changes in `chat.py`:

**(a)** `build_ainv_facts_pack` gains the parameter and one block before `return facts`:

```python
def build_ainv_facts_pack(
    output: AdditionalInvestmentOutput,
    deficit_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
```
```python
    if deficit_rows is not None:
        # Deficit-fill context (lumpsum): ideal-vs-current per subgroup, so the
        # formatter can narrate WHERE the gaps were. Engine labels — context
        # only, never surfaced raw.
        facts["deficit_rows"] = deficit_rows
```

**(b)** New body prompt, placed directly after the legacy one:

```python
_AINV_DEFICIT_FORMATTER_BODY = """You are answering a customer's question about
deploying fresh money — a one-time lumpsum being invested into specific funds.
This is BUY-only: nothing is ever sold. The shared house-style rules above apply.

HOW THE RECOMMENDATION WAS BUILT (context for your narrative): we computed the
customer's ideal portfolio for their goals INCLUDING this new money, compared it
with what they currently hold in each part of the portfolio, and directed the
fresh money into the gaps — the parts furthest below their ideal. Explain it in
this plain spirit: "we looked at where your portfolio is versus where it should
be, and this money fills those gaps."

The FACTS_PACK has this shape (treat fields not present as unknown):

  deploy_amount_inr / deploy_amount_indian — total fresh money being deployed
           (one-time; cadence is always lumpsum on this surface).
  target_bucket: "short_term" | "medium_term" | "long_term" — which horizon
           received the MOST of this money (derived from where it actually
           went). Context only — never surface the raw label.
  deficit_rows: list, one entry per subgroup the money went into:
      subgroup    — internal engine grouping. DO NOT surface this raw label.
      ideal_inr   — what the customer's ideal says this part should hold.
      current_inr — what they hold there today.
      gap_inr     — the shortfall this deploy is filling.
      buy_inr     — how much of the fresh money goes here.
  undeployed_inr / undeployed_indian — money that could NOT be placed. 0 when
           fully placed.
  under_deploy_note — present only when a MATERIAL amount couldn't be deployed.
           Surface its point when present; if buys is empty say so plainly and
           do NOT invent any fund.
  per_subgroup_target — the rupee split of the deploy (subgroup labels are
           context only).
  buys: list of the specific funds to BUY — the substance of the answer:
      recommended_fund — customer-facing scheme name. Cite VERBATIM; naming the
                    funds is the point of the reply.
      sub_category  — SEBI category for context (e.g. "Large Cap Fund").
      amount_inr / amount_indian — the one-time amount for this fund.

ACTION_MODE is `compute` — a fresh recommendation. Lead with the headline
(deploy_amount_indian, one-time), then NAME the 1-3 biggest buys with their
amounts, and give one plain-English line on why the money went where it did —
the gap-fill story, not a horizon label. When part of the deployment lands in
emergency/liquid funds, say so plainly ("part of this builds your emergency
cushion — the foundation; the rest goes to your growth gaps") rather than
leaving a liquid-fund buy unexplained. When under_deploy_note is present, close
with it. Length: 6-10 sentences (fewer when there is a single buy).
"""
```

**(c)** Trim `_AINV_FORMATTER_BODY` to SIP-only (it can now only be reached by
SIP runs). Replace its opening paragraph and the cadence-dependent lines:
- Opening: `"""You are answering a customer's question about a fresh
additional-investment recommendation — NEW money being deployed as a monthly
SIP into specific funds. This is BUY-only: nothing is ever sold. The shared
house-style rules above apply."""`
- `cadence` field doc → `cadence: always "sip_monthly" on this surface — the
plan repeats every month; frame amounts per-month (use each buy's
monthly_amount_indian).`
- Delete the lumpsum sentences from the `deploy_amount`, `buys.amount_inr`, and
`compute` ACTION_MODE blocks (keep the sip_monthly guidance; the word
"lumpsum" must not remain — the Step 1 test enforces this).

**(d)** Thread the rows through the formatter wrapper and handler:

```python
async def _format_or_fallback_ainv(
    ctx: TurnContext,
    output: AdditionalInvestmentOutput,
    deficit_facts: list[dict[str, Any]] | None = None,
) -> str:
    """Run the SHARED formatter on the engine output; fall back to the
    deterministic fund-naming brief on FormatterFailure. Deficit runs (lumpsum)
    get the gap-fill body prompt + deficit_rows facts."""
    return await format_with_telemetry(
        ctx=ctx,
        facts_pack=build_ainv_facts_pack(output, deficit_rows=deficit_facts),
        body_prompt=(
            _AINV_DEFICIT_FORMATTER_BODY
            if deficit_facts is not None
            else _AINV_FORMATTER_BODY
        ),
        module_name="additional_investment",
        action_mode="compute",
        profile={"first_name": getattr(ctx.user_ctx, "first_name", None)},
        build_fallback=lambda: _build_fallback_ainv_brief(output),
    )
```

and in `handle()`:
```python
    text = await _format_or_fallback_ainv(
        ctx, outcome.output, deficit_facts=outcome.deficit_facts
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py -q`
Expected: PASS (including pre-existing facts-pack tests — `deficit_rows` defaults to None)

---

### Task 8: Full regression + spec cross-check

**Files:** none (verification only)

- [ ] **Step 1: Full engine + touched-domain suites**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/ app/domains/additional_investment app/domains/practical_asset_allocation app/domains/rebalancing -q`
Expected: ALL PASS.

- [ ] **Step 2: Broader app suite (catch cross-domain fallout)**

Run: `.venv-mac/bin/python -m pytest app -q`
Expected: PASS (pre-existing failures, if any, must match a baseline run taken BEFORE starting Task 1 — record that baseline first).

- [ ] **Step 3: Spec checklist** — walk `docs/superpowers/specs/2026-07-03-ainv-deficit-fill-design.md` top to bottom and tick: frozen scalars fed ✓ (Task 6), iteration direction ✓ (Task 1), ratio identity ✓ (Task 1), dominant-horizon label ✓ (Tasks 1-2), projection skipped on lumpsum ✓ (Task 5), JSONB merge ✓ (Task 6), facts rows + emergency narrative ✓ (Task 7), legacy prompt trimmed ✓ (Task 7), SIP untouched ✓ (regression suites), rounding-docstring cleanup ✓ (do it now if not yet: `models.py:6` "rounded down" → "rounded to the nearest"). Report any unticked line before declaring done.

---

## Deferred / explicitly NOT in this plan

- No holdings-snapshot failure fallback (product decision 2026-07-04: skip the insurance; CAMS upload is mandatory).
- No multi-portfolio / family-member scoping (not supported yet).
- No SIP deficit-fill; no `used_cached_allocation` removal (flagged separately).
