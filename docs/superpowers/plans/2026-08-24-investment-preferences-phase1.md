# Investment Preferences — Phase 1 (Rebalancing) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **EXECUTION STATUS (2026-08-25): ALL TASKS COMPLETE** (inline execution). Deviations from plan, all disclosed in-session: fund resolver placed in existing `fund_ranking_lookup.py` as `resolve_ranked_fund` (found existing DB-backed `fund_resolver_service.resolve_fund`; no new file); eval gated by `RUN_REBAL_DETECTOR_EVAL=1` (repo convention) instead of key-presence; why-not answers go through `format_relay_or_canned` (no engine response needed); Task 10's two integration guards live in `test_preference_chat_wiring.py` (exact duplicates dropped per DRY); ruff step skipped — no ruff binary in `.venv-mac`. Eval results: existing 8/8 → 8/8 (no regression), v1-pref 4/12 → 12/12. All 16 full-suite failures/errors verified pre-existing via stash-baseline diff.

**Goal:** Customers can state fund preferences in rebalancing chat — asset-class scope, equity tilt, category weights/exclusions, named-fund explanations — and get their plan their way, side by side with the recommended plan, in one deterministic pass. (Named-fund *inclusion* in the plan is Phase 2, at its correct seam: the input builder, where the engine computes real numbers for the injected fund.)

**Prime directive (audit 2026-08-24):** the shipped F3-B reshape arithmetic is NEVER modified — when only the legacy constraints are active, `compute_reshaped_buys` runs the original code path byte-for-byte. All new capability is layered AROUND the core, never through it.

**Architecture:** Extends the existing F3-B constrained-run pattern (detect → canonicalize → engine once → deterministic reshape → comply-and-caution → narrate). Tilts apply pre-engine via a new `asset_class_tilt` on `RebalancingComputeRequest` (numbered tilts) or the existing `effective_risk_score` AA override (band-edge default); category preferences generalize `compute_reshaped_buys`. Spec: `docs/superpowers/specs/2026-08-24-investment-preferences-design.md`. Phase 2 (SIP/lump-sum parity) is a separate plan.

**Tech Stack:** Python 3.12, pydantic v2, pytest (`asyncio_mode=auto`), LangChain/Haiku (existing detector only — no new LLM calls).

## Global Constraints

- Run tests with `.venv-mac/bin/python -m pytest` from `Prozpr_Backend/` (pyproject sets `pythonpath = ["AI_Agents/src", "."]`).
- NO new `ChatAnthropic(...)` call sites. The detector's existing call is reused; `test_temperature_is_pinned.py` must stay green.
- `AI_Agents/src/*/Testing/` is **gitignored** — all NEW committed tests live under `app/domains/.../tests/`.
- Bump `ENGINE_VERSION` in `AI_Agents/src/Rebalancing/config.py` in the task that changes engine output (Task 3) — it is stamped into response metadata.
- Do NOT touch `AI_Agents/Reference_docs/` (manual-refresh convention). `CLAUDE.md` files MAY be updated (Task 10).
- PostHog events must carry NO chat text — ids and fixed tokens only (spec §telemetry; matches `capture_flow_completed`'s discipline).
- Money stays `Decimal` inside the reshape (existing `consolidation.py` convention); mix percentages are plain `float`.
- All preference turns run with `persist=False` (stateless per spec). The audit trail is the `applied_preferences` block inside the constraint-impact payload.
- Commit after every task; plan/spec files under `docs/superpowers/` need `git add -f`.

---

### Task 1: Preference normalization core (`investment_preferences.py`)

**Files:**
- Create: `app/domains/mutual_funds/services/investment_preferences.py`
- Create: `app/domains/mutual_funds/services/tests/__init__.py` (empty)
- Test: `app/domains/mutual_funds/services/tests/test_investment_preferences.py`

**Interfaces:**
- Consumes: `category_for_effective_risk_score`, `RISK_CATEGORIES` from `common` (AI_Agents src, importable after `ensure_ai_agents_path()`; in tests the pyproject pythonpath makes `from common import …` work directly).
- Produces (used by Tasks 8–9b):
  - `normalize_tilt(current_mix_pct: dict[str, float], *, scope_only: list[str] | None, tilt_asset_class: str | None, tilt_delta_pp: float | None, tilt_target_pct: float | None) -> TiltResult`
  - `@dataclass(frozen=True) TiltResult: mix_pct: dict[str, float] | None; defaults_applied: dict[str, str]; needs_band_edge_default: bool`
  - `band_edge_score(category: str) -> float`
  - `AT_EDGE_STEP_PP: float = 5.0`

- [ ] **Step 1: Write the failing tests**

```python
# app/domains/mutual_funds/services/tests/test_investment_preferences.py
"""Unit tests for preference normalization (pure functions, no I/O)."""

import pytest

from app.domains.mutual_funds.services.investment_preferences import (
    band_edge_score,
    normalize_tilt,
)

MIX = {"equity": 55.0, "debt": 35.0, "others": 10.0}


def test_scope_only_equity_normalizes_to_absolute_100():
    r = normalize_tilt(MIX, scope_only=["equity"], tilt_asset_class=None,
                       tilt_delta_pp=None, tilt_target_pct=None)
    assert r.mix_pct == {"equity": 100.0, "debt": 0.0, "others": 0.0}
    assert not r.needs_band_edge_default


def test_scope_no_gold_zeroes_others_and_renormalizes():
    r = normalize_tilt(MIX, scope_only=["equity", "debt"], tilt_asset_class=None,
                       tilt_delta_pp=None, tilt_target_pct=None)
    assert r.mix_pct["others"] == 0.0
    # 55/35 renormalized over 90 → 61.11 / 38.89
    assert r.mix_pct["equity"] == pytest.approx(61.11, abs=0.01)
    assert sum(r.mix_pct.values()) == pytest.approx(100.0)


def test_delta_tilt_renormalizes_other_classes_pro_rata():
    r = normalize_tilt(MIX, scope_only=None, tilt_asset_class="equity",
                       tilt_delta_pp=10.0, tilt_target_pct=None)
    assert r.mix_pct["equity"] == pytest.approx(65.0)
    # debt/others shrink pro-rata over their 45-point pool → 27.22 / 7.78
    assert r.mix_pct["debt"] == pytest.approx(27.22, abs=0.01)
    assert sum(r.mix_pct.values()) == pytest.approx(100.0)


def test_absolute_tilt_take_equity_to_70():
    r = normalize_tilt(MIX, scope_only=None, tilt_asset_class="equity",
                       tilt_delta_pp=None, tilt_target_pct=70.0)
    assert r.mix_pct["equity"] == pytest.approx(70.0)
    assert sum(r.mix_pct.values()) == pytest.approx(100.0)


def test_tilt_clamps_at_100_and_records_default():
    r = normalize_tilt(MIX, scope_only=None, tilt_asset_class="equity",
                       tilt_delta_pp=60.0, tilt_target_pct=None)
    assert r.mix_pct["equity"] == pytest.approx(100.0)
    assert "clamped" in r.defaults_applied["tilt"]


def test_no_number_tilt_requests_band_edge_default():
    r = normalize_tilt(MIX, scope_only=None, tilt_asset_class="equity",
                       tilt_delta_pp=None, tilt_target_pct=None)
    assert r.needs_band_edge_default
    assert r.mix_pct is None


def test_band_edge_score_is_top_of_named_band():
    from common import RISK_CATEGORIES, category_for_effective_risk_score
    for cat in RISK_CATEGORIES:
        edge = band_edge_score(cat)
        assert category_for_effective_risk_score(edge) == cat
        # nothing above the edge is still in this band (except the top band)
        if cat != RISK_CATEGORIES[-1]:
            assert category_for_effective_risk_score(edge + 0.02) != cat
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/services/tests/test_investment_preferences.py -v`
Expected: FAIL with `ModuleNotFoundError: ... investment_preferences`

- [ ] **Step 3: Write the implementation**

```python
# app/domains/mutual_funds/services/investment_preferences.py
"""Deterministic normalization for customer investment preferences.

Spec: docs/superpowers/specs/2026-08-24-investment-preferences-design.md.
Scope ("only equity") normalizes into absolute tilts so there is ONE
pre-engine mechanism. Magnitude defaults are policy, never LLM judgment;
every applied default is recorded in ``defaults_applied`` for the audit
trail. Pure functions — no I/O, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domains.ai_engine.common import ensure_ai_agents_path

ensure_ai_agents_path()

from common import RISK_CATEGORIES, category_for_effective_risk_score  # noqa: E402

ASSET_CLASSES = ("equity", "debt", "others")

# Fixed step past the band edge when the customer says "more equity" with no
# number while already sitting at their band's edge (spec: defaults table).
AT_EDGE_STEP_PP = 5.0

# Upper score bound per band, aligned with common.category_for_effective_risk_score
# thresholds (2.125 / 4.375 / 6.625 / 8.875, scores span 1.0-10.0). Edges sit
# just below the threshold so the edge score still maps into the SAME band.
_BAND_UPPER = dict(zip(RISK_CATEGORIES, (2.125, 4.375, 6.625, 8.875, 10.0)))
_EDGE_EPS = 0.01


@dataclass(frozen=True)
class TiltResult:
    mix_pct: dict[str, float] | None
    defaults_applied: dict[str, str] = field(default_factory=dict)
    needs_band_edge_default: bool = False


def band_edge_score(category: str) -> float:
    """Top effective-risk score that still maps into ``category``.

    ``_BAND_UPPER`` deliberately duplicates common.py's thresholds; this check
    is the sync mechanism — a real exception (not ``assert``, which ``-O``
    strips) so drift fails loudly in production, not silently in advice.
    """
    upper = _BAND_UPPER[category]
    edge = upper if upper == 10.0 else upper - _EDGE_EPS
    if category_for_effective_risk_score(edge) != category:
        raise ValueError(
            f"band table drift: edge {edge} no longer maps into {category!r}"
        )
    return edge


def _renormalized(mix: dict[str, float], pinned: dict[str, float]) -> dict[str, float]:
    """Hold ``pinned`` classes fixed; scale the rest pro-rata to sum 100."""
    free = [c for c in ASSET_CLASSES if c not in pinned]
    pinned_total = sum(pinned.values())
    free_current = sum(mix[c] for c in free)
    remaining = max(0.0, 100.0 - pinned_total)
    out = dict(pinned)
    for c in free:
        out[c] = (
            remaining * mix[c] / free_current
            if free_current > 0
            else (remaining / len(free) if free else 0.0)
        )
    return out


def normalize_tilt(
    current_mix_pct: dict[str, float],
    *,
    scope_only: list[str] | None,
    tilt_asset_class: str | None,
    tilt_delta_pp: float | None,
    tilt_target_pct: float | None,
) -> TiltResult:
    """Resolve scope + tilt into one absolute target mix (or a default request).

    Baseline is the RECOMMENDED target mix (never current holdings) — the
    caller passes it in. Returns ``needs_band_edge_default=True`` when a tilt
    names a class but no number: the caller resolves it via band_edge_score /
    AT_EDGE_STEP_PP (needs the customer's risk category, which lives caller-side).
    """
    defaults: dict[str, str] = {}
    mix = {c: float(current_mix_pct.get(c, 0.0)) for c in ASSET_CLASSES}

    if scope_only:
        allowed = {c for c in scope_only if c in ASSET_CLASSES}
        pinned = {c: 0.0 for c in ASSET_CLASSES if c not in allowed}
        return TiltResult(mix_pct=_renormalized(mix, pinned), defaults_applied=defaults)

    if tilt_asset_class is None:
        return TiltResult(mix_pct=None, defaults_applied=defaults)

    if tilt_delta_pp is None and tilt_target_pct is None:
        return TiltResult(mix_pct=None, defaults_applied=defaults,
                          needs_band_edge_default=True)

    target = (
        tilt_target_pct
        if tilt_target_pct is not None
        else mix[tilt_asset_class] + tilt_delta_pp
    )
    clamped = min(100.0, max(0.0, target))
    if clamped != target:
        defaults["tilt"] = f"clamped {target:.1f} -> {clamped:.1f}"
    return TiltResult(
        mix_pct=_renormalized(mix, {tilt_asset_class: clamped}),
        defaults_applied=defaults,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/services/tests/test_investment_preferences.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/domains/mutual_funds/services/investment_preferences.py app/domains/mutual_funds/services/tests/
git commit -m "feat(preferences): deterministic tilt/scope normalization + band-edge policy"
```

---

### Task 2: Generalize the buy reshape (`consolidation.py`)

**Files:**
- Modify: `AI_Agents/src/Rebalancing/consolidation.py`
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_preference_reshape.py`

**Interfaces:**
- Consumes: existing `BuyCandidate`, `_round_to_multiple`.
- Produces (used by Task 9b):
  - `ConsolidationConstraints` gains `excluded_categories: tuple[str, ...] | None` and `category_weight_targets: dict[str, float] | None` (sub_category → requested share of TOTAL buy, 0–1). No `include_fund` — named-fund inclusion is Phase 2, at the input-builder seam.
  - `compute_reshaped_buys` honors the composition order: eligibility filters → weight targets → fund-count trim (never evicting a weight-target category; count bumps up to the protected-category count when they conflict) → round/residual. **Legacy invariant: when neither new field is set, the ORIGINAL code path runs byte-for-byte.**
  - `reshape_response` may return one new error code: `"weight_category_not_in_plan"`.

- [ ] **Step 1: Write the failing tests**

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_preference_reshape.py
"""Reshape math for preference constraints (pure; mirrors F3-B invariants)."""

from decimal import Decimal

from Rebalancing.consolidation import (
    BuyCandidate,
    ConsolidationConstraints,
    compute_reshaped_buys,
    constraints_active,
)


def _cand(isin, cat, rank, buy):
    return BuyCandidate(isin=isin, recommended_fund=isin, sub_category=cat,
                        asset_subgroup="x", rank=rank, buy_inr=Decimal(buy))


CANDS = [
    _cand("A", "Large Cap Fund", 1, 40_000),
    _cand("B", "Mid Cap Fund", 1, 20_000),
    _cand("C", "Gilt Fund", 1, 30_000),
    _cand("D", "Liquid Fund", 2, 10_000),
]
TOTAL = Decimal(100_000)


def test_excluded_categories_drop_and_redistribute():
    c = ConsolidationConstraints(excluded_categories=("Gilt Fund",))
    assert constraints_active(c)
    out = compute_reshaped_buys(CANDS, c)
    assert out["C"] == 0
    assert sum(out.values()) == TOTAL


def test_weight_target_raises_category_to_requested_share():
    c = ConsolidationConstraints(category_weight_targets={"Mid Cap Fund": 0.4})
    out = compute_reshaped_buys(CANDS, c)
    assert out["B"] >= Decimal(40_000) - Decimal(100)  # rounding_multiple slack
    assert sum(out.values()) == TOTAL


def test_weight_target_already_met_is_identity():
    c = ConsolidationConstraints(category_weight_targets={"Large Cap Fund": 0.3})
    out = compute_reshaped_buys(CANDS, c)
    assert out == {x.isin: x.buy_inr for x in CANDS}


def test_count_trim_never_evicts_weight_target_category():
    c = ConsolidationConstraints(
        target_fund_count=2, category_weight_targets={"Mid Cap Fund": 0.3}
    )
    out = compute_reshaped_buys(CANDS, c)
    assert out["B"] > 0
    assert sum(1 for v in out.values() if v > 0) <= 2
    assert sum(out.values()) == TOTAL


def test_legacy_only_constraints_hit_the_original_path():
    # Guard for the prime directive: legacy-only constraints must produce the
    # EXACT same result as before this change (arithmetic form included).
    c = ConsolidationConstraints(target_fund_count=2)
    out = compute_reshaped_buys(CANDS, c)
    # Old algorithm: survivors frozen, displaced budget added pro-rata,
    # share rounded THEN added (A:40k B... rank/buy order keeps A and C).
    assert sum(out.values()) == TOTAL
    assert out["D"] == 0 and out["B"] == 0


def test_count_conflict_bumps_to_protected_category_count():
    c = ConsolidationConstraints(
        target_fund_count=1,
        category_weight_targets={"Mid Cap Fund": 0.2, "Gilt Fund": 0.2},
    )
    out = compute_reshaped_buys(CANDS, c)
    # 1 fund can't honor 2 protected categories -> count bumps to 2 (disclosed
    # upstream by Task 9b via impact["count_bumped_to"]).
    assert sum(1 for v in out.values() if v > 0) == 2
    assert sum(out.values()) == TOTAL


def test_composition_excluded_beats_weights():
    c = ConsolidationConstraints(
        excluded_categories=("Mid Cap Fund",),
        category_weight_targets={"Mid Cap Fund": 0.4},
    )
    out = compute_reshaped_buys(CANDS, c)
    assert out["B"] == 0  # exclusion wins; caller surfaces the contradiction
    assert sum(out.values()) == TOTAL
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_preference_reshape.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'excluded_categories'`

- [ ] **Step 3: Implement in `consolidation.py`**

Extend the dataclass and `constraints_active`:

```python
@dataclass(frozen=True)
class ConsolidationConstraints:
    target_fund_count: int | None = None                 # max NEW-BUY funds
    allowed_categories: tuple[str, ...] | None = None    # redeploy whole budget here
    excluded_categories: tuple[str, ...] | None = None   # never buy these
    category_weight_targets: dict[str, float] | None = None  # sub_category -> share of total buy (0-1)
    # NO include_fund: named-fund inclusion is Phase 2, at the input-builder
    # seam where the engine computes real numbers for the injected fund.
    # NO reset flag: stateless design — "back to the full plan" is narrate mode.


def constraints_active(c: ConsolidationConstraints) -> bool:
    return (
        c.target_fund_count is not None
        or bool(c.allowed_categories)
        or bool(c.excluded_categories)
        or bool(c.category_weight_targets)
    )
```

**Do NOT rewrite the existing body of `compute_reshaped_buys`.** Add a gate at the top of the existing steps so the shipped path is untouched, and a separate function for the extended path:

```python
def compute_reshaped_buys(candidates, constraints, *, rounding_multiple=100):
    cands = list(candidates)
    total = sum((c.buy_inr for c in cands), Decimal(0))
    if total <= 0 or not cands:
        return {c.isin: Decimal(0) for c in cands}
    if not constraints.excluded_categories and not constraints.category_weight_targets:
        return _reshape_legacy(cands, constraints, total,
                               rounding_multiple=rounding_multiple)
    return _reshape_extended(cands, constraints, total,
                             rounding_multiple=rounding_multiple)
```

`_reshape_legacy` is the CURRENT steps 1–4, moved verbatim — same filter, same top-N, same `c.buy_inr + _round_to_multiple(share, ...)` arithmetic, same residual placement. Zero diff beyond the function wrapper (the existing consolidation tests plus `test_legacy_only_constraints_hit_the_original_path` guard this).

`_reshape_extended` reuses the SAME arithmetic form (bases frozen, rounded DELTAS added, residual to the largest buy), composed as: filters → weight targets → count trim → distribute/round:

```python
def _reshape_extended(cands, constraints, total, *, rounding_multiple):
    # 1. Eligibility: allowed-list, then excluded-list.
    eligible = list(cands)
    if constraints.allowed_categories:
        allowed = set(constraints.allowed_categories)
        eligible = [c for c in eligible if c.sub_category in allowed]
    if constraints.excluded_categories:
        excluded = set(constraints.excluded_categories)
        eligible = [c for c in eligible if c.sub_category not in excluded]
    if not eligible:
        return {c.isin: Decimal(0) for c in cands}    # honest no-op; caller surfaces error

    # 2. Weight targets: raise each named category to its requested share of
    #    the ORIGINAL total. Donors = eligible funds in non-named categories,
    #    scaled down pro-rata; donors flooring at 0 means partial satisfaction,
    #    which the caller narrates honestly from the resulting shares.
    working = {c.isin: c.buy_inr for c in eligible}
    by_isin = {c.isin: c for c in eligible}
    if constraints.category_weight_targets:
        named = set(constraints.category_weight_targets)
        per_cat_deficit: dict[str, Decimal] = {}
        for cat, share in constraints.category_weight_targets.items():
            have = sum(v for k, v in working.items() if by_isin[k].sub_category == cat)
            want = (total * Decimal(str(share))).quantize(_ONE)
            if want > have:
                per_cat_deficit[cat] = want - have
        deficit = sum(per_cat_deficit.values(), Decimal(0))
        donors = [k for k, c in by_isin.items() if c.sub_category not in named]
        donor_total = sum(working[k] for k in donors)
        take = min(deficit, donor_total)
        for k in donors:
            if donor_total > 0:
                working[k] -= take * working[k] / donor_total
        for cat, cat_deficit in per_cat_deficit.items():
            grant = take * cat_deficit / deficit if deficit > 0 else Decimal(0)
            receivers = [k for k, c in by_isin.items() if c.sub_category == cat]
            recv_total = sum(working[k] for k in receivers)
            for k in receivers:
                frac = (working[k] / recv_total) if recv_total > 0 else (
                    Decimal(1) / Decimal(len(receivers)))
                working[k] += grant * frac

    # 3. Count trim. Protected = weight-target categories present in the plan.
    #    A count below the protected-category count is unsatisfiable as asked:
    #    bump keep_n up to len(protected) — Task 9b discloses the bump.
    live = [c for c in eligible if working[c.isin] > 0]
    ordered = sorted(live, key=lambda c: (c.rank, -working[c.isin]))
    keep = ordered
    if constraints.target_fund_count is not None:
        protected = {
            cat for cat in (constraints.category_weight_targets or {})
            if any(c.sub_category == cat for c in live)
        }
        keep_n = max(1, constraints.target_fund_count, len(protected))
        keep, seen = [], set()
        for c in ordered:                      # best fund of each protected category first
            if c.sub_category in protected and c.sub_category not in seen:
                keep.append(c)
                seen.add(c.sub_category)
        for c in ordered:                      # fill the rest by rank
            if len(keep) >= keep_n:
                break
            if c not in keep:
                keep.append(c)

    # 4. Same arithmetic form as legacy: frozen base + rounded delta, residual
    #    onto the largest surviving buy, total preserved exactly.
    kept_total = sum((working[c.isin] for c in keep), Decimal(0))
    displaced = total - kept_total
    out: dict[str, Decimal] = {c.isin: Decimal(0) for c in cands}
    for c in keep:
        share = (displaced * working[c.isin] / kept_total if kept_total > 0
                 else displaced / Decimal(len(keep)))
        out[c.isin] = working[c.isin] + _round_to_multiple(share, rounding_multiple)
    placed = sum(out.values(), Decimal(0))
    residual = total - placed
    if residual != 0 and keep:
        biggest = max(keep, key=lambda c: out[c.isin])
        out[biggest.isin] += residual
    return out
```

In `reshape_response`, after the existing `allowed_categories` presence check, add the one new honest error code (same pattern):

```python
    present = {c.sub_category for c in candidates}
    if constraints.category_weight_targets and not (
        present & set(constraints.category_weight_targets)
    ):
        return response, "weight_category_not_in_plan"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_preference_reshape.py -v`
Expected: all PASS. Also run the existing consolidation contract: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests -k consolidat -v` — must stay green (identity behavior unchanged when new fields are None).

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/consolidation.py app/domains/rebalancing/services/rebal_engine/tests/test_preference_reshape.py
git commit -m "feat(preferences): layer exclusions + weight targets around the untouched F3-B reshape core"
```

---

### Task 3: Engine tilt seam (`asset_class_tilt` on the compute request)

**Files:**
- Modify: `AI_Agents/src/Rebalancing/models.py` (RebalancingComputeRequest, ~line 150)
- Modify: `AI_Agents/src/Rebalancing/pipeline.py` (after `run_practical_allocation`, ~line 210)
- Modify: `AI_Agents/src/Rebalancing/config.py` (ENGINE_VERSION bump)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_asset_class_tilt.py`

**Interfaces:**
- Consumes: `PracticalAllocationOutput.aggregated_subgroups` (`AggregatedSubgroupRow`: subgroup, emergency, short_term, medium_term, long_term, total), `SUBGROUP_TO_ASSET_CLASS` from `asset_allocation_pydantic.tables`.
- Produces (used by Tasks 4, 9b): `RebalancingComputeRequest.asset_class_tilt: dict[str, float] | None` — absolute target mix percentages (`{"equity": 65.0, "debt": 27.2, "others": 7.8}`, sums to ~100). Pipeline scales every subgroup of a class by `tilted_class_total / current_class_total` across ALL bucket fields. A requested class absent from the portfolio is silently re-spread here (engine stays pure); the HONEST DISCLOSURE of that shortfall is Task 9b's job (spec: never silently ignore) — the chat layer detects `requested class not in current mix` and adds the note to `applied_preferences` and the reply.

- [ ] **Step 1: Write the failing test**

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_asset_class_tilt.py
"""Pipeline-level tilt: scales practical subgroup totals per asset class."""

import pytest

from asset_allocation_pydantic.models import AggregatedSubgroupRow
from asset_allocation_pydantic.tables import SUBGROUP_TO_ASSET_CLASS
from Rebalancing.pipeline import _apply_asset_class_tilt


def _row(subgroup, total):
    return AggregatedSubgroupRow(subgroup=subgroup, emergency=0.0, short_term=0.0,
                                 medium_term=0.0, long_term=total, total=total)


def test_tilt_scales_classes_to_requested_mix():
    rows = [_row("high_beta_equities", 30_000.0), _row("low_beta_equities", 30_000.0),
            _row("short_debt", 30_000.0), _row("gold_commodities", 10_000.0)]
    tilted = _apply_asset_class_tilt(rows, {"equity": 80.0, "debt": 15.0, "others": 5.0})
    by_class: dict[str, float] = {}
    for r in tilted:
        by_class[SUBGROUP_TO_ASSET_CLASS[r.subgroup]] = (
            by_class.get(SUBGROUP_TO_ASSET_CLASS[r.subgroup], 0.0) + r.total)
    grand = sum(by_class.values())
    assert grand == pytest.approx(100_000.0)
    assert by_class["equity"] / grand == pytest.approx(0.80, abs=0.001)
    # within-class proportions preserved (both equity subgroups equal)
    eq = [r.total for r in tilted if SUBGROUP_TO_ASSET_CLASS[r.subgroup] == "equity"]
    assert eq[0] == pytest.approx(eq[1])


def test_tilt_none_is_identity():
    rows = [_row("high_beta_equities", 50_000.0), _row("short_debt", 50_000.0)]
    assert _apply_asset_class_tilt(rows, None) is rows


def test_class_absent_from_portfolio_stays_absent():
    rows = [_row("high_beta_equities", 60_000.0), _row("short_debt", 40_000.0)]
    tilted = _apply_asset_class_tilt(rows, {"equity": 50.0, "debt": 40.0, "others": 10.0})
    # no "others" subgroup exists to scale up from zero — the 10% is
    # redistributed over present classes, preserving the grand total.
    assert sum(r.total for r in tilted) == pytest.approx(100_000.0)
    assert not any(SUBGROUP_TO_ASSET_CLASS[r.subgroup] == "others" for r in tilted)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_asset_class_tilt.py -v`
Expected: FAIL with `ImportError: cannot import name '_apply_asset_class_tilt'`

- [ ] **Step 3: Implement**

In `models.py`, add to `RebalancingComputeRequest` (after `rounding_step`):

```python
    # Optional customer preference: absolute asset-class target mix (percent,
    # ~sums to 100) applied to the practical allocation before subgroup targets
    # are lifted. None = engine-recommended mix. Spec:
    # docs/superpowers/specs/2026-08-24-investment-preferences-design.md
    asset_class_tilt: dict[str, float] | None = None
```

In `pipeline.py`, add the helper and call it right after `practical = run_practical_allocation(...)`:

```python
from asset_allocation_pydantic.tables import SUBGROUP_TO_ASSET_CLASS


def _apply_asset_class_tilt(rows, tilt):
    """Scale each asset class's subgroup rows to hit the requested mix.

    Within-class proportions are preserved (every bucket field scales by the
    same factor). A requested class with zero current total cannot be created
    from nothing — its share is re-spread over the present classes so the
    grand total is preserved exactly.
    """
    if not tilt:
        return rows
    grand = sum(r.total for r in rows)
    if grand <= 0:
        return rows
    current: dict[str, float] = {}
    for r in rows:
        cls = SUBGROUP_TO_ASSET_CLASS.get(r.subgroup, "others")
        current[cls] = current.get(cls, 0.0) + r.total
    present = {c: p for c, p in tilt.items() if current.get(c, 0.0) > 0}
    present_share = sum(present.values())
    factors = {
        c: (grand * (p / present_share)) / current[c]
        for c, p in present.items()
        if present_share > 0
    }
    out = []
    for r in rows:
        cls = SUBGROUP_TO_ASSET_CLASS.get(r.subgroup, "others")
        f = factors.get(cls)
        if f is None:
            out.append(r)
            continue
        out.append(r.model_copy(update={
            "emergency": r.emergency * f, "short_term": r.short_term * f,
            "medium_term": r.medium_term * f, "long_term": r.long_term * f,
            "total": r.total * f,
        }))
    return out
```

At the call site (`pipeline.py` ~line 210):

```python
    practical = run_practical_allocation(request.practical_allocation_input)
    tilted_subgroups = _apply_asset_class_tilt(
        practical.aggregated_subgroups, request.asset_class_tilt
    )
```

and pass `tilted_subgroups` wherever `practical.aggregated_subgroups` fed `_assign_subgroup_targets` (keep the untouched `practical` object on the response for the ideal-vs-practical UI). In `config.py`, bump `ENGINE_VERSION` (output-altering change — CLAUDE.md invariant).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_asset_class_tilt.py -v`
Expected: PASS. Then the engine's own local suite (gitignored but runnable): `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing -q` — no regressions (tilt=None is identity).

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/models.py AI_Agents/src/Rebalancing/pipeline.py AI_Agents/src/Rebalancing/config.py app/domains/rebalancing/services/rebal_engine/tests/test_asset_class_tilt.py
git commit -m "feat(preferences): asset_class_tilt on RebalancingComputeRequest, applied post-practical; bump ENGINE_VERSION"
```

---

### Task 4: Thread the tilt through overrides + input builder

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/overrides.py` (allow-list frozenset)
- Modify: `app/domains/rebalancing/services/rebal_engine/input_builder.py` (~line 435, beside the other `effective_param` reads)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_tilt_override_threading.py`

**Interfaces:**
- Consumes: `with_chat_overrides(ctx, dict)`, `effective_param(ctx, key, fallback)` (existing), `RebalancingComputeRequest.asset_class_tilt` (Task 3).
- Produces (used by Task 9b): override key `"asset_class_tilt"` accepted in the rebal allow-list; value shape `dict[str, float]` (absolute mix). The input builder copies it onto the request verbatim.

- [ ] **Step 1: Write the failing test**

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_tilt_override_threading.py
"""asset_class_tilt is an allowed chat override and reaches the request."""

import pytest

from app.domains.rebalancing.services.rebal_engine.overrides import (
    _REBAL_ALLOWED_OVERRIDE_KEYS,
    effective_param,
    with_chat_overrides,
)


def test_asset_class_tilt_is_allowed_key():
    assert "asset_class_tilt" in _REBAL_ALLOWED_OVERRIDE_KEYS


def test_effective_param_round_trips_tilt(detector_ctx):
    ctx = with_chat_overrides(detector_ctx, {"asset_class_tilt": {"equity": 70.0}})
    assert effective_param(ctx, "asset_class_tilt", None) == {"equity": 70.0}
```

(`detector_ctx` is the TurnContext fixture in `rebal_engine/tests/conftest.py` — the existing override tests there already build one; if it has a different name, reuse that fixture rather than stubbing TurnContext by hand.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_tilt_override_threading.py -v`
Expected: FAIL — `"asset_class_tilt" not in _REBAL_ALLOWED_OVERRIDE_KEYS`

- [ ] **Step 3: Implement**

In `overrides.py`, add to the allow-list frozenset (the one exported as `_REBAL_ALLOWED_OVERRIDE_KEYS`):

```python
        "asset_class_tilt",  # dict {class: absolute pct} — spec 2026-08-24 investment preferences
```

In `input_builder.py`, beside the four existing reads (~line 435):

```python
    tilt_override = effective_param(ctx, "asset_class_tilt", None)
```

and set it on the constructed request:

```python
        asset_class_tilt=tilt_override,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_tilt_override_threading.py app/domains/rebalancing/services/rebal_engine/tests -q`
Expected: new test PASS; whole rebal_engine suite green.

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/overrides.py app/domains/rebalancing/services/rebal_engine/input_builder.py app/domains/rebalancing/services/rebal_engine/tests/test_tilt_override_threading.py
git commit -m "feat(preferences): thread asset_class_tilt override into the compute request"
```

---

### Task 5: Named-fund resolver (`resolve_fund`)

**Files (revised during execution — duplication found):** the domain already has `fund_resolver_service.py` (DB-backed identity resolution — would miss CSV-only rejected funds) and `fund_ranking_lookup.py` (declared home for "thin lookups over the cached fund-ranking CSV"). So: NO new `fund_resolver.py`; the CSV-side matcher lives in `fund_ranking_lookup.py` as `resolve_ranked_fund` (name chosen to avoid colliding with the existing `resolve_fund` symbol).
- Modify: `app/domains/rebalancing/services/rebal_engine/fund_rank.py` (add `get_all_rows()` over ALL CSV rows)
- Modify: `app/domains/mutual_funds/services/fund_ranking_lookup.py` (add `FundResolution` + `resolve_ranked_fund`)
- Test: `app/domains/mutual_funds/services/tests/test_fund_resolver.py` (+ fixture CSV)

**Interfaces:**
- Consumes: `get_fund_ranking()` (recommended rows), `get_rejection_reasons()` (`{isin: reason_text}` for rank-blank rows), plus a NEW `fund_rank.get_all_rows() -> list[FundRankRow]` (every CSV row, recommended or not; same cached-loader pattern as `get_fund_ranking`, rejected rows carry `rank=0`).
- Produces (used by Task 9b):

```python
@dataclass(frozen=True)
class FundResolution:
    status: Literal["recommended", "rejected", "ambiguous", "unknown"]
    isin: str | None = None
    fund_name: str | None = None
    sub_category: str | None = None
    rejection_text: str | None = None
    candidates: tuple[str, ...] = ()   # names, for ambiguous

def resolve_fund(text: str) -> FundResolution: ...
```

- [ ] **Step 1: Write the failing tests**

Tests run against a 4-row FIXTURE CSV, never the live ranking — the May→June CSV swap already burned one pinned test in this repo; do not recreate that trap. Also Create: `app/domains/mutual_funds/services/tests/fund_ranking_fixture.csv` with exactly the live header row (copy it from `prozpr_fund_ranking_june_2026_v2.csv`) plus:

```csv
low_beta_equities,Large Cap Fund,1,INFTEST00001,100001,Alpha Large Cap Fund,solid pick,,,,,,,,,
medium_beta_equities,Mid Cap Fund,,INFTEST00002,100002,Beta Mid Cap Fund,,,short PM tenure,,,,,,,
low_beta_equities,Large Cap Fund,2,INFTEST00003,100003,Gamma Bluechip Fund,decent,,,,,,,,,
short_debt,Liquid Fund,1,INFTEST00004,100004,Delta Liquid Fund,cash mgmt,,,,,,,,,
```

(Row 2 is rank-blank with a populated rejection column → "rejected".)

```python
# app/domains/mutual_funds/services/tests/test_fund_resolver.py
"""Name → ranking-row resolution against a FIXTURE CSV (never the live one)."""

from pathlib import Path

import pytest

import app.domains.rebalancing.services.rebal_engine.fund_rank as fund_rank
from app.domains.mutual_funds.services.fund_resolver import resolve_fund

_FIXTURE = Path(__file__).parent / "fund_ranking_fixture.csv"


@pytest.fixture(autouse=True)
def _fixture_csv(monkeypatch):
    monkeypatch.setattr(fund_rank, "_CSV_PATH", _FIXTURE)
    for fn in ("get_fund_ranking", "get_all_rows", "get_rejection_reasons"):
        cached = getattr(fund_rank, fn, None)
        if cached is not None and hasattr(cached, "cache_clear"):
            cached.cache_clear()
    yield
    for fn in ("get_fund_ranking", "get_all_rows", "get_rejection_reasons"):
        cached = getattr(fund_rank, fn, None)
        if cached is not None and hasattr(cached, "cache_clear"):
            cached.cache_clear()


def test_recommended_fund_resolves_with_isin_and_category():
    r = resolve_fund("alpha large cap")
    assert r.status == "recommended"
    assert r.isin == "INFTEST00001" and r.sub_category == "Large Cap Fund"


def test_rejected_fund_carries_rejection_text():
    r = resolve_fund("beta mid cap")
    assert r.status == "rejected"
    assert "tenure" in (r.rejection_text or "")


def test_unknown_fund_is_unknown():
    assert resolve_fund("definitely not a real scheme 123").status == "unknown"


def test_stopword_only_query_is_not_guessed():
    assert resolve_fund("fund").status in ("ambiguous", "unknown")


def test_two_matches_are_ambiguous_never_guessed():
    r = resolve_fund("large cap")           # Alpha + Gamma both match
    assert r.status == "ambiguous"
    assert len(r.candidates) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/services/tests/test_fund_resolver.py -v`
Expected: FAIL with `ModuleNotFoundError: ... fund_resolver`

- [ ] **Step 3: Implement**

In `fund_rank.py`, add beside `get_fund_ranking` (same `@cache` + CSV-path pattern, reusing its row parser):

```python
@cache
def get_all_rows() -> list[FundRankRow]:
    """Every CSV row (recommended AND evaluated-but-rejected; rejected rows
    carry rank=0). Same cache/reload contract as get_fund_ranking."""
```

New `fund_resolver.py`:

```python
"""Resolve a customer's fund words against the live ranking CSV.

Three honest outcomes (spec: named_fund): recommended -> can be included in a
buy plan; rejected -> we answer with the CSV's own rejection reasons; unknown
-> we say we don't rank it. Matching is deliberately conservative: normalized
substring, and >1 distinct ISIN match = ambiguous (never guess a fund).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.domains.rebalancing.services.rebal_engine.fund_rank import (
    get_all_rows,
    get_rejection_reasons,
)

_STOPWORDS = {"fund", "plan", "direct", "growth", "the", "of"}


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("-", " ").split())


@dataclass(frozen=True)
class FundResolution:
    status: Literal["recommended", "rejected", "ambiguous", "unknown"]
    isin: str | None = None
    fund_name: str | None = None
    sub_category: str | None = None
    rejection_text: str | None = None
    candidates: tuple[str, ...] = ()


def resolve_fund(text: str) -> FundResolution:
    needle = _norm(text)
    if not needle or all(t in _STOPWORDS for t in needle.split()):
        return FundResolution(status="unknown")
    hits = {r.isin: r for r in get_all_rows() if needle in _norm(r.fund_name)}
    if not hits:
        return FundResolution(status="unknown")
    if len(hits) > 1:
        names = tuple(sorted(r.fund_name for r in hits.values())[:5])
        return FundResolution(status="ambiguous", candidates=names)
    row = next(iter(hits.values()))
    rejection = get_rejection_reasons().get(row.isin)
    if rejection:
        return FundResolution(status="rejected", isin=row.isin,
                              fund_name=row.fund_name, sub_category=row.sub_category,
                              rejection_text=rejection)
    return FundResolution(status="recommended", isin=row.isin,
                          fund_name=row.fund_name, sub_category=row.sub_category)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/services/tests/test_fund_resolver.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/fund_rank.py app/domains/mutual_funds/services/fund_resolver.py app/domains/mutual_funds/services/tests/test_fund_resolver.py app/domains/mutual_funds/services/tests/fund_ranking_fixture.csv
git commit -m "feat(preferences): conservative fund-name resolver over the full ranking CSV"
```

---

### Task 6: Unserved-preference telemetry helper

**Files:**
- Modify: `app/core/observability.py` (beside `capture_flow_completed`, ~line 190)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_preference_telemetry.py`

**Interfaces:**
- Produces (called from Task 9b's chat branches):

```python
def capture_preference_unserved(*, flow: str, failure_class: str,
                                session_id: object | None,
                                distinct_id: object | None) -> None: ...
```

Event name `preference_unserved`. `failure_class` is one of the fixed tokens: `"redirect"`, `"category_unranked"`, `"category_not_in_plan"`, `"fund_unknown"`, `"invalid_override"`, `"contradiction"`, `"named_include_deferred"`. **Properties carry ids and tokens only — never chat text** (spec boundary: PostHog has no content). `TurnContext` has no per-turn id — verified 2026-08-24 — so the join key is `session_id` (+ event timestamp) against the transcript rows in Postgres.

- [ ] **Step 1: Write the failing test**

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_preference_telemetry.py
"""preference_unserved: content-free, never raises, no-ops without a client."""

import app.core.observability as obs


class _FakeClient:
    def __init__(self):
        self.events = []

    def capture(self, event, distinct_id=None, properties=None):
        self.events.append((event, distinct_id, properties))


def test_capture_is_noop_without_client(monkeypatch):
    monkeypatch.setattr(obs, "_posthog_client", None)
    obs.capture_preference_unserved(flow="rebalancing", failure_class="redirect",
                                    session_id="s1", distinct_id="u1")  # must not raise


def test_capture_sends_ids_and_tokens_only(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(obs, "_posthog_client", fake)
    obs.capture_preference_unserved(flow="rebalancing", failure_class="fund_unknown",
                                    session_id="sess-42", distinct_id="user-7")
    ((event, distinct_id, props),) = fake.events
    assert event == "preference_unserved"
    assert distinct_id == "user-7"
    assert props == {"flow": "rebalancing", "failure_class": "fund_unknown",
                     "session_id": "sess-42"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_preference_telemetry.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'capture_preference_unserved'`

- [ ] **Step 3: Implement** (mirror `capture_flow_completed`'s null-client guard and swallow-all discipline)

```python
def capture_preference_unserved(
    *,
    flow: str,
    failure_class: str,
    session_id: object | None,
    distinct_id: object | None,
) -> None:
    """A customer preference we could not serve (spec 2026-08-24, telemetry).

    ``failure_class`` is a fixed token, never free text. NO chat content —
    PostHog holds no transcripts; join to the Postgres transcript rows by
    session_id + event timestamp to read the actual ask.
    """
    client = _posthog_client
    if client is None:
        return
    try:
        client.capture(
            "preference_unserved",
            distinct_id=str(distinct_id) if distinct_id else "backend",
            properties={
                "flow": flow,
                "failure_class": failure_class,
                "session_id": str(session_id) if session_id else None,
            },
        )
    except Exception:  # pragma: no cover - reporting must never raise
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_preference_telemetry.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/observability.py app/domains/rebalancing/services/rebal_engine/tests/test_preference_telemetry.py
git commit -m "feat(preferences): content-free preference_unserved PostHog event"
```

---

### Task 7: Detector eval set + baseline (BEFORE any detector change)

**Files:**
- Create: `AI_Agents/tests/test_rebal_detector_eval.py`
- Create: `app/domains/rebalancing/services/rebal_engine/tests/detector_ctx.py` (plain helper module — conftests are NOT importable modules; both this suite and the rebal tests import `make_detector_ctx` from here)
- Modify: `pyproject.toml` (register marker `rebal_detector_eval`)

**Interfaces:**
- Consumes: `run_suite` from `AI_Agents/tests/_eval_harness.py` (case list + runner + grader + threshold); `_detect_rebal_action` needs a live API key, so the suite is opt-in.
- Produces: the labeled question set (owner-reviewable) + a recorded baseline score for the CURRENT prompt. Task 8 must re-run this suite and not regress the baseline on old-vocabulary cases.

- [ ] **Step 1: Write the eval module** (this "test" is the deliverable; there is no fail-first step — the checkpoint is the baseline run)

```python
# AI_Agents/tests/test_rebal_detector_eval.py
"""Labeled eval for the rebalancing action detector (LIVE Haiku calls).

Run explicitly:  .venv-mac/bin/python -m pytest AI_Agents/tests/test_rebal_detector_eval.py -m rebal_detector_eval -v
Skipped without ANTHROPIC_API_KEY. Baseline (pre-preferences prompt,
recorded 2026-08-24 by Task 7): fill in `BASELINE_NOTE` after the first run.

Cases marked vocab="v1-pref" are EXPECTED TO FAIL until Task 8 ships the new
vocabulary; the threshold below counts only vocab="existing" cases, so this
file is safe to keep green before and after.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

import pytest

from tests._eval_harness import run_suite

pytestmark = pytest.mark.rebal_detector_eval

BASELINE_NOTE = "existing-vocab baseline: RUN_STEP_2_TO_FILL/8"  # Task 7 Step 2 replaces this with the measured count


@dataclass(frozen=True)
class Case:
    label: str
    question: str
    expect_mode: str
    vocab: str = "existing"                    # "existing" | "v1-pref"
    expect_override_keys: frozenset = field(default_factory=frozenset)
    expect_clarify: bool = False


CASES = [
    # ---- existing vocabulary (the no-regression floor) ----
    Case("narrate-why-sell", "why are you selling my HDFC fund?", "narrate"),
    Case("educate-exit-load", "what is exit load?", "educate"),
    Case("cf-tax-rate", "what if my tax rate were 20%?", "counterfactual_explore",
         expect_override_keys=frozenset({"effective_tax_rate"})),
    Case("cf-extra-cash", "what if I had 2 lakh more to deploy?",
         "counterfactual_explore", expect_override_keys=frozenset({"additional_cash_inr"})),
    Case("consolidate-count", "can you do this with just 4 funds?", "consolidate"),
    Case("consolidate-category", "put the new money only in large cap", "consolidate"),
    Case("redirect-lock", "don't sell my HDFC Top 100", "redirect"),
    Case("compute-rerun", "rebalance again with my latest holdings", "compute"),
    # ---- v1 preference vocabulary (Task 8 makes these pass) ----
    Case("tilt-delta", "increase my equity by 10 percent", "counterfactual_explore",
         vocab="v1-pref", expect_override_keys=frozenset({"asset_class_tilt"})),
    Case("tilt-absolute", "take my equity exposure to 70%", "counterfactual_explore",
         vocab="v1-pref", expect_override_keys=frozenset({"asset_class_tilt"})),
    Case("tilt-no-number", "increase my equity exposure", "counterfactual_explore",
         vocab="v1-pref"),
    Case("scope-only-equity", "I only want to invest in equity funds",
         "counterfactual_explore", vocab="v1-pref"),
    Case("weight-mid-cap", "I want more mid cap in this plan", "consolidate",
         vocab="v1-pref"),
    Case("exclude-elss", "nothing with a lock-in please", "consolidate",
         vocab="v1-pref"),
    Case("named-include", "use Parag Parikh Flexi Cap instead", "narrate",
         vocab="v1-pref"),   # Phase 1: honest "coming later" + demand telemetry
    Case("named-why-not", "why didn't you pick Quant Small Cap?", "narrate",
         vocab="v1-pref"),
    Case("stacked", "only equity, and more mid cap, max 4 funds", "consolidate",
         vocab="v1-pref"),
    Case("contradiction", "only debt funds but add more mid cap", "clarify",
         vocab="v1-pref", expect_clarify=True),
    Case("vague-safer", "make it safer", "clarify", vocab="v1-pref",
         expect_clarify=True),
    Case("oov-esg", "only ESG funds please", "redirect", vocab="v1-pref"),
]


def _runner(case: Case):
    from app.domains.rebalancing.services.rebal_engine.chat import _detect_rebal_action
    from app.domains.rebalancing.services.rebal_engine.tests.detector_ctx import (
        make_detector_ctx)
    # last_run=None exercises the pure-question path.
    return asyncio.run(_detect_rebal_action(None, make_detector_ctx(case.question)))


def _grader(case: Case, action):
    if action.mode != case.expect_mode:
        return False, f"mode={action.mode} want={case.expect_mode}"
    got_keys = frozenset((action.overrides or {}).keys())
    if case.expect_override_keys and not case.expect_override_keys <= got_keys:
        return False, f"override keys={sorted(got_keys)}"
    if case.expect_clarify and not action.clarification_question:
        return False, "no clarification_question"
    return True, ""


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="live eval")
def test_existing_vocabulary_floor():
    existing = [c for c in CASES if c.vocab == "existing"]
    report = run_suite(suite="rebal-detector-existing", cases=existing,
                       runner=_runner, grader=_grader,
                       threshold=len(existing) - 1)   # allow 1 flake in 8
    print(report.summary())


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="live eval")
def test_v1_preference_vocabulary():
    prefs = [c for c in CASES if c.vocab == "v1-pref"]
    # Threshold 0 until Task 8 lands; Task 8's Step 4 raises it to len-2.
    report = run_suite(suite="rebal-detector-v1pref", cases=prefs,
                       runner=_runner, grader=_grader, threshold=0)
    print(report.summary())
```

(If `run_suite`'s exact signature differs — check `_eval_harness.py:62` — adapt the two call sites, keeping cases/runner/grader/threshold semantics. `detector_ctx.py` contains one function, `make_detector_ctx(question: str) -> TurnContext`, extracted from however the existing detector tests in `rebal_engine/tests/` construct a TurnContext; the conftest fixture `detector_ctx` becomes a thin wrapper over it.)

Register the marker in `pyproject.toml` under the existing markers list: `rebal_detector_eval: live Haiku eval of the rebalancing action detector`.

- [ ] **Step 2: Run the baseline and record it**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_rebal_detector_eval.py -m rebal_detector_eval -v -s`
Record the existing-vocab pass count into `BASELINE_NOTE` (e.g. `"existing-vocab baseline: 8/8"`) and note the v1-pref count (expected ~0 — that's the point).
**CHECKPOINT: show the case list to the product owner for review before Task 8.**

- [ ] **Step 3: Run the offline collection sanity check** (no API key)

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_rebal_detector_eval.py -q`
Expected: both tests SKIPPED (marker + no key), collection clean.

- [ ] **Step 4: Commit**

```bash
git add AI_Agents/tests/test_rebal_detector_eval.py app/domains/rebalancing/services/rebal_engine/tests/detector_ctx.py pyproject.toml
git commit -m "test(preferences): labeled rebal detector eval + recorded pre-change baseline"
```

---

### Task 8: Detector vocabulary (schema + prompt)

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (`RebalanceAction` ~line 53, `_DETECT_REBAL_SYSTEM` ~line 103, `_INVALID_OVERRIDE_TEMPLATE` ~line 90)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_action_schema.py`

**Interfaces:**
- Consumes: baseline eval from Task 7.
- Produces (consumed by Task 9): new `RebalanceAction` fields, all Optional with defaults:
  - `scope_only_asset_classes: Optional[list[Literal["equity", "debt", "others"]]]`
  - `tilt_asset_class: Optional[Literal["equity", "debt", "others"]]`, `tilt_delta_pp: Optional[float]`, `tilt_target_pct: Optional[float]`
  - `excluded_categories: Optional[list[str]]` (customer words verbatim, like `allowed_categories`)
  - `category_weights: Optional[dict[str, float]]` (customer words → requested % of buys, 0–100)
  - `named_fund: Optional[str]`, `named_fund_intent: Optional[Literal["include", "why_not"]]`
  - override key `"asset_class_tilt"` documented in the `overrides` field description.

- [ ] **Step 1: Write the failing schema tests** (offline — no API)

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_action_schema.py
"""Schema-drift guards for the preference vocabulary on RebalanceAction."""

from app.domains.rebalancing.services.rebal_engine.chat import (
    _INVALID_OVERRIDE_TEMPLATE,
    RebalanceAction,
)


def test_preference_fields_default_to_none():
    a = RebalanceAction(mode="narrate")
    assert a.scope_only_asset_classes is None
    assert a.tilt_asset_class is None and a.tilt_delta_pp is None
    assert a.tilt_target_pct is None
    assert a.excluded_categories is None and a.category_weights is None
    assert a.named_fund is None and a.named_fund_intent is None


def test_stacked_preference_action_parses():
    a = RebalanceAction(mode="consolidate", target_fund_count=4,
                        category_weights={"mid cap": 30.0},
                        excluded_categories=["elss"])
    assert a.category_weights == {"mid cap": 30.0}


def test_invalid_override_template_mentions_tilt():
    assert "equity" in _INVALID_OVERRIDE_TEMPLATE.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_action_schema.py -v`
Expected: FAIL — unexpected keyword / missing attribute.

- [ ] **Step 3: Implement**

Add the fields to `RebalanceAction` exactly as in **Produces** (each with a one-line `description` telling Haiku to extract customer words verbatim and numbers only when stated — mirror the `allowed_categories` description style). Extend `_DETECT_REBAL_SYSTEM`:

- In the `counterfactual_explore` bullet, add to the allowed-override list:
  `asset_class_tilt` is NOT emitted directly by the model — instead document the three tilt fields: *"Exposure asks ('increase my equity', 'take equity to 70%', 'only equity funds') → counterfactual_explore. Fill tilt_asset_class; fill tilt_delta_pp ONLY when the customer states a relative number, tilt_target_pct ONLY for an absolute one; NEVER invent a number — leave both unset when none was said. 'Only X funds' → scope_only_asset_classes=[x]."*
- In the `consolidate` bullet, add this exact contract: *"'more/increase <category>' with a stated percent → category_weights {'<words>': pct}. With NO stated percent → category_weights {'<words>': 0} — the 0 is a sentinel meaning 'no number stated'; the app applies its documented default step and discloses it. 'no/without <category>' or 'nothing with a lock-in' → excluded_categories. A specific scheme name → mode narrate + named_fund + named_fund_intent ('use X' → include; 'why not X' → why_not) — BOTH intents ride on narrate in Phase 1 (include gets an honest 'coming later' with our reasoning; why_not gets the rejection reasons). Contradictory asks (excluded category also requested) → clarify, naming the conflict."*
- Update `_INVALID_OVERRIDE_TEMPLATE` to mention exposure changes are now supported: `"...tax rate, STCG offset budget, carry-forward losses, additional cash, or your equity/debt/gold exposure..."`.

(`category_weights` value `0` is the documented "no number stated" sentinel; Task 9 converts `0` → the +10pp-of-sleeve default and records it in `defaults_applied`.)

- [ ] **Step 4: Run tests + re-run the eval**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_action_schema.py -v` → PASS.
Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_rebal_detector_eval.py -m rebal_detector_eval -v -s`
Expected: existing-vocab floor holds ≥ baseline; v1-pref cases now mostly pass. Raise the v1-pref `threshold` to `len(prefs) - 2` in the eval file and update `BASELINE_NOTE` with both scores. Iterate on prompt wording (not thresholds) if below.

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py app/domains/rebalancing/services/rebal_engine/tests/test_action_schema.py AI_Agents/tests/test_rebal_detector_eval.py
git commit -m "feat(preferences): detector vocabulary - tilt/scope/weights/exclusions/named-fund"
```

---

### Task 9a: Extract `_handle_action` (pure refactor, zero behavior change)

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (mode ladder, ~lines 592–697)

**Interfaces:**
- Produces (used by 9b and Task 10 tests): `async def _handle_action(ctx: TurnContext, action: RebalanceAction) -> ChatHandlerResult` — the existing `if action.mode == "clarify"/"redirect"/"counterfactual_explore"/"consolidate"/"compute"/narrate-educate` chain moved verbatim out of the main handler; the main handler calls it after detect. No logic edits of any kind in this task.

- [ ] **Step 1: Extract the function** — cut the mode chain into `_handle_action`, replace the original site with `return await _handle_action(ctx, action)` (the narrate/educate tail that reads `last_run` moves with it; pass `last_run` as a third parameter if the tail needs it: `_handle_action(ctx, action, last_run)`).

- [ ] **Step 2: Run the full existing rebal suite to prove zero drift**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests -q`
Expected: green, no test edits needed — that is the definition of this refactor succeeding.

- [ ] **Step 3: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py
git commit -m "refactor(rebal): extract mode ladder into _handle_action (no behavior change)"
```

---

### Task 9b: Chat wiring (defaults policy, two-run tilt, extended consolidate, named-fund replies, telemetry)

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (`_handle_action` from 9a, `_counterfactual_explore` ~line 710, `_consolidate` ~line 774)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_preference_chat_wiring.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8. Key call shapes already in the file: `compute_rebalancing_result(..., persist=False, force_fresh_allocation=..., chat_ctx=...)`, `build_constraint_impact(baseline_response, changed_response, risk_profile=...)`, `format_relay_or_canned(...)`, `_format_or_fallback_rebal(..., constraint_impact=...)`.
- Produces: the user-facing behavior. The `constraint_impact` dict gains new keys the formatter passes through: `applied_preferences` (audit block, may carry `shortfall_note`), `tilt_note` (fixed caution string), and `count_bumped_to` (disclosed count bump, consolidate path).

- [ ] **Step 1: Write the failing tests** (monkeypatch the compute + formatter seams; no LLM, no DB)

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_preference_chat_wiring.py
"""Preference turns: defaults policy, two-run tilt impact, audit block, telemetry."""

import pytest

import app.domains.rebalancing.services.rebal_engine.chat as chat_mod


@pytest.fixture
def spy(monkeypatch):
    calls = {"compute": [], "impact": [], "telemetry": [], "format": []}

    async def fake_compute(**kw):
        calls["compute"].append(kw)
        return chat_mod.__dict__["_TEST_OUTCOME_FACTORY"]()  # helper added below

    monkeypatch.setattr(chat_mod, "compute_rebalancing_result", fake_compute)
    monkeypatch.setattr(
        "app.core.observability.capture_preference_unserved",
        lambda **kw: calls["telemetry"].append(kw),
    )

    async def fake_format(**kw):
        calls["format"].append(kw)
        return "formatted"

    monkeypatch.setattr(chat_mod, "_format_or_fallback_rebal", fake_format)
    return calls


async def test_numbered_tilt_runs_engine_twice_with_tilt_override(spy, detector_ctx):
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      tilt_asset_class="equity", tilt_delta_pp=10.0)
    await chat_mod._handle_preference_counterfactual(detector_ctx, action)
    assert len(spy["compute"]) == 2                      # baseline + requested
    tilted_kw = spy["compute"][1]
    ov = tilted_kw["chat_ctx"].chat_overrides
    assert "asset_class_tilt" in ov
    impact = spy["format"][0]["constraint_impact"]
    assert impact["applied_preferences"]["tilt"]["source"] == "customer_number"
    assert "tilt_note" in impact


async def test_no_number_tilt_defaults_to_band_edge_risk_score(spy, detector_ctx):
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      tilt_asset_class="equity")
    await chat_mod._handle_preference_counterfactual(detector_ctx, action)
    ov = spy["compute"][1]["chat_ctx"].chat_overrides
    assert "effective_risk_score" in ov                  # band-edge default path
    assert spy["compute"][1]["force_fresh_allocation"] is True
    impact = spy["format"][0]["constraint_impact"]
    assert impact["applied_preferences"]["tilt"]["source"] == "band_edge_default"


async def test_redirect_fires_content_free_telemetry(spy, detector_ctx):
    action = chat_mod.RebalanceAction(mode="redirect", redirect_reason="lock holdings")
    await chat_mod._handle_action(detector_ctx, action)   # extracted in Task 9a
    assert spy["telemetry"] == [dict(flow="rebalancing", failure_class="redirect",
                                     session_id=detector_ctx.session_id,
                                     distinct_id=detector_ctx.effective_user_id)]


async def test_unknown_named_fund_is_honest_and_logged(spy, detector_ctx, monkeypatch):
    monkeypatch.setattr(chat_mod, "resolve_fund",
                        lambda text: chat_mod.FundResolution(status="unknown"))
    action = chat_mod.RebalanceAction(mode="narrate", named_fund="mystery fund",
                                      named_fund_intent="why_not")
    result = await chat_mod._handle_named_fund(detector_ctx, action)
    assert "don't rank" in result.text.lower() or spy["format"]
    assert spy["telemetry"][0]["failure_class"] == "fund_unknown"


async def test_named_include_is_deferred_honestly_and_measured(spy, detector_ctx, monkeypatch):
    monkeypatch.setattr(chat_mod, "resolve_fund",
                        lambda text: chat_mod.FundResolution(
                            status="recommended", isin="I1", fund_name="Alpha",
                            sub_category="Large Cap Fund"))
    action = chat_mod.RebalanceAction(mode="narrate", named_fund="alpha",
                                      named_fund_intent="include")
    await chat_mod._handle_named_fund(detector_ctx, action)
    # Phase 1: never silently ignored — honest "coming later" reply + demand telemetry.
    assert spy["telemetry"][0]["failure_class"] == "named_include_deferred"
```

(`detector_ctx` comes from the same conftest fixture Task 7 uses; `_TEST_OUTCOME_FACTORY` is a tiny module-level test seam OR — preferred — build a real `RebalanceOutcome` stub in conftest with a minimal `RebalancingComputeResponse` fixture that `build_constraint_impact` accepts; the rebal tests conftest already builds responses for the consolidation tests — reuse it.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_preference_chat_wiring.py -v`
Expected: FAIL — `_handle_preference_counterfactual` does not exist.

- [ ] **Step 3: Implement in `chat.py`**

New handler, dispatched from the mode ladder when tilt/scope fields are set (before the plain-overrides `counterfactual_explore` branch):

```python
async def _handle_preference_counterfactual(
    ctx: TurnContext, action: RebalanceAction
) -> ChatHandlerResult:
    """Two-run comply-and-caution for exposure preferences (spec 2026-08-24).

    Run 1 = recommended plan. Run 2 = requested plan (tilt/scope applied).
    Deviation between the two is the caution lens. Stateless: persist=False.
    """
    from app.domains.mutual_funds.services.investment_preferences import (
        AT_EDGE_STEP_PP, band_edge_score, normalize_tilt)
    from app.domains.rebalancing.services.rebal_engine.constraint_impact import (
        build_constraint_impact)

    baseline = await compute_rebalancing_result(
        user=ctx.user_ctx, user_question=ctx.user_question, db=ctx.db,
        acting_user_id=ctx.effective_user_id, chat_session_id=ctx.session_id,
        persist=False, chat_ctx=ctx)
    if baseline.blocking_message is not None or baseline.response is None:
        return await _blocking_or_degraded(ctx, baseline)   # small shared helper, extract from _counterfactual_explore

    current_mix = _current_target_mix_pct(baseline.response)  # rollup helper: reuse constraint_impact._planned_mix_pct
    tilt = normalize_tilt(
        current_mix,
        scope_only=action.scope_only_asset_classes,
        tilt_asset_class=action.tilt_asset_class,
        tilt_delta_pp=action.tilt_delta_pp,
        tilt_target_pct=action.tilt_target_pct)

    applied: dict[str, Any]
    if tilt.needs_band_edge_default:
        category = getattr(ctx.user_ctx, "risk_profile", None)
        current_score = getattr(ctx.user_ctx, "effective_risk_score", None)
        edge = band_edge_score(category)
        if current_score is not None and current_score >= edge:
            # already at the edge -> fixed step beyond it via asset_class_tilt
            step = normalize_tilt(current_mix, scope_only=None,
                                  tilt_asset_class=action.tilt_asset_class,
                                  tilt_delta_pp=AT_EDGE_STEP_PP, tilt_target_pct=None)
            overrides = {"asset_class_tilt": step.mix_pct}
            applied = {"tilt": {"source": "at_edge_fixed_step",
                                "step_pp": AT_EDGE_STEP_PP}}
            fresh = False
        else:
            overrides = {"effective_risk_score": edge}
            applied = {"tilt": {"source": "band_edge_default",
                                "edge_score": edge, "band": category}}
            fresh = True
    else:
        overrides = {"asset_class_tilt": tilt.mix_pct}
        applied = {"tilt": {"source": "customer_number",
                            "requested_mix_pct": tilt.mix_pct,
                            **({"defaults": tilt.defaults_applied}
                               if tilt.defaults_applied else {})}}
        fresh = False

    requested = await compute_rebalancing_result(
        user=ctx.user_ctx, user_question=ctx.user_question, db=ctx.db,
        acting_user_id=ctx.effective_user_id, chat_session_id=ctx.session_id,
        persist=False, force_fresh_allocation=fresh,
        chat_ctx=with_chat_overrides(ctx, overrides))
    if requested.blocking_message is not None or requested.response is None:
        return await _blocking_or_degraded(ctx, requested)

    impact = build_constraint_impact(
        baseline.response, requested.response,
        risk_profile=getattr(ctx.user_ctx, "risk_profile", None))
    impact["applied_preferences"] = applied              # audit trail (stateless spec)
    impact["tilt_note"] = (
        "The requested plan changes SELLS as well as buys versus our "
        "recommendation; tax estimates shown are for the requested plan.")
    text = await _format_or_fallback_rebal(
        ctx=ctx, response=requested.response,
        fallback_brief=requested.formatted_text or "",
        action_mode="counterfactual_explore",
        goal_buckets=requested.goal_buckets, constraint_impact=impact)
    return ChatHandlerResult(text=text, snapshot_id=None,
                             rebalancing_recommendation_id=None)
```

Also in this step:
1. `_consolidate`: resolve `action.excluded_categories` through `resolve_categories` (same unresolved handling as allowed, `failure_class="category_unranked"` telemetry when nothing resolves); convert `action.category_weights` — resolve words, divide stated pct by 100; value `0` (detector's no-number sentinel) → `0.10` default with `applied["category_weights"]["source"] = "default_step"`; pass `excluded_categories` / `category_weight_targets` into `ConsolidationConstraints`; surface the `weight_category_not_in_plan` error with an honest reply (mirror `category_not_in_plan`'s text + telemetry `failure_class="category_not_in_plan"`); when the count-trim bumped the fund count to the protected-category count, set `impact["count_bumped_to"]` so the formatter discloses it (same pattern as `defaulted_fund_count`); attach `impact["applied_preferences"]`.
2. Named fund: `_handle_named_fund(ctx, action)` — `resolve_fund(action.named_fund)`; `rejected` + intent `why_not` → narrate with `{"named_fund_rejection": {"fund": name, "reasons": rejection_text}}` passed as `constraint_impact` so the formatter grounds the answer in the CSV's own words; `recommended` + `why_not` → narrate "it IS on our list" with its `selection_reason`; ANY status + intent `include` → honest deferred reply ("including a specific fund in the plan is coming; here's why we picked what we picked") + `capture_preference_unserved(failure_class="named_include_deferred", ...)` — Phase 2 implements inclusion at the input-builder seam; `unknown`/`ambiguous` → honest text (offer the candidate names when ambiguous) + `failure_class="fund_unknown"`.
3. Redirect branch and `_counterfactual_explore`'s invalid-override branch: add `capture_preference_unserved(flow="rebalancing", failure_class="redirect"|"invalid_override", session_id=ctx.session_id, distinct_id=ctx.effective_user_id)`.
4. Mode dispatch inside `_handle_action` (from 9a): route `counterfactual_explore` with tilt/scope fields → `_handle_preference_counterfactual`; `named_fund` set → `_handle_named_fund`; detector-flagged contradiction arrives as `clarify` (no new dispatch needed).
5. Zero-class shortfall disclosure (Task 3's note): in `_handle_preference_counterfactual`, when a requested class has ~0% in `current_mix`, append `{"shortfall_note": "no <class> holdings exist to scale — the request was spread over the present classes"}` to `applied_preferences` so the reply says it plainly.
6. **Dual-source labeling on tilt turns (architecture review 2026-08-24):** `build_rebal_facts_pack` reads `response.practical_allocation`, which stays UNTILTED while rows/targets are tilted — so the tilt-turn facts pack carries both the recommended mix and the requested mix. That is the comply-and-caution comparison, but the formatter must be told which is which: extend `tilt_note` to state explicitly that the practical/ideal-allocation figures are the RECOMMENDED baseline and the trade rows are the REQUESTED plan, and never to average or blend the two. Add one wiring test asserting `tilt_note` names both sources.

- [ ] **Step 4: Run the wiring tests + full rebal suite**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests -v`
Expected: all PASS (new + pre-existing).

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py app/domains/rebalancing/services/rebal_engine/tests/test_preference_chat_wiring.py
git commit -m "feat(preferences): rebal chat wiring - defaults policy, two-run tilt, extended consolidate, named-fund replies, telemetry"
```

---

### Task 10: Integration guard, docs, full suite

**Files:**
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_preference_integration.py`
- Modify: `AI_Agents/src/Rebalancing/CLAUDE.md` (consolidation bullet), `app/domains/rebalancing/CLAUDE.md` or `rebal_engine`'s CLAUDE.md if present (one gotcha line each)

**Interfaces:** consumes everything; produces the spec's two structural guarantees as tests.

- [ ] **Step 1: Write the failing integration tests**

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_preference_integration.py
"""Spec guarantees: caution always present; unserved never silent."""

# Reuses the spy/detector_ctx fixtures from test_preference_chat_wiring.


async def test_every_preference_turn_carries_impact_and_audit(spy, detector_ctx):
    import app.domains.rebalancing.services.rebal_engine.chat as chat_mod
    action = chat_mod.RebalanceAction(mode="counterfactual_explore",
                                      tilt_asset_class="equity", tilt_target_pct=70.0)
    await chat_mod._handle_preference_counterfactual(detector_ctx, action)
    impact = spy["format"][0]["constraint_impact"]
    assert "applied_preferences" in impact and "tilt_note" in impact
    assert impact["applied_preferences"]["tilt"]["source"] == "customer_number"


async def test_unserved_paths_always_fire_telemetry(spy, detector_ctx):
    import app.domains.rebalancing.services.rebal_engine.chat as chat_mod
    action = chat_mod.RebalanceAction(mode="redirect", redirect_reason="x")
    await chat_mod._handle_action(detector_ctx, action)
    assert spy["telemetry"], "redirect must never be silent in telemetry"
```

- [ ] **Step 2: Run to verify green** (these should pass immediately if Task 9 is correct — a failure here is a Task 9 bug)

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_preference_integration.py -v`

- [ ] **Step 3: Update CLAUDE.md files** (2–3 lines total; CLAUDE.md is exempt from the no-docs rule)

- `AI_Agents/src/Rebalancing/CLAUDE.md` consolidation bullet: note the constraint set now includes exclusions and weight targets; composition order filters→weights→count; the legacy-only path runs the original F3-B code byte-for-byte.
- Rebalancing domain CLAUDE.md gotchas: one bullet — "preference turns are stateless (`persist=False`); the audit trail is `constraint_impact.applied_preferences`; `preference_unserved` PostHog event carries ids only, never text."

- [ ] **Step 4: Full suite + lint**

Run: `.venv-mac/bin/python -m pytest app -q` and `.venv-mac/bin/python -m ruff check app AI_Agents/src/Rebalancing`
Expected: green; fix anything the sweep catches (including `test_temperature_is_pinned.py`).

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/tests/test_preference_integration.py AI_Agents/src/Rebalancing/CLAUDE.md app/domains/rebalancing/CLAUDE.md
git commit -m "test(preferences): integration guards for caution + telemetry invariants; CLAUDE.md notes"
```

---

## Deferred to Phase 2 (separate plan, after this ships)

SIP/lump-sum parity via `ainv_engine/chat.py` + the shared reshape; persisted ainv runs then carry the `applied_preferences` payload on the run row (they persist, unlike rebal preference turns). **Named-fund inclusion** — at its correct seam: the input builder injects the resolved fund as a ranked row so the engine computes real numbers for it (Phase 1's `named_include_deferred` telemetry measures the demand). Out entirely (spec): sell-side locks, tax-shaped sell filters, tranches, theme-level sectoral, persistence, agent loop.
