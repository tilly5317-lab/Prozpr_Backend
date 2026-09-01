# Rebalancing Preferences (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make market-cap (large/mid/small) a first-class re-run dial in rebalancing chat, compose it with the existing asset-class + tax/cash preferences in one merged engine run, make the fund-count reshape subgroup-aware, surface SEBI categories as tables, and add a synchronous-execution guardrail.

**Architecture:** A market-cap tilt is the asset-class tilt "one level lower": a new `_apply_market_cap_tilt` rescales the three equity beta subgroups right after `_apply_asset_class_tilt`, before targets are assigned; the rest of the engine sells+buys to hit it. The chat handler builds ONE merged override dict (asset-class + market-cap + tax/cash) for the existing two-run comply-and-caution path. The post-engine count reshape is made subgroup-aware so it can't undo a tilt.

**Tech Stack:** Python 3, pydantic v2, SQLAlchemy async, pytest (`asyncio_mode=auto`). Engine package `AI_Agents/src/Rebalancing` (bare imports via `sys.path`); app layer `app/domains/rebalancing/services/rebal_engine`.

## Global Constraints

- **Run tests with** `.venv-mac/bin/python -m pytest` (config in `pyproject.toml`; `pythonpath = ["AI_Agents/src", "."]` so bare `Rebalancing.*` imports resolve).
- **Every `ChatAnthropic(...)` pins `temperature=0` as a literal** (a repo scan test enforces it).
- **Bump `ENGINE_VERSION`** (`AI_Agents/src/Rebalancing/config.py`, currently `"1.5.0"`) on any output-altering engine change — done once in Task 2.
- **Market-cap subgroup mapping:** `large → low_beta_equities`, `mid → medium_beta_equities`, `small → high_beta_equities`. These are long-term-only subgroups (`emergency/short_term/medium_term` are 0).
- **Market-cap default steps:** "more X" = `+10%` relative to current; "X heavy/mostly" = `+50%` relative; always upward, feasibility-capped at 100%; zero-current → ask (don't fabricate).
- **Do NOT** touch sell-side logic (no `do_not_sell`), and do NOT add `do_not_buy` — both out of scope this phase.
- **sqlite test gotcha:** use letter-bearing UUIDs (all-digit coerce to float); create only the table(s) under test.
- **Leave changes in the working tree; commit per-task only as the plan's commit steps say.** Co-author trailer on commits: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File map

- `AI_Agents/src/Rebalancing/models.py` — add `market_cap_tilt` field to `RebalancingComputeRequest` (line ~163, beside `asset_class_tilt`).
- `AI_Agents/src/Rebalancing/pipeline.py` — add `_apply_market_cap_tilt` + `_SUBGROUP_MARKET_CAP`; wire into `run_rebalancing` (seam at lines 273-283).
- `AI_Agents/src/Rebalancing/config.py` — bump `ENGINE_VERSION`.
- `AI_Agents/src/Rebalancing/consolidation.py` — make `_reshape_legacy`/`_reshape_extended` group by `asset_subgroup`.
- `app/domains/rebalancing/services/rebal_engine/overrides.py` — add `"market_cap_tilt"` to the allow-list.
- `app/domains/rebalancing/services/rebal_engine/input_builder.py` — read + pass `market_cap_tilt` (lines ~439, ~442).
- `app/domains/mutual_funds/services/investment_preferences.py` — add `normalize_market_cap_tilt` + generalise `_renormalized`.
- `app/domains/rebalancing/services/rebal_engine/chat.py` — detector fields, current-mix helper, handler compose.
- `AI_Agents/src/persona.py` — synchronous-execution rule in `SHARED_MECHANICS`.
- Tests under each package's `Testing/` or `tests/` folder (see tasks).

---

### Task 1: Plumb `market_cap_tilt` through the request (inert)

Adds the field end-to-end so a tilt can be passed; no behaviour yet (a `None` tilt must be byte-identical to today).

**Files:**
- Modify: `AI_Agents/src/Rebalancing/models.py:163`
- Modify: `app/domains/rebalancing/services/rebal_engine/overrides.py:24-34`
- Modify: `app/domains/rebalancing/services/rebal_engine/input_builder.py:439,442`
- Test: `AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py` (new)

**Interfaces:**
- Produces: `RebalancingComputeRequest.market_cap_tilt: dict[str, float] | None` (keys `"large"|"mid"|"small"`, values are % of the beta sleeve). Override key `"market_cap_tilt"` accepted by `effective_param`.

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py
from decimal import Decimal

from Rebalancing.Testing.conftest import make_request  # shared helper (conftest.py:17)


def test_request_accepts_market_cap_tilt_and_defaults_none():
    req = make_request([], Decimal("5000000"))  # empty rows is valid; helper supplies the rest
    assert req.market_cap_tilt is None
    req2 = req.model_copy(update={"market_cap_tilt": {"large": 20, "mid": 30, "small": 50}})
    assert req2.market_cap_tilt == {"large": 20, "mid": 30, "small": 50}
```

> `make_request(rows, total_corpus, **overrides)` (`AI_Agents/src/Rebalancing/Testing/conftest.py:17`) builds the required nested `PracticalAllocationInput`, `tax_regime`, and `effective_tax_rate_pct`. The `empty_holdings_request` fixture (conftest.py:42) is an equivalent alternative.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py::test_request_accepts_market_cap_tilt_and_defaults_none -v`
Expected: FAIL — `market_cap_tilt` is not a field.

- [ ] **Step 3: Add the field**

In `models.py`, directly below `asset_class_tilt: dict[str, float] | None = None` (line 163):

```python
    market_cap_tilt: dict[str, float] | None = None  # {large,mid,small} abs % of the equity beta sleeve — spec 2026-08-30
```

- [ ] **Step 4: Add the override key**

In `overrides.py`, inside `_REBAL_ALLOWED_OVERRIDE_KEYS` (after `"pure_equity_only"`):

```python
        "market_cap_tilt",  # dict {large,mid,small: abs pct of beta sleeve} — spec 2026-08-30
```

- [ ] **Step 5: Read + pass it in the input builder**

In `input_builder.py`, beside the tilt reads (line ~439):

```python
    market_cap_override = effective_param(ctx, "market_cap_tilt", None)
```

and in the `RebalancingComputeRequest(...)` construction, beside `asset_class_tilt=tilt_override,` (line ~442):

```python
        market_cap_tilt=market_cap_override,
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/Rebalancing/models.py app/domains/rebalancing/services/rebal_engine/overrides.py app/domains/rebalancing/services/rebal_engine/input_builder.py AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py
git commit -m "feat(rebal): plumb market_cap_tilt through request + override allow-list"
```

---

### Task 2: `_apply_market_cap_tilt` rescale + wire into the pipeline

The core new engine logic. Rescales the three beta subgroups to the requested large/mid/small split, holding their combined total fixed, mirroring `_apply_asset_class_tilt` exactly.

**Files:**
- Modify: `AI_Agents/src/Rebalancing/pipeline.py` (add function after `_apply_asset_class_tilt` at line 261; wire at 273-283)
- Modify: `AI_Agents/src/Rebalancing/config.py` (bump `ENGINE_VERSION`)
- Test: `AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py` (extend)

**Interfaces:**
- Consumes: `RebalancingComputeRequest.market_cap_tilt` (Task 1).
- Produces: `_apply_market_cap_tilt(rows: list[AggregatedSubgroupRow], mix: dict[str,float]) -> list[AggregatedSubgroupRow]`; applied in `run_rebalancing` after the asset-class tilt.

- [ ] **Step 1: Write the failing tests**

```python
# append to AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py
from Rebalancing.pipeline import _apply_market_cap_tilt
from asset_allocation_pydantic.models import AggregatedSubgroupRow  # NOT Rebalancing.models


def _beta_rows(large, mid, small):
    def row(sg, total):
        return AggregatedSubgroupRow(
            subgroup=sg, emergency=0.0, short_term=0.0,
            medium_term=0.0, long_term=total, total=total,
        )
    return [
        row("low_beta_equities", large),
        row("medium_beta_equities", mid),
        row("high_beta_equities", small),
        row("short_debt", 500.0),  # a non-equity row must be untouched
    ]


def test_market_cap_tilt_hits_target_split_and_preserves_sleeve_total():
    rows = _beta_rows(large=300, mid=200, small=100)  # sleeve total 600
    out = {r.subgroup: r.total for r in _apply_market_cap_tilt(rows, {"large": 20, "mid": 30, "small": 50})}
    assert out["low_beta_equities"] == 120.0    # 20% of 600
    assert out["medium_beta_equities"] == 180.0  # 30% of 600
    assert out["high_beta_equities"] == 300.0    # 50% of 600
    assert out["short_debt"] == 500.0            # untouched
    assert out["low_beta_equities"] + out["medium_beta_equities"] + out["high_beta_equities"] == 600.0


def test_market_cap_tilt_writes_total_field():
    rows = _beta_rows(300, 200, 100)
    out = {r.subgroup: r for r in _apply_market_cap_tilt(rows, {"large": 20, "mid": 30, "small": 50})}
    assert out["high_beta_equities"].total == 300.0  # .total, not just .long_term


def test_market_cap_tilt_zero_current_subgroup_respread_over_present():
    # No small-cap present; request still names it -> its share re-spreads over present caps.
    rows = _beta_rows(large=300, mid=300, small=0)  # sleeve 600
    out = {r.subgroup: r.total for r in _apply_market_cap_tilt(rows, {"large": 10, "mid": 40, "small": 50})}
    # present={large:10,mid:40}, present_share=50 -> large=600*10/50=120, mid=600*40/50=480
    assert out["low_beta_equities"] == 120.0
    assert out["medium_beta_equities"] == 480.0
    assert out["high_beta_equities"] == 0.0


def test_market_cap_tilt_none_is_identity():
    rows = _beta_rows(300, 200, 100)
    assert _apply_market_cap_tilt(rows, None) == rows
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py -v`
Expected: FAIL — `_apply_market_cap_tilt` not defined.

- [ ] **Step 3: Implement the function**

In `pipeline.py`, after `_apply_asset_class_tilt` (after line 261):

```python
# The engine's equity beta subgroup -> its market-cap bucket.
_SUBGROUP_MARKET_CAP = {
    "low_beta_equities": "large",
    "medium_beta_equities": "mid",
    "high_beta_equities": "small",
}


def _apply_market_cap_tilt(rows, mix):
    """Rescale the equity beta subgroups (large/mid/small) to ``mix`` — an absolute
    {large,mid,small} split of the beta sleeve — holding their combined total fixed.

    Mirrors ``_apply_asset_class_tilt`` one level lower: present-subgroup re-spread
    (a requested share for a zero-current subgroup is re-spread over the present
    subgroups), every bucket field scaled by the same per-subgroup factor. Non-beta
    rows are untouched.
    """
    if not mix:
        return rows
    sleeve_total = sum(r.total for r in rows if r.subgroup in _SUBGROUP_MARKET_CAP)
    if sleeve_total <= 0:
        return rows
    current: dict[str, float] = {}
    for r in rows:
        cap = _SUBGROUP_MARKET_CAP.get(r.subgroup)
        if cap is not None:
            current[cap] = current.get(cap, 0.0) + r.total
    present = {c: p for c, p in mix.items() if current.get(c, 0.0) > 0}
    present_share = sum(present.values())
    if present_share <= 0:
        return rows
    factors = {
        c: (sleeve_total * (p / present_share)) / current[c] for c, p in present.items()
    }
    out = []
    for r in rows:
        cap = _SUBGROUP_MARKET_CAP.get(r.subgroup)
        f = factors.get(cap) if cap is not None else None
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

- [ ] **Step 4: Wire into `run_rebalancing`**

Replace the tilt block in `run_rebalancing` (lines 273-283) with:

```python
    practical_for_targets = practical
    if request.asset_class_tilt or request.market_cap_tilt:
        tilted = practical.aggregated_subgroups
        if request.asset_class_tilt:
            tilted = _apply_asset_class_tilt(
                tilted, request.asset_class_tilt, pure_equity=request.pure_equity_only,
            )
        if request.market_cap_tilt:
            tilted = _apply_market_cap_tilt(tilted, request.market_cap_tilt)
        practical_for_targets = practical.model_copy(update={"aggregated_subgroups": tilted})
    rows_with_targets = _assign_subgroup_targets(
        request.rows, practical_for_targets, request.rounding_step
    )
```

- [ ] **Step 5: Bump ENGINE_VERSION**

In `config.py`, change `ENGINE_VERSION = "1.5.0"` → `ENGINE_VERSION = "1.6.0"`.

- [ ] **Step 6: Run tests**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py -v`
Expected: PASS (all four).

- [ ] **Step 7: Run the existing engine suite to confirm no regression**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing -v`
Expected: PASS (a `None` tilt is identity; only `ENGINE_VERSION` string changed — update any test asserting `"1.5.0"` to `"1.6.0"` if one exists).

- [ ] **Step 8: Commit**

```bash
git add AI_Agents/src/Rebalancing/pipeline.py AI_Agents/src/Rebalancing/config.py AI_Agents/src/Rebalancing/Testing/test_market_cap_tilt.py
git commit -m "feat(rebal): market-cap tilt rescales beta subgroups in the pipeline"
```

---

### Task 3: `normalize_market_cap_tilt` (absolute mix from a spoken ask)

Pure function: turn "more small cap" / "small-cap heavy" into an absolute {large,mid,small} mix using the current split + the relative-step rules.

**Files:**
- Modify: `app/domains/mutual_funds/services/investment_preferences.py`
- Test: `app/domains/mutual_funds/services/tests/test_investment_preferences.py` (EXISTS — extend the current `normalize_tilt` suite, do not create a new file)

**Interfaces:**
- Consumes: current beta-sleeve mix `dict[str,float]` (keys large/mid/small).
- Produces: `normalize_market_cap_tilt(current_mix_pct, *, cap: str, heavy: bool) -> MarketCapTiltResult` with `.mix_pct: dict|None`, `.zero_current: bool`, `.default_step_applied: bool`.

- [ ] **Step 1: Write the failing tests**

```python
# app/domains/mutual_funds/services/tests/test_investment_preferences.py
from app.domains.mutual_funds.services.investment_preferences import (
    normalize_market_cap_tilt,
)


def test_more_small_cap_is_plus_10pct_relative():
    r = normalize_market_cap_tilt({"large": 50, "mid": 30, "small": 20}, cap="small", heavy=False)
    # small 20 -> 22 (+10% rel); large/mid scale down pro-rata to fill 78
    assert round(r.mix_pct["small"], 2) == 22.0
    assert round(r.mix_pct["large"] + r.mix_pct["mid"] + r.mix_pct["small"], 2) == 100.0
    assert r.default_step_applied is True
    assert r.zero_current is False


def test_small_cap_heavy_is_plus_50pct_relative_and_uncapped_at_50():
    r = normalize_market_cap_tilt({"large": 20, "mid": 20, "small": 60}, cap="small", heavy=True)
    # already 60 -> 60*1.5 = 90 (goes above, feasibility cap 100)
    assert round(r.mix_pct["small"], 2) == 90.0


def test_zero_current_small_cap_signals_ask():
    r = normalize_market_cap_tilt({"large": 60, "mid": 40, "small": 0}, cap="small", heavy=False)
    assert r.mix_pct is None
    assert r.zero_current is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/services/tests/test_investment_preferences.py -k market_cap -v`
Expected: FAIL — function not defined.

- [ ] **Step 3: Generalise `_renormalized` and add the normalizer**

In `investment_preferences.py`, change `_renormalized` to accept the class tuple (backward-compatible — existing callers pass two args):

```python
def _renormalized(mix, pinned, classes=ASSET_CLASSES):
    """Hold ``pinned`` classes fixed; scale the rest pro-rata to sum 100."""
    free = [c for c in classes if c not in pinned]
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
```

Then add, below `normalize_tilt`:

```python
MARKET_CAPS = ("large", "mid", "small")
DEFAULT_CAP_STEP_REL = 0.10   # "more X" = +10% of current
HEAVY_CAP_STEP_REL = 0.50     # "X heavy / mostly X" = +50% of current


@dataclass(frozen=True)
class MarketCapTiltResult:
    mix_pct: dict[str, float] | None
    zero_current: bool = False
    default_step_applied: bool = False


def normalize_market_cap_tilt(current_mix_pct, *, cap, heavy):
    """Absolute {large,mid,small} mix from a spoken ask. Relative step, upward,
    feasibility-capped at 100. Zero-current cap -> ask (mix_pct None)."""
    mix = {c: float(current_mix_pct.get(c, 0.0)) for c in MARKET_CAPS}
    cur = mix.get(cap, 0.0)
    if cur <= 0.0:
        return MarketCapTiltResult(mix_pct=None, zero_current=True)
    step = HEAVY_CAP_STEP_REL if heavy else DEFAULT_CAP_STEP_REL
    target = min(100.0, cur * (1.0 + step))
    return MarketCapTiltResult(
        mix_pct=_renormalized(mix, {cap: target}, classes=MARKET_CAPS),
        default_step_applied=not heavy,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest app/domains/mutual_funds/services/tests/test_investment_preferences.py -v`
Expected: PASS (new + existing `normalize_tilt` tests still green — the `_renormalized` default keeps old behaviour).

- [ ] **Step 5: Commit**

```bash
git add app/domains/mutual_funds/services/investment_preferences.py app/domains/mutual_funds/services/tests/test_investment_preferences.py
git commit -m "feat(prefs): normalize_market_cap_tilt (relative step, zero-current ask)"
```

---

### Task 4: Detector extraction + handler compose (one merged override dict)

Detect a market-cap ask; build the `market_cap_tilt` override from the baseline plan's current split; merge ALL engine-side overrides (asset-class + market-cap + tax/cash) into one dict; ask on zero-current.

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (RebalanceAction fields ~63-162; `_DETECT_REBAL_SYSTEM` ~178-358; `_handle_preference_counterfactual` ~964-1086; add `_current_market_cap_mix_pct`)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_chat.py` (extend); `AI_Agents/tests/test_rebal_detector_eval.py` (extend)

**Interfaces:**
- Consumes: `normalize_market_cap_tilt` (Task 3); `market_cap_tilt` request field (Task 1).
- Produces: `RebalanceAction.market_cap` (`Literal["large","mid","small"] | None`) and `RebalanceAction.market_cap_heavy` (`bool`); `_current_market_cap_mix_pct(response) -> dict[str,float]`.

- [ ] **Step 1: Write the failing test (handler builds a merged override dict)**

```python
# app/domains/rebalancing/services/rebal_engine/tests/test_chat.py (new test)
import app.domains.rebalancing.services.rebal_engine.chat as chat


def test_current_market_cap_mix_pct_buckets_beta_subgroups():
    class SG:
        def __init__(self, asset_subgroup, final):
            self.asset_subgroup = asset_subgroup
            self.suggested_final_holding_inr = final   # the REAL SubgroupSummary field
    class Resp:
        subgroups = [SG("low_beta_equities", 300), SG("medium_beta_equities", 200),
                     SG("high_beta_equities", 100), SG("short_debt", 999)]
    mix = chat._current_market_cap_mix_pct(Resp())
    assert round(mix["large"], 1) == 50.0   # 300/600
    assert round(mix["mid"], 1) == 33.3
    assert round(mix["small"], 1) == 16.7
```

> AUDIT-CONFIRMED field: `SubgroupSummary` (`models.py:291-320`) has **no**
> `planned_final_inr`. The per-subgroup "after rebalance" amount is
> **`suggested_final_holding_inr`** (`models.py:313`) — equal to the sum of that
> subgroup's facts-pack `buckets[].planned_final_inr` (= current+buy−sell), the same
> amount `_planned_mix_pct` uses at asset-class level. Do NOT use `planned_final_inr`
> on `response.subgroups` — it silently returns 0 and every market-cap ask misfires
> into the zero-current branch.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_chat.py -k market_cap_mix -v`
Expected: FAIL — helper not defined.

- [ ] **Step 3: Add the detector fields**

In `RebalanceAction` (chat.py), add:

```python
    market_cap: Optional[Literal["large", "mid", "small"]] = Field(
        default=None,
        description=(
            "The market-cap sleeve the customer wants MORE of ('more small cap', "
            "'tilt to midcaps', 'small-cap heavy'). large/mid/small only."
        ),
    )
    market_cap_heavy: Optional[bool] = Field(
        default=False,
        description="True for a 'heavy/mostly/lots of' qualifier ('small-cap heavy'); else False.",
    )
```

- [ ] **Step 4: Add the current-mix helper (mirror `_planned_mix_pct`)**

Add near `_current_target_mix_pct` in chat.py:

```python
def _current_market_cap_mix_pct(response) -> dict[str, float]:
    """Beta-sleeve split (large/mid/small %) of the plan's targets — the market-cap
    tilt baseline. Buckets the three equity beta subgroups by their post-rebalance
    holding (the subgroup-level counterpart of _planned_mix_pct)."""
    sub_of_cap = {"low_beta_equities": "large",
                  "medium_beta_equities": "mid",
                  "high_beta_equities": "small"}
    amt = {"large": 0.0, "mid": 0.0, "small": 0.0}
    for sg in getattr(response, "subgroups", []) or []:
        cap = sub_of_cap.get(getattr(sg, "asset_subgroup", None))
        if cap:
            amt[cap] += float(getattr(sg, "suggested_final_holding_inr", 0) or 0)
    total = sum(amt.values())
    if total <= 0:
        return {c: 0.0 for c in amt}
    return {c: v * 100.0 / total for c, v in amt.items()}
```

- [ ] **Step 5: Restructure `_handle_preference_counterfactual` to compose tilts + count**

This is a genuine RESTRUCTURE of the handler body (chat.py ~986-1086), not an insertion: the current shape calls `normalize_tilt`, then early-returns at `chat.py:995-997` (`if tilt.mix_pct is None: return await _counterfactual_explore(...)`) *before* any market-cap logic — so a pure market-cap ask would silently drop the tilt. Make four changes.

**(1) Init `overrides`/`applied` up front, compute BOTH tilts, and only fall through when NEITHER is expressed.** Replace the block from `normalize_tilt` (chat.py:986) down to and including the `requested_run = compute_rebalancing_result(...)` call + its `early` guard (chat.py:1035) with:

```python
    from app.domains.mutual_funds.services.investment_preferences import (
        normalize_market_cap_tilt,
    )
    from Rebalancing.consolidation import (  # type: ignore[import-not-found]
        ConsolidationConstraints, constraints_active, reshape_response,
    )

    overrides: dict[str, Any] = {
        k: v for k, v in (action.overrides or {}).items()
        if k in _REBAL_ALLOWED_OVERRIDE_KEYS          # merge tax/cash, allow-list only
    }
    applied: dict[str, Any] = {}

    # asset-class tilt (math unchanged; now writes into the shared `overrides`)
    current_mix = _current_target_mix_pct(baseline.response)
    tilt = normalize_tilt(
        current_mix,
        scope_only=action.scope_only_asset_classes,
        tilt_asset_class=action.tilt_asset_class,
        tilt_delta_pp=action.tilt_delta_pp,
        tilt_target_pct=action.tilt_target_pct,
    )
    if tilt.mix_pct is not None:
        overrides["asset_class_tilt"] = tilt.mix_pct
        applied["tilt"] = {"source": "default_step" if tilt.default_step_applied else "customer_number"}
        if tilt.mix_pct.get("equity", 0.0) >= 99.0:
            overrides["pure_equity_only"] = True
            applied["tilt"]["pure_equity"] = True
        # KEEP the existing requested_classes / `absent` shortfall_note block here,
        # writing into this `applied` (chat.py:1010-1018).

    # market-cap tilt
    if action.market_cap:
        mc = normalize_market_cap_tilt(
            _current_market_cap_mix_pct(baseline.response),
            cap=action.market_cap, heavy=bool(action.market_cap_heavy),
        )
        if mc.zero_current:
            text = await format_relay_or_canned(
                ctx=ctx, module_name="rebalancing",
                message=(f"You don't hold any {action.market_cap}-cap funds today, so I can't "
                         f"tilt toward more of them — how much would you like in {action.market_cap} cap?"),
                action_mode="gather",
            )
            return ChatHandlerResult(text=text, snapshot_id=None, rebalancing_recommendation_id=None)
        if mc.mix_pct is not None:
            overrides["market_cap_tilt"] = mc.mix_pct
            applied["market_cap"] = {"source": "default_step" if mc.default_step_applied else "customer_number"}

    # No tilt expressed at all -> plain tax/cash counterfactual (carries the merged overrides).
    if "asset_class_tilt" not in overrides and "market_cap_tilt" not in overrides:
        return await _counterfactual_explore(ctx, overrides)

    requested_run = await compute_rebalancing_result(
        user=ctx.user_ctx, user_question=ctx.user_question, db=ctx.db,
        acting_user_id=ctx.effective_user_id, chat_session_id=ctx.session_id,
        persist=True, origin=ORIGIN_CANDIDATE,
        force_fresh_allocation=("additional_cash_inr" in overrides),
        chat_ctx=with_chat_overrides(ctx, overrides),
    )
    early = await _degraded_or_none(ctx, requested_run)
    if early is not None:
        return early
```

**(2) Compose the subgroup-aware fund count on the requested plan (audit fix — the tilt handler never read `target_fund_count`):**

```python
    requested_response = requested_run.response
    count_c = ConsolidationConstraints(target_fund_count=action.target_fund_count)
    if constraints_active(count_c):
        reshaped, err = reshape_response(requested_response, count_c)
        if err is None:
            requested_response = reshaped
            applied["fund_count"] = action.target_fund_count
            bumped = getattr(getattr(reshaped, "totals", None), "funds_to_buy_count", None)
            if action.target_fund_count and bumped and bumped > action.target_fund_count:
                applied["fund_count_bumped_to"] = bumped   # disclosed via constraint_impact
```

**(3) Switch the remaining downstream reads from `requested_run.response` to `requested_response`** — the requested-mix line (chat.py:1044), the 2nd arg of `_buy_changes_vs_recommended` (:1051), and `response=` in `_format_or_fallback_rebal` (:1072); and replace `requested_run.formatted_text` (:1073) with `build_fallback_rebal_brief(requested_response, used_cached_allocation=False)`. Add `applied["fund_count_bumped_to"]`/`applied["fund_count"]` into the `impact` dict so the reply can disclose the count and any bump.

**(4) The guard at chat.py:775** — change to
`if action.tilt_asset_class or action.scope_only_asset_classes or action.market_cap:`.

- [ ] **Step 6: Extend `_DETECT_REBAL_SYSTEM`**

Add to the `counterfactual_explore` section a market-cap rule + examples (verbatim additions):

```
MARKET-CAP ASKS (large/mid/small cap) are counterfactual_explore too — set
`market_cap` to the sleeve they want MORE of and `market_cap_heavy` for a
"heavy/mostly/lots of" qualifier. These re-run the plan toward that cap.
- "more small cap" / "tilt to small caps"   -> market_cap="small"
- "make it small-cap heavy" / "mostly small"-> market_cap="small", market_cap_heavy=true
- "increase mid caps"                        -> market_cap="mid"
A market-cap ask MAY co-occur with an asset-class ask ("only equity, more small cap")
and with a fund-count ask ("more small cap, max 4 funds") — set ALL the fields; do
not drop any.
For a large/mid/small-cap ask use `market_cap` — do NOT ALSO fill `category_weights`
for large/mid/small cap (the market-cap tilt supersedes that legacy buy-shuffle for
caps). `category_weights` remains only for non-cap category weighting.
```

Then **retire the now-contradictory cap examples** in the existing consolidate section so the prompt doesn't teach two rules for the same phrase (dead-code/contradiction fix):
- `chat.py:319-321` — change the "I want more mid cap" / "more mid cap than large cap" → `category_weights={"mid cap": 0}` example to route to `market_cap="mid"` instead.
- `chat.py:324-325` — change `"only equity, more mid cap, max 4 funds"` from `category_weights={"mid cap":0}, target_fund_count=4 (category+count win here)` to `scope_only_asset_classes=["equity"], market_cap="mid", target_fund_count=4`.
- `chat.py:244` — drop "more mid cap" / "at least 30% in small cap" from the `category_weights` description's cap examples (keep only genuinely non-cap categories).

Leave `category_weights` in the schema and `_consolidate` untouched (still valid for non-cap categories); only the cap-routing examples move.

- [ ] **Step 7: Run tests**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_chat.py -v`
Expected: PASS.

- [ ] **Step 8: Extend + run the detector eval**

Add cases to `AI_Agents/tests/test_rebal_detector_eval.py`: `"more small cap"` → `market_cap="small"`; `"small-cap heavy"` → `market_cap="small", market_cap_heavy=True`; `"only equity, more mid cap, max 4 funds"` → `scope_only_asset_classes=["equity"]`, `market_cap="mid"`, `target_fund_count=4`.
Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_rebal_detector_eval.py -v`

- [ ] **Step 9: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py AI_Agents/tests/test_rebal_detector_eval.py app/domains/rebalancing/services/rebal_engine/tests/test_chat.py
git commit -m "feat(rebal): detect market-cap ask, compose one merged override dict"
```

---

### Task 5: Subgroup-aware fund-count reshape

Make the count/category reshape group by `asset_subgroup` so it preserves each subgroup's buy total (can't undo a market-cap tilt), concentrates within a subgroup, and floors the count at the number of bought subgroups.

**Scope (from audit):** subgroup-awareness applies to the **bare `target_fund_count` path ONLY** (no `allowed_categories`/`excluded_categories`/`category_weight_targets`). The `allowed_categories` path keeps its portfolio-wide "redeploy the whole budget" semantics, and `_reshape_extended` is **left unchanged** — otherwise the total-preservation invariant and 3 existing `allowed_categories` tests break. This scoping is exactly the tilt+count case ("more small cap, max 4 funds").

**Files:**
- Modify: `AI_Agents/src/Rebalancing/consolidation.py` — add `from collections import defaultdict` (line ~14); split `_reshape_legacy` (89-138) into the unchanged allowed-categories branch + a new bare-count subgroup-aware branch. `_reshape_extended` UNCHANGED.
- Test: `AI_Agents/src/Rebalancing/Testing/test_consolidation.py` (extend + update 1 bare-count test); `AI_Agents/src/Rebalancing/Testing/test_consolidation_response.py` (update 2 bare-count tests).

**Interfaces:**
- Consumes: `BuyCandidate.asset_subgroup` (already present, line 49).
- Produces: unchanged public signature (`compute_reshaped_buys` / `reshape_response`). Bare-count behaviour now subgroup-grouped; `allowed_categories`/extended behaviour byte-identical. A floor-driven count bump surfaces via `reshape_response`'s recomputed `totals.funds_to_buy_count` (line 310) → the existing disclosure at `chat.py:1352-1358`.

- [ ] **Step 1: Write the failing test**

```python
# AI_Agents/src/Rebalancing/Testing/test_consolidation.py (new tests)
from decimal import Decimal
from Rebalancing.consolidation import (
    BuyCandidate, ConsolidationConstraints, compute_reshaped_buys,
)


def _c(isin, sub, sg, rank, buy):
    return BuyCandidate(isin=isin, recommended_fund=isin, sub_category=sub,
                        asset_subgroup=sg, rank=rank, buy_inr=Decimal(buy))


def test_count_preserves_each_subgroup_buy_total():
    # 2 small-cap funds (300 total) + 2 large-cap funds (200 total); ask max 2 funds.
    cands = [
        _c("S1", "Small Cap Fund", "high_beta_equities", 1, 200),
        _c("S2", "Small Cap Fund", "high_beta_equities", 2, 100),
        _c("L1", "Large Cap Fund", "low_beta_equities", 1, 150),
        _c("L2", "Large Cap Fund", "low_beta_equities", 2, 50),
    ]
    out = compute_reshaped_buys(cands, ConsolidationConstraints(target_fund_count=2))
    small = out["S1"] + out["S2"]
    large = out["L1"] + out["L2"]
    assert small == Decimal(300)   # small-cap total preserved
    assert large == Decimal(200)   # large-cap total preserved
    # concentrated: one fund per subgroup keeps its subgroup's whole total
    assert out["S1"] == Decimal(300) and out["S2"] == Decimal(0)
    assert out["L1"] == Decimal(200) and out["L2"] == Decimal(0)


def test_count_floor_is_number_of_bought_subgroups():
    # 3 subgroups with buys; ask max 2 -> cannot go below 3 without moving across
    # subgroups, so at least one fund per subgroup survives (3 funds kept).
    cands = [
        _c("S1", "Small Cap Fund", "high_beta_equities", 1, 100),
        _c("M1", "Mid Cap Fund", "medium_beta_equities", 1, 100),
        _c("L1", "Large Cap Fund", "low_beta_equities", 1, 100),
    ]
    out = compute_reshaped_buys(cands, ConsolidationConstraints(target_fund_count=2))
    assert sum(1 for v in out.values() if v > 0) == 3
    assert out["S1"] == Decimal(100) and out["M1"] == Decimal(100) and out["L1"] == Decimal(100)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py -k subgroup -v`
Expected: FAIL — today's flat reshape moves budget across subgroups.

- [ ] **Step 3: Add the `defaultdict` import**

At the top of `consolidation.py` (with the other stdlib imports, ~line 14):

```python
from collections import defaultdict
```

- [ ] **Step 4: Split `_reshape_legacy` — keep the allowed-categories branch, add a subgroup-aware bare-count branch**

Replace `_reshape_legacy` with:

```python
def _reshape_legacy(cands, constraints, total, *, rounding_multiple):
    # allowed_categories keeps its portfolio-wide "redeploy the whole budget into
    # these categories" semantics — UNCHANGED (cross-subgroup movement is the point).
    if constraints.allowed_categories:
        allowed = set(constraints.allowed_categories)
        eligible = [c for c in cands if c.sub_category in allowed]
        if not eligible:
            return {c.isin: Decimal(0) for c in cands}
        ordered = sorted(eligible, key=lambda c: (c.rank, -c.buy_inr))
        keep = (ordered[: max(1, constraints.target_fund_count)]
                if constraints.target_fund_count is not None else ordered)
        if len(keep) == len(cands):
            return {c.isin: c.buy_inr for c in cands}
        kept_total = sum((c.buy_inr for c in keep), Decimal(0))
        displaced = total - kept_total
        out = {c.isin: Decimal(0) for c in cands}
        for c in keep:
            share = (displaced * c.buy_inr / kept_total
                     if kept_total > 0 else displaced / Decimal(len(keep)))
            out[c.isin] = c.buy_inr + _round_to_multiple(share, rounding_multiple)
        placed = sum(out.values(), Decimal(0))
        residual = total - placed
        if residual != 0:
            biggest = max(keep, key=lambda c: out[c.isin])
            out[biggest.isin] += residual
        return out

    # Bare target_fund_count: SUBGROUP-AWARE. Preserve each subgroup's buy total so a
    # count trim never pulls money out of a market-cap tilt. Redistribute WITHIN a
    # subgroup only; count floor = number of subgroups with buys.
    out: dict[str, Decimal] = {c.isin: Decimal(0) for c in cands}
    by_sg: dict[str, list[BuyCandidate]] = defaultdict(list)
    for c in cands:
        by_sg[c.asset_subgroup].append(c)
    keep_per_sg: dict[str, int] = {sg: 1 for sg in by_sg}
    if constraints.target_fund_count is not None:
        extra = max(0, constraints.target_fund_count - len(by_sg))
        sgs = list(by_sg)  # plain round-robin; spec fixes no extra-slot ordering
        i = 0
        while extra > 0 and any(keep_per_sg[s] < len(by_sg[s]) for s in sgs):
            sg = sgs[i % len(sgs)]
            if keep_per_sg[sg] < len(by_sg[sg]):
                keep_per_sg[sg] += 1
                extra -= 1
            i += 1
    else:
        keep_per_sg = {sg: len(by_sg[sg]) for sg in by_sg}
    for sg, group in by_sg.items():
        sg_total = sum((c.buy_inr for c in group), Decimal(0))
        ordered = sorted(group, key=lambda c: (c.rank, -c.buy_inr))
        keep = ordered[: max(1, keep_per_sg[sg])]
        kept_total = sum((c.buy_inr for c in keep), Decimal(0))
        displaced = sg_total - kept_total
        for c in keep:
            share = (displaced * c.buy_inr / kept_total
                     if kept_total > 0 else displaced / Decimal(len(keep)))
            out[c.isin] = c.buy_inr + _round_to_multiple(share, rounding_multiple)
        placed = sum(out[c.isin] for c in keep)
        residual = sg_total - placed
        if residual != 0 and keep:
            biggest = max(keep, key=lambda c: out[c.isin])
            out[biggest.isin] += residual
    return out
```

`_reshape_extended` (excluded/weight-target path) is **left unchanged** this phase — a market-cap tilt composes with a bare count, not with category exclusion/weighting. Add a one-line comment at the top of `_reshape_extended` noting it is **intentionally tilt-unaware** (a future "more small cap, exclude sectoral" would route here and not preserve the tilt) so the asymmetry with the bare-count path doesn't read as a latent bug.

- [ ] **Step 5: Update the 3 bare-count tests that the new behaviour deliberately changes**

The new bare-count rule is an intended behaviour change, so these existing tests (which assert the OLD portfolio-wide count) must be updated to the new invariant — **each subgroup's buy total is preserved, and #funds kept = `max(target, #subgroups-with-buys)`**. Read each test's candidate inputs and recompute the expected buys under that rule:
- `test_consolidation.py::test_displaced_budget_spreads_pro_rata` (3 subgroups, `target_fund_count=2` → floor 3, all three kept, each subgroup's total unchanged).
- `test_consolidation_response.py::test_reshape_response_collapses_buys_keeps_sells` (count case → now keeps one fund per bought subgroup).
- `test_consolidation_response.py::test_reshape_keeps_all_buy_representations_in_agreement` (`funds_to_buy_count` now = #bought subgroups).

Leave every `allowed_categories` / `excluded_categories` / weight-target test UNCHANGED — they must still pass (proof the split preserved their semantics).

- [ ] **Step 6: Run tests**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing/test_consolidation.py AI_Agents/src/Rebalancing/Testing/test_consolidation_response.py -v`
Expected: PASS — new subgroup tests + the 3 updated bare-count tests + all unchanged allowed-categories/weight tests still green.

- [ ] **Step 7: Commit**

```bash
git add AI_Agents/src/Rebalancing/consolidation.py AI_Agents/src/Rebalancing/Testing/test_consolidation.py AI_Agents/src/Rebalancing/Testing/test_consolidation_response.py
git commit -m "feat(rebal): subgroup-aware bare-count reshape (preserves per-subgroup totals)"
```

---

### Task 6: SEBI-category table in the reply

Formatter prompt change only — the data (`sub_category`) is already in the facts pack.

**Files:**
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py` (`_REBAL_FORMATTER_BODY`, the compute/counterfactual/consolidate mode blocks ~487-553)
- Test: `app/domains/rebalancing/services/rebal_engine/tests/test_chat.py` (prompt-content assertion)

- [ ] **Step 1: Write the failing test**

```python
def test_formatter_body_requires_sebi_category_table():
    from app.domains.rebalancing.services.rebal_engine.chat import _REBAL_FORMATTER_BODY
    body = _REBAL_FORMATTER_BODY.lower()
    assert "sebi" in body and "table" in body
    assert "one row per sub_category" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_chat.py -k sebi_category_table -v`
Expected: FAIL.

- [ ] **Step 3: Add the instruction**

In `_REBAL_FORMATTER_BODY`, in the shared plan-presentation guidance (near the `fund_actions` "always include a short fund-level trade list" note), add:

```
On any turn that PRESENTS A PLAN (compute, counterfactual_explore, consolidate),
render a SEBI-category table: one row per sub_category from `buckets`, columns
Current → Buy → Sell → Planned (copy the `_indian` amounts verbatim), bold the
header, right-align the numbers, and a bold totals row. Then the short fund-level
trade list. NEVER surface asset_subgroup; the customer-facing label is the SEBI
sub_category. When a market-cap tilt moved a shared subgroup, add ONE light line
(e.g. "this also nudges your flexi/multi-cap funds in the same bucket") — do not
imply pin-point precision.
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/rebal_engine/tests/test_chat.py -k sebi_category_table -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/domains/rebalancing/services/rebal_engine/chat.py
git commit -m "feat(rebal): reply renders a SEBI-category table on plan turns"
```

---

### Task 7: Anti-stall synchronous-execution guardrail

**Files:**
- Modify: `AI_Agents/src/persona.py` (`SHARED_MECHANICS`)
- Test: `app/domains/ai_engine/tests/test_persona_synchronous_rule.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# app/domains/ai_engine/tests/test_persona_synchronous_rule.py
def test_persona_forbids_deferred_work_promises():
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4] / "AI_Agents" / "src"))
    from persona import SHARED_MECHANICS, build_system_prompt
    text = build_system_prompt("").lower()  # already includes SHARED_MECHANICS (persona.py:142)
    assert "one turn" in text  # from the new rule specifically, not an incidental match
    for banned in ("come back", "working on it", "give me a moment", "hang tight"):
        assert banned in text  # the rule names them verbatim as forbidden phrasings
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_persona_synchronous_rule.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the rule**

Append to `SHARED_MECHANICS` (before the closing of the string):

```python
    "- Synchronous reality: you answer in ONE turn. Everything you output IS the "
    "complete reply — there is no background work and nothing runs after you stop. "
    "NEVER say you are 'working on it', that you will 'come back with' the answer, "
    "or use a stall like 'give me a moment', 'a few seconds', or 'hang tight'. If "
    "you don't have a result in hand, say so plainly and offer a next step — never "
    "promise future or deferred delivery."
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv-mac/bin/python -m pytest app/domains/ai_engine/tests/test_persona_synchronous_rule.py -v`
Expected: PASS.

- [ ] **Step 5: Full regression**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/Rebalancing/Testing app/domains/rebalancing app/domains/mutual_funds/tests app/domains/ai_engine/tests -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/persona.py app/domains/ai_engine/tests/test_persona_synchronous_rule.py
git commit -m "feat(persona): synchronous-execution rule, no deferred-work promises"
```

---

## Self-review notes (author)

- **Spec coverage:** §5.1 market-cap tilt → Tasks 1-2; §4 compose/merge overrides → Task 4; §5.2 subgroup-aware count → Task 5; §6.2 default steps → Task 3; §7 SEBI tables → Task 6; §8 guardrail → Task 7; §6.5 ENGINE_VERSION → Task 2. Deferred (`do_not_buy`) and dropped (`do_not_sell`) have no tasks — intended.
- **Two open confirmations flagged inline (not placeholders):** (a) the exact `SubgroupSummary` amount field for `_current_market_cap_mix_pct` (mirror `_planned_mix_pct`); (b) the existing minimal-request fixture in `Rebalancing/Testing`. Both are "read this specific existing symbol and copy it," not invented behaviour.
- **Type consistency:** `market_cap_tilt` dict keys are `large/mid/small` everywhere (request, `_apply_market_cap_tilt`, `normalize_market_cap_tilt`, detector). `_SUBGROUP_MARKET_CAP` in pipeline and the equivalent `sub_of_cap` map in `_current_market_cap_mix_pct` use identical subgroup names.
