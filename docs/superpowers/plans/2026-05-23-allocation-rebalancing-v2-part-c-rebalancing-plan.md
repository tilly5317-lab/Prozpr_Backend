# Rebalancing v2 Part C — `Rebalancing/` Thin Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the `Rebalancing` engine to consume the (Part B) `practical_asset_allocation` module: nest its input on the request, run it first inside `pipeline.py`, lift per-subgroup targets onto rank-1 rows, drop the ELSS row special-case in `step2`, add the `short_debt = 30%` per-fund cap tier, and surface `SELL_DIRECT_STOCKS` + two frozen subgroup summaries from `step6`. Plumb the four corpus scalars (`mf_corpus_inr`, `non_mf_equity_corpus_inr`, `elss_corpus_inr`, plus the implicit `total_corpus_inr`) from the bridge input builder by reading `StockTransaction` and filtering out ELSS rows.

**Architecture:** Existing six-step engine stays. `pipeline.py` gains one pre-step call to `run_practical_allocation` and a small `_assign_targets_to_rank1` helper. `step1`/`step2`/`step6` and `models.py`/`config.py`/`tables.py`/`rationales.py` get surgical edits. `app/services/ai_bridge/rebalancing/input_builder.py` is extended to compute the four corpus scalars and filter ELSS rows out of the engine input.

**Tech Stack:** Python 3.11+, pydantic v2, pytest, SQLAlchemy 2.x async.

**Spec reference:** `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md` Part C (sections C.1–C.12).

**Assumes:** Part B has merged. `practical_asset_allocation` is importable as `from practical_asset_allocation import run_practical_allocation` (or `from practical_asset_allocation.pipeline import run_practical_allocation`) and exposes the pydantic types `PracticalAllocationInput`, `PracticalAllocationOutput`, `CorpusBreakdown`. If executing serially, finish Part B first.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `AI_Agents/src/Rebalancing/CLAUDE.md` | Modify | Document the new upstream `practical_asset_allocation` dependency edge (spec exception to peer-isolation). Remove the ELSS "special case" bullet under Data contract. |
| `AI_Agents/src/Rebalancing/config.py` | Modify | Add `SHORT_DEBT_FUND_CAP_PCT = 30.0` knob with env override `REBAL_SHORT_DEBT_FUND_CAP_PCT`. |
| `AI_Agents/src/Rebalancing/tables.py` | Modify | Replace `MULTI_FUND_CAP_SUBGROUPS` frozenset with `SUBGROUP_FUND_CAP_PCT: dict[str, float]` (multi_asset=20, short_debt=30; default OTHERS_FUND_CAP_PCT). |
| `AI_Agents/src/Rebalancing/steps/step1_cap_and_spill.py` | Modify | Replace the `_max_pct_for` helper with a lookup against the new dict. |
| `AI_Agents/src/Rebalancing/models.py` | Modify | (a) Add `practical_allocation_input: PracticalAllocationInput` to `RebalancingComputeRequest` and drop the now-redundant `total_corpus` field. (b) Add `practical_allocation: PracticalAllocationOutput` to `RebalancingComputeResponse`. (c) Widen `TradeAction.action` literal to `BUY | SELL | EXIT | SELL_DIRECT_STOCKS` and make `isin`, `sub_category`, `recommended_fund` Optional. (d) Document `SubgroupSummary.actions: list[FundRowAfterStep5] = []` for frozen entries (no schema change). |
| `AI_Agents/src/Rebalancing/rationales.py` | Modify | Add `sell_excess_direct_stocks` entry (title + `{amount}` body). |
| `AI_Agents/src/Rebalancing/steps/step2_compare_and_decide.py` | Modify | Drop the `asset_subgroup == "tax_efficient_equities"` row-special-case (current lines ~37–45) and the BAD_FUND_DETECTED ELSS-exclusion clause (current lines ~63–77). |
| `AI_Agents/src/Rebalancing/steps/step6_presentation.py` | Modify | Accept new `practical: PracticalAllocationOutput` parameter; append two frozen `SubgroupSummary` entries (`tax_efficient_equities`, `non_mf_equities`); emit `SELL_DIRECT_STOCKS` `TradeAction` when `practical.corpus_breakdown.excess_direct_stocks_inr > 0`; set `practical_allocation` on the response; switch `total_corpus` references to `request.practical_allocation_input.total_corpus`. |
| `AI_Agents/src/Rebalancing/pipeline.py` | Modify | Import `practical_asset_allocation`; call it first; inline `_assign_targets_to_rank1` helper (~10 lines); thread `practical` through to `step6.apply`. |
| `AI_Agents/src/Rebalancing/Testing/test_part_c.py` | Create (LOCAL — gitignored) | New TDD tests covering each engine-side change (cap dict, ELSS removal, SELL_DIRECT_STOCKS, frozen subgroups, practical passthrough). |
| `app/services/ai_bridge/rebalancing/input_builder.py` | Modify | Read `StockTransaction` for non-MF equity sum; build `PracticalAllocationInput` (total_corpus = MF + non-MF + cash; mf_corpus = MF; elss_corpus = sum of `asset_subgroup="tax_efficient_equities"` rows; non_mf_equity_corpus from stocks); filter out ELSS rows; set `target_amount_pre_cap = 0` on remaining rows. |
| `app/services/ai_bridge/rebalancing/tests/test_input_builder.py` | Modify (COMMITTED) | New fixtures + cases for ELSS-filter, non-MF equity sum, practical_allocation_input shape. |
| `app/services/ai_bridge/rebalancing/tests/test_service.py` | Modify (COMMITTED) | Update `_build_min_response` / `_build_response_with_subgroup` / `_build_response_with_funds` to include the new `practical_allocation` field. |
| `app/services/ai_bridge/rebalancing/tests/conftest.py` | Modify (COMMITTED) | Add `fixture_user_with_stock_holding` and `fixture_user_with_elss_holding` matching existing patterns. |

---

## Conventions

- **Engine-internal tests** live in `AI_Agents/src/Rebalancing/Testing/` — this folder is **gitignored** per repo `.gitignore` (`/AI_Agents/src/*/Testing/`). Files there are local-only TDD scaffolding; **never `git add`** anything in that folder. Engine code changes get committed; their tests do not.
- **Integration tests** live in `app/services/ai_bridge/rebalancing/tests/` — these **are** committed and ARE the customer-facing test suite that ships.
- **Cross-agent import exception.** Per spec §B.1 / §C.3, `Rebalancing` importing `practical_asset_allocation` is a documented exception to the peer-isolation rule in `AI_Agents/src/CLAUDE.md`. We update `Rebalancing/CLAUDE.md` to call this out explicitly in Task 1; the upstream `practical_asset_allocation/CLAUDE.md` already calls out its own exception per Part B.
- **Commit cadence.** Engine and bridge code commits happen at the **end** of each task (after tests pass). One commit per task unless a sub-step naturally splits (e.g., Task 12 verification is read-only and may produce zero commits or a single fixture-fix commit).
- **Memory rule.** Per project memory, superpowers artifacts stay local — do **not** `git add` this plan file or anything under `docs/superpowers/`.
- **Project name.** "Prozpr" (never "Prozper"; autocorrect trap).
- **Memory note flagged.** A project memory entry warns about "rebalancing test damage — tests reference `RebalancingRecommendation`/`RecommendationType` that don't exist". Search of the current tree finds those names only in `app.models.rebalancing` (which **does** exist) and in `Reference_docs/*.md` (planning docs). The `Rebalancing/Testing/` per-step files reference real engine classes only. Treat the memory note as stale; do **not** spend cycles "fixing" the engine-internal tests beyond Task 12's local run.

---

### Task 1: Update `Rebalancing/CLAUDE.md` to declare the upstream dependency

**Files:**
- Modify: `AI_Agents/src/Rebalancing/CLAUDE.md`

**Why:** Per `AI_Agents/src/CLAUDE.md`, agents under `src/` are peers and do not import each other unless documented. Part C introduces the second documented exception: `Rebalancing → practical_asset_allocation`. The leaf CLAUDE.md must call this out so future readers don't "fix" the import.

- [ ] **Step 1: Edit `Rebalancing/CLAUDE.md`**

Replace the **Depends on** block (currently a single line "pydantic only. No `src/` peer is imported.") and the **Data contract** ELSS bullet to reflect the new world:

```diff
 ## Depends on

-- `pydantic` only. No `src/` peer is imported.
+- `pydantic`.
+- `practical_asset_allocation` (documented peer-isolation exception per
+  `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md`
+  §B.1 / §C.3): `pipeline.run_rebalancing` calls
+  `practical_asset_allocation.run_practical_allocation` first, then lifts
+  per-subgroup targets from its `aggregated_subgroups` onto rank-1 fund rows.
+  The practical output is also surfaced verbatim on `RebalancingComputeResponse.practical_allocation`.

 ## Data contract

 - Input: `RebalancingComputeRequest` — corpus, tax state, and a single homogeneous list of `FundRowInput` rows. Recommended funds carry `rank ≥ 1` (rank-1 holding the goal-allocation amount, ranks 2+ starting at 0). Held-but-not-recommended ("BAD") funds carry `rank = 0`, `is_recommended = False`, `target_amount_pre_cap = 0`. The input builder (upstream, in `app/services/`) is responsible for materialising both kinds.
 - Output: `RebalancingComputeResponse` — rows after step 5, totals, trade list, warnings, metadata.
-- **ELSS special case**: rows with `asset_subgroup == "tax_efficient_equities"` are treated as pure buy-demand (fresh FY purchase headroom), not portfolio-share targets. Existing ELSS holdings are locked under the 3-year SEBI lock-in — step 2 never trims or exits ELSS positions, even off-list (BAD) ones. See `steps/step2_compare_and_decide.py`.
+- **ELSS is scalar, not row.** ELSS exposure is surfaced via `RebalancingComputeRequest.practical_allocation_input.elss_corpus` (and echoed on `practical_allocation.corpus_breakdown.elss_corpus_inr`). ELSS rows are filtered out of `rows` by the upstream input builder. `step6` emits a frozen `SubgroupSummary` for `tax_efficient_equities` so the customer view still shows the ELSS allocation, but no `BUY`/`SELL`/`EXIT` trade is ever generated for it (SEBI 3-year lock-in).
+- **Non-MF equity is scalar, not row.** Direct-stock / PMS holdings live on `practical_allocation_input.non_mf_equity_corpus`. When the practical engine's NFA-banded cap forces a trim, `step6` emits a single `SELL_DIRECT_STOCKS` `TradeAction` for `excess_direct_stocks_inr`. No per-stock trades.
```

Also add the new env knob row to the Env-knobs table:

```diff
 | `REBAL_MULTI_FUND_CAP_PCT` | `20.0` | Per-fund cap for multi-cap sub-categories |
 | `REBAL_OTHERS_FUND_CAP_PCT` | `10.0` | Per-fund cap otherwise |
+| `REBAL_SHORT_DEBT_FUND_CAP_PCT` | `30.0` | Per-fund cap for short_debt subgroup (Excel R247) |
 | `REBAL_MIN_CHANGE_PCT` | `0.10` | `worth_to_change` threshold |
```

- [ ] **Step 2: Commit (documentation only)**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
git add AI_Agents/src/Rebalancing/CLAUDE.md
git commit -m "docs(rebalancing): document practical_asset_allocation upstream dependency (C.3)

Declares the new src/-internal cross-agent import edge from Rebalancing to
practical_asset_allocation as a documented exception to the peer-isolation
rule in AI_Agents/src/CLAUDE.md. Replaces the old ELSS row 'special case'
data-contract bullet with the new scalar model (ELSS + non-MF equity flow
in via practical_allocation_input; step6 emits a frozen subgroup and a
SELL_DIRECT_STOCKS trade when relevant). Adds REBAL_SHORT_DEBT_FUND_CAP_PCT
to the env-knob table for the upcoming Task 2 change.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.3"
```

---

### Task 2: Add `SHORT_DEBT_FUND_CAP_PCT` knob in `config.py`

**Files:**
- Modify: `AI_Agents/src/Rebalancing/config.py:16-17`

- [ ] **Step 1: Add the constant**

```diff
 # ── Bucket A — caps & thresholds ─────────────────────────────────────────────

 MULTI_FUND_CAP_PCT: float = float(os.getenv("REBAL_MULTI_FUND_CAP_PCT", "20.0"))
 OTHERS_FUND_CAP_PCT: float = float(os.getenv("REBAL_OTHERS_FUND_CAP_PCT", "10.0"))
+# Per Excel R247: `short_debt` carries a higher per-fund cap than the
+# generic 10% because the universe of high-quality short-duration debt
+# funds is small and concentration risk is correspondingly lower.
+SHORT_DEBT_FUND_CAP_PCT: float = float(os.getenv("REBAL_SHORT_DEBT_FUND_CAP_PCT", "30.0"))
 REBALANCE_MIN_CHANGE_PCT: float = float(os.getenv("REBAL_MIN_CHANGE_PCT", "0.10"))
```

- [ ] **Step 2: Commit**

```bash
git add AI_Agents/src/Rebalancing/config.py
git commit -m "feat(rebalancing): add SHORT_DEBT_FUND_CAP_PCT knob (C.5)

New env-overrideable knob REBAL_SHORT_DEBT_FUND_CAP_PCT (default 30.0)
that step1 cap-and-spill will use when the asset_subgroup is short_debt.
Consumed by Task 3 (tables.py dict) and Task 4 (step1 lookup). Excel R247.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.5"
```

---

### Task 3: Replace frozenset with `SUBGROUP_FUND_CAP_PCT` dict in `tables.py`

**Files:**
- Modify: `AI_Agents/src/Rebalancing/tables.py` (full rewrite — file is 12 lines)

- [ ] **Step 1: Write the local failing test (engine-internal, gitignored)**

Create `AI_Agents/src/Rebalancing/Testing/test_part_c.py`:

```python
"""Local TDD tests for Part C. Gitignored — never `git add`."""
from __future__ import annotations


def test_subgroup_fund_cap_pct_short_debt_is_30():
    from Rebalancing.tables import SUBGROUP_FUND_CAP_PCT
    assert SUBGROUP_FUND_CAP_PCT["short_debt"] == 30.0


def test_subgroup_fund_cap_pct_multi_asset_is_20():
    from Rebalancing.tables import SUBGROUP_FUND_CAP_PCT
    assert SUBGROUP_FUND_CAP_PCT["multi_asset"] == 20.0


def test_subgroup_fund_cap_pct_unknown_subgroup_absent():
    """Unknown keys are absent; the dict is for lookup with a default fallback."""
    from Rebalancing.tables import SUBGROUP_FUND_CAP_PCT
    assert "low_beta_equities" not in SUBGROUP_FUND_CAP_PCT
```

- [ ] **Step 2: Run — expect ImportError / KeyError**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py::test_subgroup_fund_cap_pct_short_debt_is_30 -v
```

Expected: FAIL — `SUBGROUP_FUND_CAP_PCT` does not exist.

- [ ] **Step 3: Rewrite `tables.py`**

Replace the entire file contents:

```python
"""In-memory lookup tables used by the rebalancing engine."""

from __future__ import annotations

from .config import (
    MULTI_FUND_CAP_PCT,
    OTHERS_FUND_CAP_PCT,
    SHORT_DEBT_FUND_CAP_PCT,
)


# Per-fund concentration cap (% of corpus) keyed by `asset_subgroup`.
# Missing keys fall back to `OTHERS_FUND_CAP_PCT` via `cap_pct_for(...)`.
# Sources:
#   - `multi_asset` 20%: multi-asset funds are internally diversified across
#     asset classes; per-fund concentration risk is lower than single-class.
#   - `short_debt` 30%: Excel R247 — short-duration debt fund universe is
#     small and high-quality; concentration risk is correspondingly lower.
SUBGROUP_FUND_CAP_PCT: dict[str, float] = {
    "multi_asset": MULTI_FUND_CAP_PCT,
    "short_debt": SHORT_DEBT_FUND_CAP_PCT,
}


def cap_pct_for(asset_subgroup: str) -> float:
    """Per-fund cap (% of corpus) for `asset_subgroup`, with default fallback."""
    return SUBGROUP_FUND_CAP_PCT.get(asset_subgroup, OTHERS_FUND_CAP_PCT)
```

- [ ] **Step 4: Run the three Task-3 tests — should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "subgroup_fund_cap_pct" -v
```

Expected: 3 passed.

- [ ] **Step 5: Step1 will break in the next task — note any test failures here are expected.**

`step1_cap_and_spill.py` and `step6_presentation.py` still import `MULTI_FUND_CAP_SUBGROUPS`. Don't run the full engine suite yet; the next task fixes those call-sites.

- [ ] **Step 6: Commit (engine code only; gitignored tests stay local)**

```bash
git add AI_Agents/src/Rebalancing/tables.py
git commit -m "feat(rebalancing): replace MULTI_FUND_CAP_SUBGROUPS frozenset with SUBGROUP_FUND_CAP_PCT dict (C.5)

Introduces a 3-tier per-fund cap lookup:
  - multi_asset: 20%
  - short_debt:  30% (NEW; Excel R247)
  - default:     OTHERS_FUND_CAP_PCT (10%)

Exposes a `cap_pct_for(asset_subgroup) -> float` helper for step1 and step6
to use instead of branching on a frozenset. Step1, step6 call-site updates
land in Task 4 and Task 9 respectively.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.5"
```

---

### Task 4: Switch `step1_cap_and_spill.py` to the new dict lookup

**Files:**
- Modify: `AI_Agents/src/Rebalancing/steps/step1_cap_and_spill.py:18` (import), `:26` (import), `:30-31` (helper).

- [ ] **Step 1: Write the failing test (engine-internal)**

Append to `AI_Agents/src/Rebalancing/Testing/test_part_c.py`:

```python
def test_step1_short_debt_uses_30pct_cap():
    """Per C.5: a short_debt subgroup row carries max_pct = 30.0."""
    from decimal import Decimal

    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    from Rebalancing.steps import step1_cap_and_spill

    # A 50L corpus, single rank-1 short_debt fund with target 40L.
    # With the new 30% cap, max = 15L; overflow 25L emits an unrebalanced warning
    # since there is no rank-2 to spill to.
    rows = [
        FundRowInput(
            asset_subgroup="short_debt",
            sub_category="Short Duration Fund",
            recommended_fund="ICICI Short Term",
            isin="INF109K01Z80",
            rank=1,
            target_amount_pre_cap=Decimal("4000000"),
        ),
    ]
    # NOTE: This test predates Task 5's request-shape change — it uses the
    # current scalar `total_corpus` field. Re-run after Task 5 wires the
    # nested practical_allocation_input; until then this is the canonical
    # smoke test for the cap-lookup change.
    req = RebalancingComputeRequest(
        total_corpus=Decimal("5000000"),
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=rows,
    )
    out_rows, _warnings, _ = step1_cap_and_spill.apply(rows, req)
    assert out_rows[0].max_pct == 30.0
    assert out_rows[0].final_target_amount == Decimal("1500000")


def test_step1_unknown_subgroup_falls_back_to_others_cap():
    from decimal import Decimal

    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    from Rebalancing.steps import step1_cap_and_spill

    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="HDFC Top 100",
            isin="INF179K01YV8",
            rank=1,
            target_amount_pre_cap=Decimal("1000000"),
        ),
    ]
    req = RebalancingComputeRequest(
        total_corpus=Decimal("5000000"),
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=rows,
    )
    out_rows, _, _ = step1_cap_and_spill.apply(rows, req)
    assert out_rows[0].max_pct == 10.0  # OTHERS_FUND_CAP_PCT default
```

- [ ] **Step 2: Run — expect ImportError**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "step1_" -v
```

Expected: ImportError on `from ..tables import MULTI_FUND_CAP_SUBGROUPS` (removed in Task 3).

- [ ] **Step 3: Update `step1_cap_and_spill.py`**

```diff
-from ..config import MULTI_FUND_CAP_PCT, OTHERS_FUND_CAP_PCT
+# Per-fund cap lookup moved to tables.cap_pct_for; config constants are still
+# read inside that helper.
 from ..models import (
     FundRowAfterStep1,
     FundRowInput,
     RebalancingComputeRequest,
     RebalancingWarning,
     WarningCode,
 )
-from ..tables import MULTI_FUND_CAP_SUBGROUPS
+from ..tables import cap_pct_for
 from ..utils import round_to_step
-
-
-def _max_pct_for(asset_subgroup: str) -> float:
-    return MULTI_FUND_CAP_PCT if asset_subgroup in MULTI_FUND_CAP_SUBGROUPS else OTHERS_FUND_CAP_PCT
```

Then replace the two `_max_pct_for(...)` call-sites in `apply(...)` (currently lines 61 and 104):

```diff
-            max_pct = _max_pct_for(r.asset_subgroup)
+            max_pct = cap_pct_for(r.asset_subgroup)
```

```diff
-                    max_pct=_max_pct_for(r.asset_subgroup),
+                    max_pct=cap_pct_for(r.asset_subgroup),
```

- [ ] **Step 4: Run the Task-4 tests — should pass; existing step1 tests should also pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_step1_caps.py AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "step1" -v
```

Expected: all green. The pre-existing `test_step1_caps.py` exercises multi_asset (20%) and large-cap (10% default) — both behaviours preserved.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/steps/step1_cap_and_spill.py
git commit -m "feat(rebalancing): step1 reads per-fund cap from SUBGROUP_FUND_CAP_PCT dict (C.5)

Drops the local _max_pct_for branch on a frozenset in favour of
tables.cap_pct_for(asset_subgroup), which returns 20% for multi_asset,
30% for short_debt (NEW per Excel R247), and the OTHERS_FUND_CAP_PCT
default (10%) for everything else. step1's two call-sites now route
through the helper; existing multi-asset and equity behaviour unchanged.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.5"
```

---

### Task 5: Extend `models.py` — nested practical input/output, widened TradeAction, frozen-friendly SubgroupSummary

**Files:**
- Modify: `AI_Agents/src/Rebalancing/models.py:115-130` (RebalancingComputeRequest), `:184-198` (TradeAction), `:201-222` (SubgroupSummary), `:225-231` (RebalancingComputeResponse).

- [ ] **Step 1: Write the failing test (engine-internal)**

Append to `AI_Agents/src/Rebalancing/Testing/test_part_c.py`:

```python
def test_request_carries_practical_allocation_input():
    """C.1: RebalancingComputeRequest must accept practical_allocation_input."""
    from practical_asset_allocation.pipeline import PracticalAllocationInput  # type: ignore[import-not-found]
    from Rebalancing.models import RebalancingComputeRequest

    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000,
        osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=10_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
        mf_corpus=8_000_000, non_mf_equity_corpus=1_000_000,
        elss_corpus=500_000,
    )
    req = RebalancingComputeRequest(
        practical_allocation_input=inp,
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=[],
    )
    assert req.practical_allocation_input.elss_corpus == 500_000


def test_trade_action_accepts_sell_direct_stocks_with_no_isin():
    """C.6: SELL_DIRECT_STOCKS allowed with isin=None, fund_name=None."""
    from decimal import Decimal

    from Rebalancing.models import TradeAction

    ta = TradeAction(
        isin=None,
        asset_subgroup="non_mf_equities",
        sub_category=None,
        recommended_fund=None,
        action="SELL_DIRECT_STOCKS",
        amount_inr=Decimal("250000"),
        reason_code="sell_excess_direct_stocks",
        reason_title="Trim direct-stock holdings",
        reason_text="...",
    )
    assert ta.action == "SELL_DIRECT_STOCKS"
    assert ta.isin is None


def test_subgroup_summary_actions_defaults_to_empty():
    """C.6: SubgroupSummary supports frozen entries with no fund rows."""
    from decimal import Decimal

    from Rebalancing.models import SubgroupSummary

    sg = SubgroupSummary(
        asset_subgroup="tax_efficient_equities",
        goal_target_inr=Decimal("500000"),
        current_holding_inr=Decimal("500000"),
        suggested_final_holding_inr=Decimal("500000"),
        rebalance_inr=Decimal(0),
        total_buy_inr=Decimal(0),
        total_sell_inr=Decimal(0),
        ranks_total=0,
        ranks_with_holding=0,
        ranks_with_action=0,
    )
    assert sg.actions == []
```

- [ ] **Step 2: Run — expect at least one validation error or ImportError**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "request_carries_practical or trade_action_accepts or subgroup_summary_actions" -v
```

Expected: FAIL — `RebalancingComputeRequest` has no `practical_allocation_input` field; `TradeAction.isin` is non-Optional and `action` literal does not include `SELL_DIRECT_STOCKS`. (The `SubgroupSummary` test already passes because `actions` has `default_factory=list`; we keep it as a regression guard.)

- [ ] **Step 3: Edit `models.py`**

Add the import for the practical types near the top (after the existing pydantic import, line 17):

```diff
 from pydantic import BaseModel, Field
+
+# Cross-agent import: documented exception to peer-isolation per
+# `docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md` §B.1.
+from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
+    PracticalAllocationInput,
+    PracticalAllocationOutput,
+)
```

Rewrite `RebalancingComputeRequest` (current lines 115–130) to drop `total_corpus` and add the nested practical input:

```diff
 class RebalancingComputeRequest(BaseModel):
-    total_corpus: Decimal = Field(ge=0)
+    # The four corpus scalars (total / mf / non-MF equity / ELSS) and all
+    # profile/goal/market-view fields ride on this nested input. The previous
+    # top-level `total_corpus` is now `practical_allocation_input.total_corpus`.
+    practical_allocation_input: PracticalAllocationInput
     tax_regime: Literal["old", "new"]
     effective_tax_rate_pct: float = Field(ge=0.0, le=100.0)
     rounding_step: int = Field(default=100, ge=1)

     # Per-request capital-gains state (bucket D)
     stcg_offset_budget_inr: Optional[Decimal] = None
     carryforward_st_loss_inr: Decimal = Field(default=Decimal(0), ge=0)
     carryforward_lt_loss_inr: Decimal = Field(default=Decimal(0), ge=0)

-    # All rows: recommended (rank≥1) and BAD (rank=0)
+    # All MF rows: recommended (rank≥1) and BAD (rank=0). ELSS rows are
+    # filtered out by the input builder — ELSS exposure surfaces via
+    # `practical_allocation_input.elss_corpus` and as a frozen subgroup row
+    # in step6's response.
     rows: list[FundRowInput]

     # Tracing
     request_id: UUID = Field(default_factory=uuid4)
+
+    @property
+    def total_corpus(self) -> Decimal:
+        """Backwards-compatible accessor; consumers should prefer
+        `practical_allocation_input.total_corpus` directly."""
+        return Decimal(str(self.practical_allocation_input.total_corpus))
```

(The `total_corpus` property keeps step6's `request.total_corpus` working until Task 9 explicitly migrates that call-site.)

Update `TradeAction` (current lines 184–198):

```diff
 class TradeAction(BaseModel):
-    isin: str
-    asset_subgroup: str
-    sub_category: str
-    recommended_fund: str
-    action: Literal["BUY", "SELL", "EXIT"]
+    isin: Optional[str] = None
+    asset_subgroup: str
+    sub_category: Optional[str] = None
+    recommended_fund: Optional[str] = None
+    action: Literal["BUY", "SELL", "EXIT", "SELL_DIRECT_STOCKS"]
     amount_inr: Decimal
     reason_code: str                 # machine — stable, analytics
     reason_title: str                # customer card header
     reason_text: str                 # customer card body, one sentence
     fund_reason: Optional[str] = None
```

Adjust `SubgroupSummary` docstring (current lines 201–222) to document the frozen-entry contract — no schema change, only intent:

```diff
 class SubgroupSummary(BaseModel):
     """Per-asset_subgroup aggregate: target vs current vs final holding,
     plus the participating fund rows for that subgroup. Built by step 6
     so the presentation layer doesn't have to re-derive these aggregates.

     `actions` includes every fund row that's part of the plan for this
     subgroup — both rows being traded (buy/sell/exit) and rows being
     held as-is (target unchanged within tolerance, or already at target).
     Phantom rows (zero target and zero holding) are dropped. To filter to
     only traded rows, use the `ranks_with_action` count or check each
-    row's pass1_buy_amount / pass1_sell_amount / pass2_sell_amount."""
+    row's pass1_buy_amount / pass1_sell_amount / pass2_sell_amount.
+
+    **Frozen subgroups** (`tax_efficient_equities`, `non_mf_equities`):
+    step6 emits these with `actions = []` because they have no MF rows
+    in the engine — their amounts come straight from
+    `practical_allocation.corpus_breakdown` and no trades are generated
+    against them inside the engine (`SELL_DIRECT_STOCKS` rides on
+    `trade_list`, not on `SubgroupSummary.actions`)."""
     asset_subgroup: str
     ...
     actions: list[FundRowAfterStep5] = Field(default_factory=list)
```

Extend `RebalancingComputeResponse` (current lines 225–231):

```diff
 class RebalancingComputeResponse(BaseModel):
     rows: list[FundRowAfterStep5]                             # full audit trail
     subgroups: list[SubgroupSummary] = Field(default_factory=list)  # presentation
     totals: RebalancingTotals
     metadata: RebalancingRunMetadata
     trade_list: list[TradeAction] = Field(default_factory=list)
     warnings: list[RebalancingWarning] = Field(default_factory=list)
+    # Verbatim passthrough of the practical allocation output for the
+    # ideal-vs-practical UI. Same shape as GoalAllocationOutput + an extras
+    # `corpus_breakdown` block surfacing ELSS / non-MF equity numbers.
+    practical_allocation: PracticalAllocationOutput
```

- [ ] **Step 4: Run the Task-5 tests — should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "request_carries_practical or trade_action_accepts or subgroup_summary_actions" -v
```

Expected: 3 passed.

- [ ] **Step 5: Expect the rest of the engine suite to fail (test_step1_caps etc still pass; step6 / pipeline tests will break until Task 9–10)**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/ -v
```

Expected failures: any test that constructs `RebalancingComputeRequest(total_corpus=...)` positionally or that builds `RebalancingComputeResponse` without `practical_allocation`. These get fixed in Task 9 (step6) and the bridge fixtures in Task 11. The engine-internal tests are gitignored — no commit pressure.

- [ ] **Step 6: Commit**

```bash
git add AI_Agents/src/Rebalancing/models.py
git commit -m "feat(rebalancing): nest PracticalAllocationInput on request; widen TradeAction + add practical_allocation on response (C.1 / C.2 / C.6)

RebalancingComputeRequest now carries a practical_allocation_input:
PracticalAllocationInput field; the previous scalar total_corpus is
removed and exposed via a backwards-compatible @property that reads
through the nested input. ELSS and non-MF equity scalars travel inside
practical_allocation_input.

RebalancingComputeResponse gains a practical_allocation:
PracticalAllocationOutput passthrough field so chat / UI can render
ideal-vs-practical bars without re-running the practical engine.

TradeAction widens action to include SELL_DIRECT_STOCKS and makes
isin, sub_category, recommended_fund Optional so step6 can emit
the single direct-stock-trim trade without a per-fund identity.

SubgroupSummary docstring documents the frozen-entry contract
(tax_efficient_equities, non_mf_equities) — no schema change, only intent.

Spec refs: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.1 §C.2 §C.6"
```

---

### Task 6: Add `sell_excess_direct_stocks` rationale

**Files:**
- Modify: `AI_Agents/src/Rebalancing/rationales.py`

- [ ] **Step 1: Write the failing test (engine-internal)**

Append to `test_part_c.py`:

```python
def test_sell_excess_direct_stocks_rationale_present():
    from Rebalancing.rationales import RATIONALES, get_rationale
    assert "sell_excess_direct_stocks" in RATIONALES
    title, text = get_rationale("sell_excess_direct_stocks")
    assert title  # non-empty
    assert "stock" in text.lower()
    # Body uses an `{amount}` placeholder so step6 can format the trim INR.
    assert "{amount}" in text
```

- [ ] **Step 2: Run — expect KeyError / assertion fail**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py::test_sell_excess_direct_stocks_rationale_present -v
```

Expected: FAIL.

- [ ] **Step 3: Add the entry in `rationales.py`**

Append inside the `RATIONALES` dict (after `exit_low_rated`):

```diff
     "exit_low_rated": {
         "title": "Exit — rating below threshold",
         "text": (
             "This fund's quality rating has fallen below our minimum threshold. "
             "Exiting it maintains the quality standard of your portfolio."
         ),
     },
+    "sell_excess_direct_stocks": {
+        "title": "Trim direct-stock holdings",
+        "text": (
+            "Your direct stock holdings exceed the level we'd recommend for "
+            "your wealth bracket — concentrated single-stock positions are "
+            "hard to manage well without active research. We recommend "
+            "selling ₹{amount} and reallocating to diversified mutual funds, "
+            "which give you the same equity exposure with much less "
+            "single-name risk."
+        ),
+    },
 }
```

- [ ] **Step 4: Run — should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py::test_sell_excess_direct_stocks_rationale_present -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/rationales.py
git commit -m "feat(rebalancing): add sell_excess_direct_stocks rationale (C.7)

New rationale entry consumed by step6 when
practical.corpus_breakdown.excess_direct_stocks_inr > 0 — the NFA-banded
cap in the practical engine has trimmed back the customer's direct-stock
allocation and we surface that as a single SELL_DIRECT_STOCKS trade card.
The body contains an {amount} placeholder so step6 can substitute the
trim INR at format time.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.7"
```

---

### Task 7: Drop ELSS row special-case in `step2_compare_and_decide.py`

**Files:**
- Modify: `AI_Agents/src/Rebalancing/steps/step2_compare_and_decide.py:36-77`

**Why:** ELSS rows no longer arrive in `req.rows` (input builder filters them out). The `asset_subgroup == "tax_efficient_equities"` branch is now dead code, and the BAD_FUND_DETECTED ELSS-exclusion clause that pairs with it is also dead.

- [ ] **Step 1: Write the failing test (engine-internal)**

Append to `test_part_c.py`:

```python
def test_step2_treats_elss_row_like_any_other_subgroup_if_present():
    """After the C.4 removal, step2 no longer special-cases ELSS — if an ELSS
    row somehow leaks through (input-builder bug), it computes diff the
    normal way. This is a regression-guard test, not an expected real path."""
    from decimal import Decimal

    from Rebalancing.models import FundRowAfterStep1, RebalancingComputeRequest
    from Rebalancing.steps import step2_compare_and_decide
    from practical_asset_allocation.pipeline import PracticalAllocationInput  # type: ignore[import-not-found]

    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000,
        osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=10_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=10_000_000, goals=[],
        mf_corpus=10_000_000, non_mf_equity_corpus=0, elss_corpus=0,
    )
    req = RebalancingComputeRequest(
        practical_allocation_input=inp,
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=[],
    )
    row = FundRowAfterStep1(
        asset_subgroup="tax_efficient_equities",
        sub_category="ELSS",
        recommended_fund="Axis LT Equity",
        isin="INF846K01EW2",
        rank=1,
        target_amount_pre_cap=Decimal("100000"),
        present_allocation_inr=Decimal("200000"),
        max_pct=10.0,
        target_pre_cap_pct=1.0,
        target_own_capped_pct=1.0,
        final_target_pct=1.0,
        final_target_amount=Decimal("100000"),
    )
    out, _warnings = step2_compare_and_decide.apply([row], req)
    # Pre-removal: diff = final_target = +100000 (pure buy-demand). Post-removal:
    # diff = final - present = -100000 (trim signal). The new test asserts the
    # general path is taken.
    assert out[0].diff == Decimal("-100000")
```

- [ ] **Step 2: Run — expect FAIL (current code returns +100000)**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py::test_step2_treats_elss_row_like_any_other_subgroup_if_present -v
```

Expected: FAIL.

- [ ] **Step 3: Edit `step2_compare_and_decide.py`**

Replace the entire `for r in rows:` body (current lines 36–77) with the simpler unbranched version:

```diff
     for r in rows:
-        if r.asset_subgroup == "tax_efficient_equities":
-            # ELSS goal from the AA engine is "fresh purchase headroom this
-            # FY", not a portfolio-share target. Existing ELSS is locked
-            # under the 3-year SEBI lock-in. Treat the goal as pure buy
-            # demand (no comparison to present); never trim or exit, even
-            # for off-list (BAD) ELSS funds.
-            diff = r.final_target_amount
-            exit_flag = False
-            worth_to_change = diff > 0
-        else:
-            diff = r.final_target_amount - r.present_allocation_inr
-            exit_flag = (r.fund_rating < EXIT_FLOOR_RATING) or (not r.is_recommended)
-
-            scale = max(r.final_target_amount, r.present_allocation_inr)
-            threshold = scale * threshold_factor
-            worth_to_change = (abs(diff) >= threshold) or exit_flag
+        # Per C.4 (spec 2026-05-23): the upstream input builder strips ELSS
+        # rows out of `rows`; ELSS exposure now travels as a scalar on
+        # `practical_allocation_input.elss_corpus` and surfaces as a frozen
+        # SubgroupSummary in step6. Step2 therefore has a single uniform
+        # diff/exit/worth-to-change path.
+        diff = r.final_target_amount - r.present_allocation_inr
+        exit_flag = (r.fund_rating < EXIT_FLOOR_RATING) or (not r.is_recommended)
+
+        scale = max(r.final_target_amount, r.present_allocation_inr)
+        threshold = scale * threshold_factor
+        worth_to_change = (abs(diff) >= threshold) or exit_flag

         out.append(
             FundRowAfterStep2(
                 **r.model_dump(),
                 diff=diff,
                 exit_flag=exit_flag,
                 worth_to_change=worth_to_change,
             )
         )

-        if (
-            not r.is_recommended
-            and r.present_allocation_inr > 0
-            and r.asset_subgroup != "tax_efficient_equities"
-        ):
+        if not r.is_recommended and r.present_allocation_inr > 0:
             warnings.append(
                 RebalancingWarning(
                     code=WarningCode.BAD_FUND_DETECTED,
                     message=(
                         f"Held fund {r.isin} ({r.recommended_fund}) is not "
                         f"in the recommended set."
                     ),
                     affected_isins=[r.isin],
                 )
             )

     return out, warnings
```

- [ ] **Step 4: Run the Task-7 test — should pass; full step2 suite should still pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_step2_diff_exit.py AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "step2" -v
```

Expected: all green. The existing `test_step2_diff_exit.py` does not use ELSS rows.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/steps/step2_compare_and_decide.py
git commit -m "feat(rebalancing): drop ELSS row special-case in step2 (C.4)

ELSS rows are now stripped from RebalancingComputeRequest.rows by the
upstream input builder. ELSS exposure travels on
practical_allocation_input.elss_corpus and is surfaced by step6 as a
frozen SubgroupSummary. Step2's pure-buy-demand branch and its paired
BAD_FUND_DETECTED ELSS-exclusion clause are dead code under the new
contract; both removed in favour of a single uniform diff path.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.4"
```

---

### Task 8: Update `pipeline.py` — call practical engine, lift targets, thread to step6

**Files:**
- Modify: `AI_Agents/src/Rebalancing/pipeline.py` (full rewrite — 33 lines)

- [ ] **Step 1: Write the failing test (engine-internal)**

Append to `test_part_c.py`:

```python
def test_pipeline_runs_practical_and_assigns_targets_to_rank1():
    """C.3: pipeline calls practical engine first; rank-1 of each subgroup
    receives the aggregated_subgroups amount as target_amount_pre_cap."""
    from decimal import Decimal

    from practical_asset_allocation.pipeline import PracticalAllocationInput  # type: ignore[import-not-found]
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    from Rebalancing.pipeline import run_rebalancing

    from asset_allocation_pydantic.models import Goal  # type: ignore[import-not-found]

    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000,
        osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=1_000_000,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=1_000_000,
        goals=[Goal(goal_name="Retire", time_to_goal_months=240,
                    amount_needed=1_000_000, goal_priority="non_negotiable")],
        mf_corpus=1_000_000, non_mf_equity_corpus=0, elss_corpus=0,
    )
    req = RebalancingComputeRequest(
        practical_allocation_input=inp,
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=[
            FundRowInput(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                recommended_fund="HDFC Top 100", isin="INF179K01YV8", rank=1,
                target_amount_pre_cap=Decimal(0),
            ),
            FundRowInput(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                recommended_fund="ICICI Bluechip", isin="INF109K012Z3", rank=2,
                target_amount_pre_cap=Decimal(0),
            ),
        ],
    )
    resp = run_rebalancing(req)
    rank1 = next(r for r in resp.rows if r.rank == 1)
    assert rank1.target_amount_pre_cap > 0
    assert resp.practical_allocation is not None
    assert resp.practical_allocation.grand_total == 1_000_000
```

- [ ] **Step 2: Run — expect FAIL on the test**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py::test_pipeline_runs_practical_and_assigns_targets_to_rank1 -v
```

Expected: FAIL — `resp.practical_allocation` does not exist on the response built by the current pipeline (also: `target_amount_pre_cap` stays 0 because there's no assignment step).

- [ ] **Step 3: Rewrite `pipeline.py`**

```python
"""Pipeline orchestrator. Pure-sync, DB-free.

Runs the upstream practical asset-allocation engine first, lifts its
per-subgroup totals onto rank-1 MF rows, then threads the six rebalancing
steps in order. The practical output is also passed through verbatim on
the response for the ideal-vs-practical UI.
"""

from __future__ import annotations

from decimal import Decimal

# Documented cross-agent import per spec §B.1 / §C.3.
from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
    PracticalAllocationOutput,
    run_practical_allocation,
)

from .models import (
    FundRowInput,
    RebalancingComputeRequest,
    RebalancingComputeResponse,
)
from .steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step3_tax_classification,
    step4_initial_trades_under_stcg_cap,
    step5_loss_offset_top_up,
    step6_presentation,
)


# Subgroups that exist in `practical.aggregated_subgroups` but have no MF
# rows in the engine — their amounts are surfaced as frozen
# `SubgroupSummary` entries in step6, not lifted onto rank-1 rows here.
_FROZEN_SUBGROUPS: frozenset[str] = frozenset({
    "tax_efficient_equities",
    "non_mf_equities",
})


def _assign_targets_to_rank1(
    rows: list[FundRowInput],
    practical: PracticalAllocationOutput,
) -> list[FundRowInput]:
    """Return a new list of rows where the rank-1 row of each MF subgroup
    has `target_amount_pre_cap` set to the practical engine's aggregated
    total for that subgroup. Rows for frozen subgroups (ELSS, non-MF
    equity) and rank-2+ MF rows are passed through unchanged."""
    target_by_subgroup: dict[str, Decimal] = {
        r.subgroup: Decimal(str(r.total))
        for r in practical.aggregated_subgroups
        if r.subgroup not in _FROZEN_SUBGROUPS
    }
    out: list[FundRowInput] = []
    for r in rows:
        if r.rank == 1 and r.asset_subgroup in target_by_subgroup:
            out.append(r.model_copy(update={
                "target_amount_pre_cap": target_by_subgroup[r.asset_subgroup],
            }))
        else:
            out.append(r)
    return out


def run_rebalancing(request: RebalancingComputeRequest) -> RebalancingComputeResponse:
    # 1. Practical allocation (holdings-aware; consumes ELSS + non-MF scalars).
    practical = run_practical_allocation(request.practical_allocation_input)

    # 2. Lift per-subgroup MF targets onto rank-1 rows.
    rows_with_targets = _assign_targets_to_rank1(request.rows, practical)

    # 3. Six-step rebalancing engine (interface unchanged).
    s1_rows, s1_warnings, unrebalanced_total = step1_cap_and_spill.apply(
        rows_with_targets, request
    )
    s2_rows, s2_warnings = step2_compare_and_decide.apply(s1_rows, request)
    s3_rows = step3_tax_classification.apply(s2_rows, request)
    s4_rows, s4_warnings = step4_initial_trades_under_stcg_cap.apply(s3_rows, request)
    s5_rows = step5_loss_offset_top_up.apply(s4_rows, request)

    all_warnings = list(s1_warnings) + list(s2_warnings) + list(s4_warnings)
    return step6_presentation.apply(
        s5_rows, request, all_warnings, unrebalanced_total, practical=practical,
    )
```

- [ ] **Step 4: Don't run the pipeline test yet — step6 doesn't accept `practical=...` until Task 9. Move on.**

(If you do run, you'll see a `TypeError: apply() got an unexpected keyword argument 'practical'`. That's resolved next task.)

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/pipeline.py
git commit -m "feat(rebalancing): pipeline runs practical engine first; lifts targets to rank-1 (C.3)

run_rebalancing now (1) calls
practical_asset_allocation.run_practical_allocation on the nested input,
(2) lifts per-subgroup totals from practical.aggregated_subgroups onto
the matching rank-1 MF rows via the new _assign_targets_to_rank1 helper,
(3) runs the existing six-step pipeline, and (4) passes practical to
step6 for frozen-subgroup emission and response passthrough.

Frozen subgroups (tax_efficient_equities, non_mf_equities) are
deliberately skipped by the target-assignment helper — they have no MF
rows by design and surface only via step6's frozen SubgroupSummary
entries plus the optional SELL_DIRECT_STOCKS TradeAction.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.3"
```

---

### Task 9: Update `step6_presentation.py` — accept `practical`, emit frozen subgroups + SELL_DIRECT_STOCKS, set passthrough

**Files:**
- Modify: `AI_Agents/src/Rebalancing/steps/step6_presentation.py` (signature + body changes; lines 40, 44–56 for knob snapshot, 167–240 for `apply`).

- [ ] **Step 1: Write the failing tests (engine-internal)**

Append to `test_part_c.py`:

```python
def test_step6_emits_frozen_tax_efficient_equities_subgroup():
    """C.6(a): even with no ELSS in `rows`, response.subgroups contains a
    `tax_efficient_equities` frozen entry sourced from corpus_breakdown."""
    from Rebalancing.pipeline import run_rebalancing
    req = _build_request(elss_corpus=500_000)  # see helper below
    resp = run_rebalancing(req)
    tee = next(
        sg for sg in resp.subgroups if sg.asset_subgroup == "tax_efficient_equities"
    )
    assert tee.goal_target_inr == 500_000
    assert tee.current_holding_inr == 500_000
    assert tee.suggested_final_holding_inr == 500_000
    assert tee.actions == []


def test_step6_emits_frozen_non_mf_equities_subgroup():
    from Rebalancing.pipeline import run_rebalancing
    req = _build_request(non_mf_equity_corpus=1_000_000)
    resp = run_rebalancing(req)
    nme = next(
        sg for sg in resp.subgroups if sg.asset_subgroup == "non_mf_equities"
    )
    assert nme.current_holding_inr == 1_000_000
    assert nme.suggested_final_holding_inr <= nme.current_holding_inr


def test_step6_emits_sell_direct_stocks_when_excess_positive():
    """C.6(b): excess > 0 → SELL_DIRECT_STOCKS TradeAction with the right amount."""
    from Rebalancing.pipeline import run_rebalancing
    # NFA band default (< 1Cr) caps non-MF equity at 33% of equity_amount.
    # Set up so excess > 0.
    req = _build_request(
        total_corpus=5_000_000, mf_corpus=2_000_000,
        non_mf_equity_corpus=3_000_000, elss_corpus=0,
        net_financial_assets=5_000_000,  # < 1Cr → 33% cap
    )
    resp = run_rebalancing(req)
    sells = [t for t in resp.trade_list if t.action == "SELL_DIRECT_STOCKS"]
    assert len(sells) == 1
    sell = sells[0]
    assert sell.isin is None
    assert sell.recommended_fund is None
    assert sell.reason_code == "sell_excess_direct_stocks"
    assert sell.amount_inr > 0
    # The placeholder is filled with the rupee amount.
    assert "{amount}" not in sell.reason_text


def test_step6_no_sell_direct_stocks_when_excess_zero():
    from Rebalancing.pipeline import run_rebalancing
    req = _build_request(non_mf_equity_corpus=0)
    resp = run_rebalancing(req)
    sells = [t for t in resp.trade_list if t.action == "SELL_DIRECT_STOCKS"]
    assert sells == []


def test_step6_passes_through_practical_allocation():
    from Rebalancing.pipeline import run_rebalancing
    req = _build_request()
    resp = run_rebalancing(req)
    assert resp.practical_allocation is not None
    assert resp.practical_allocation.corpus_breakdown is not None


def _build_request(
    *,
    total_corpus: int = 10_000_000,
    mf_corpus: int = 8_000_000,
    non_mf_equity_corpus: int = 1_000_000,
    elss_corpus: int = 500_000,
    net_financial_assets: int = 10_000_000,
):
    """Shared minimal builder for the step6 tests."""
    from decimal import Decimal
    from asset_allocation_pydantic.models import Goal  # type: ignore[import-not-found]
    from practical_asset_allocation.pipeline import PracticalAllocationInput  # type: ignore[import-not-found]
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest

    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000,
        osi=0.0, savings_rate_adjustment="none", gap_exceeds_3=False,
        shortfall_amount=0.0, total_corpus=total_corpus,
        monthly_household_expense=100_000, effective_tax_rate=15.0,
        net_financial_assets=net_financial_assets,
        goals=[Goal(goal_name="Retire", time_to_goal_months=240,
                    amount_needed=total_corpus, goal_priority="non_negotiable")],
        mf_corpus=mf_corpus,
        non_mf_equity_corpus=non_mf_equity_corpus,
        elss_corpus=elss_corpus,
    )
    return RebalancingComputeRequest(
        practical_allocation_input=inp,
        tax_regime="new",
        effective_tax_rate_pct=30.0,
        rows=[
            FundRowInput(
                asset_subgroup="low_beta_equities",
                sub_category="Large Cap Fund",
                recommended_fund="HDFC Top 100", isin="INF179K01YV8", rank=1,
                target_amount_pre_cap=Decimal(0),
            ),
        ],
    )
```

- [ ] **Step 2: Run — expect FAIL (step6 doesn't accept `practical`; pipeline already passes it)**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -k "step6_emits or step6_no_sell or step6_passes" -v
```

Expected: FAIL — `TypeError: apply() got an unexpected keyword argument 'practical'`.

- [ ] **Step 3: Edit `step6_presentation.py`**

Update imports (current lines 16–41):

```diff
 from ..config import (
     ENGINE_VERSION,
     EXIT_FLOOR_RATING,
     LTCG_ANNUAL_EXEMPTION_INR,
     LTCG_RATE_EQUITY_PCT,
     MULTI_FUND_CAP_PCT,
     OTHERS_FUND_CAP_PCT,
     REBALANCE_MIN_CHANGE_PCT,
     ST_THRESHOLD_MONTHS_DEBT,
     ST_THRESHOLD_MONTHS_EQUITY,
     STCG_RATE_EQUITY_PCT,
 )
 from ..models import (
     FundRowAfterStep5,
     KnobSnapshot,
     RebalancingComputeRequest,
     RebalancingComputeResponse,
     RebalancingRunMetadata,
     RebalancingTotals,
     RebalancingWarning,
     SubgroupSummary,
     TradeAction,
 )
 from ..rationales import get_rationale
-from ..tables import MULTI_FUND_CAP_SUBGROUPS
+from ..tables import SUBGROUP_FUND_CAP_PCT
 from ..utils import estimate_tax
+
+# Cross-agent type — same documented exception as in pipeline.py / models.py.
+from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
+    PracticalAllocationOutput,
+)
```

Update `_build_knob_snapshot` (current lines 44–56) to surface the dict keys:

```diff
 def _build_knob_snapshot() -> KnobSnapshot:
     return KnobSnapshot(
         multi_fund_cap_pct=MULTI_FUND_CAP_PCT,
         others_fund_cap_pct=OTHERS_FUND_CAP_PCT,
         rebalance_min_change_pct=REBALANCE_MIN_CHANGE_PCT,
         exit_floor_rating=EXIT_FLOOR_RATING,
         ltcg_annual_exemption_inr=LTCG_ANNUAL_EXEMPTION_INR,
         stcg_rate_equity_pct=STCG_RATE_EQUITY_PCT,
         ltcg_rate_equity_pct=LTCG_RATE_EQUITY_PCT,
         st_threshold_months_equity=ST_THRESHOLD_MONTHS_EQUITY,
         st_threshold_months_debt=ST_THRESHOLD_MONTHS_DEBT,
-        multi_fund_cap_subgroups=sorted(MULTI_FUND_CAP_SUBGROUPS),
+        # List of subgroups with a non-default per-fund cap (sorted for stable
+        # output). Includes multi_asset (20%) and short_debt (30%).
+        multi_fund_cap_subgroups=sorted(SUBGROUP_FUND_CAP_PCT.keys()),
     )
```

Add the frozen-subgroup builder + SELL_DIRECT_STOCKS helper near the top of the file (after `_build_knob_snapshot`):

```python
def _frozen_subgroups(practical: PracticalAllocationOutput) -> list[SubgroupSummary]:
    """Two frozen entries for non-MF exposures the engine doesn't trade
    per-fund. Sourced from practical.corpus_breakdown."""
    cb = practical.corpus_breakdown
    elss = Decimal(str(cb.elss_corpus_inr))
    nme_input = Decimal(str(cb.non_mf_equity_input_inr))
    nme_actual = Decimal(str(cb.non_mf_equity_actual_inr))

    out: list[SubgroupSummary] = []
    if elss > 0:
        out.append(SubgroupSummary(
            asset_subgroup="tax_efficient_equities",
            goal_target_inr=elss,
            current_holding_inr=elss,
            suggested_final_holding_inr=elss,
            rebalance_inr=Decimal(0),
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            ranks_total=0,
            ranks_with_holding=0,
            ranks_with_action=0,
            actions=[],
        ))
    if nme_input > 0 or nme_actual > 0:
        out.append(SubgroupSummary(
            asset_subgroup="non_mf_equities",
            goal_target_inr=nme_actual,
            current_holding_inr=nme_input,
            suggested_final_holding_inr=nme_actual,
            rebalance_inr=nme_actual - nme_input,
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            ranks_total=0,
            ranks_with_holding=0,
            ranks_with_action=0,
            actions=[],
        ))
    return out


def _sell_direct_stocks_action(
    practical: PracticalAllocationOutput,
) -> TradeAction | None:
    """C.6(b): single SELL_DIRECT_STOCKS trade when the NFA-banded cap has
    trimmed the customer's direct-stock allocation."""
    excess = Decimal(str(practical.corpus_breakdown.excess_direct_stocks_inr))
    if excess <= 0:
        return None
    title, text = get_rationale("sell_excess_direct_stocks")
    # `common.format_inr_indian` is the project standard (see
    # `AI_Agents/src/common.py`); import locally to avoid a top-level dep
    # on the cross-agent helper at module load time.
    from common import format_inr_indian  # type: ignore[import-not-found]

    return TradeAction(
        isin=None,
        asset_subgroup="non_mf_equities",
        sub_category=None,
        recommended_fund=None,
        action="SELL_DIRECT_STOCKS",
        amount_inr=excess,
        reason_code="sell_excess_direct_stocks",
        reason_title=title,
        reason_text=text.replace("{amount}", format_inr_indian(int(excess))),
        fund_reason=None,
    )
```

Rewrite `apply(...)` to accept `practical`, switch the metadata block to read the nested total_corpus:

```diff
 def apply(
     rows: list[FundRowAfterStep5],
     request: RebalancingComputeRequest,
     warnings: list[RebalancingWarning],
     unrebalanced_remainder_inr: Decimal,
+    practical: PracticalAllocationOutput,
 ) -> RebalancingComputeResponse:
     total_buy = sum((r.pass1_buy_amount for r in rows), Decimal(0))
     total_sell = sum((r.pass1_sell_amount + r.pass2_sell_amount for r in rows), Decimal(0))
     # ... unchanged ...

     metadata = RebalancingRunMetadata(
         computed_at=datetime.now(timezone.utc),
         engine_version=ENGINE_VERSION,
-        request_corpus_inr=request.total_corpus,
+        request_corpus_inr=Decimal(str(request.practical_allocation_input.total_corpus)),
         knob_snapshot=_build_knob_snapshot(),
         request_id=request.request_id,
     )

     trade_list: list[TradeAction] = []
     for r in rows:
         ta = _trade_action_for(r)
         if ta:
             trade_list.append(ta)
+    sds = _sell_direct_stocks_action(practical)
+    if sds is not None:
+        trade_list.append(sds)

+    subgroups = _build_subgroups(rows) + _frozen_subgroups(practical)
+    # Preserve the biggest-first sort across MF + frozen entries.
+    subgroups.sort(
+        key=lambda s: (-float(s.goal_target_inr), -float(s.current_holding_inr))
+    )
+
     return RebalancingComputeResponse(
         rows=rows,
-        subgroups=_build_subgroups(rows),
+        subgroups=subgroups,
         totals=totals,
         metadata=metadata,
         trade_list=trade_list,
         warnings=warnings,
+        practical_allocation=practical,
     )
```

- [ ] **Step 4: Run the Task-8 + Task-9 tests — should pass**

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -v
```

Expected: all Part-C tests green. The full engine suite (`test_step1_caps.py` … `test_step6_presentation.py`) may still have stale call-sites that pass `total_corpus=...` to `RebalancingComputeRequest`; the Task-5 backwards-compat `@property` does NOT make `total_corpus` a constructor field, so these existing per-step tests will fail at construction. Per memory note ("rebalancing test damage … defer to upcoming rebalancing module rewrite"), do not chase those fixes here — the bridge tests in Task 11 are the customer contract.

- [ ] **Step 5: Commit**

```bash
git add AI_Agents/src/Rebalancing/steps/step6_presentation.py
git commit -m "feat(rebalancing): step6 emits frozen subgroups + SELL_DIRECT_STOCKS + practical passthrough (C.6)

step6.apply now takes a practical: PracticalAllocationOutput keyword
and (1) appends two frozen SubgroupSummary entries —
tax_efficient_equities and non_mf_equities — sourced from
practical.corpus_breakdown, (2) emits a single SELL_DIRECT_STOCKS
TradeAction when corpus_breakdown.excess_direct_stocks_inr > 0
(reason_code='sell_excess_direct_stocks', isin=None,
recommended_fund=None), (3) sets practical_allocation = practical on
the response, and (4) switches the knob-snapshot's
multi_fund_cap_subgroups field to read from the new
SUBGROUP_FUND_CAP_PCT dict. metadata.request_corpus_inr now reads the
nested practical_allocation_input.total_corpus explicitly instead of
relying on the backwards-compat property.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.6"
```

---

### Task 10: Extend `app/services/ai_bridge/rebalancing/input_builder.py` — read stocks, filter ELSS, build PracticalAllocationInput

**Files:**
- Modify: `app/services/ai_bridge/rebalancing/input_builder.py`

**Why:** The bridge layer is the only place that knows where the customer's holdings actually live (DB tables). Per C.10 it must:
1. Read `StockTransaction` and sum signed BUY − SELL of `amount` → `non_mf_equity_corpus`.
2. Sum MF rows (including ELSS) → `mf_corpus`.
3. Sum the ELSS subset (rows whose CSV `asset_subgroup == "tax_efficient_equities"`) → `elss_corpus`.
4. Compute `total_corpus = mf_corpus + non_mf_equity_corpus + cash`. Cash is not yet tracked in a dedicated table; pass `0` for now (TODO comment to wire when added).
5. Build `PracticalAllocationInput` from the customer profile / goals (from `allocation_output.client_summary`) plus the four new scalars.
6. Filter ELSS rows out of `rows` before sending to the engine.
7. Set `target_amount_pre_cap = 0` on the remaining recommended rows (the engine assigns post-step0 via `_assign_targets_to_rank1`).

- [ ] **Step 1: Write the failing integration tests (COMMITTED)**

Append to `app/services/ai_bridge/rebalancing/tests/test_input_builder.py`:

```python
@pytest.mark.asyncio
async def test_builder_excludes_elss_rows(
    db_session,
    fixture_user_with_elss_holding,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """ELSS holdings must not appear as MF rows; they ride on
    practical_allocation_input.elss_corpus instead."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    user, _elss_isin = fixture_user_with_elss_holding
    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(user, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    assert all(
        r.asset_subgroup != "tax_efficient_equities" for r in request.rows
    )
    assert request.practical_allocation_input.elss_corpus > 0


@pytest.mark.asyncio
async def test_builder_sums_non_mf_equity_from_stock_transactions(
    db_session,
    fixture_user_with_stock_holding,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """non_mf_equity_corpus = sum of cost basis from StockTransaction BUYs minus SELLs."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    user, expected_total = fixture_user_with_stock_holding
    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(user, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    assert (
        request.practical_allocation_input.non_mf_equity_corpus == expected_total
    )


@pytest.mark.asyncio
async def test_builder_sets_target_amount_zero_on_all_rows(
    db_session,
    fixture_user_with_holdings,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """Post-C.10 the engine assigns targets; the builder leaves them at 0."""
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )
    from decimal import Decimal

    user, _ = fixture_user_with_holdings
    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(user, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    assert all(r.target_amount_pre_cap == Decimal(0) for r in request.rows)


@pytest.mark.asyncio
async def test_builder_passes_through_total_corpus_via_practical_input(
    db_session,
    fixture_user_with_two_holdings,
    fixture_goal_allocation_output_one_subgroup,
    fixture_seed_low_beta_navs,
    fixture_one_subgroup_ranking,
):
    """request.practical_allocation_input.total_corpus must equal MF + non-MF + cash."""
    from decimal import Decimal
    from app.services.ai_bridge.rebalancing.input_builder import (
        build_rebalancing_input_for_user,
    )

    request, _ = await build_rebalancing_input_for_user(
        _ctx_for(fixture_user_with_two_holdings, db_session),
        fixture_goal_allocation_output_one_subgroup,
    )
    mf_expected = Decimal("10") * Decimal("60") + Decimal("5") * Decimal("80")
    assert request.practical_allocation_input.mf_corpus == float(mf_expected)
    assert request.practical_allocation_input.non_mf_equity_corpus == 0.0
    assert request.practical_allocation_input.total_corpus == float(mf_expected)
```

Also note: the existing `test_total_corpus_sums_held_market_values` (lines 113–133 of the file) still asserts `request.total_corpus == expected`. That property is preserved on `RebalancingComputeRequest` as a derived accessor (Task 5), so the existing test continues to pass without modification. The existing `test_missing_tax_profile_uses_defaults` asserts `request.stcg_offset_budget_inr is None` and `request.rounding_step == 100` — both still valid.

- [ ] **Step 2: Add the new fixtures in `conftest.py`**

Append to `app/services/ai_bridge/rebalancing/tests/conftest.py`:

```python
@pytest_asyncio.fixture
async def fixture_user_with_elss_holding(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> tuple[User, str]:
    """User with one ELSS MF holding (asset_subgroup='tax_efficient_equities')."""
    elss_isin = "INF846K01EW2"
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{elss_isin}",
        isin=elss_isin,
        units=Decimal("100"),
        nav=Decimal("50"),
        txn_date=date(2024, 1, 1),
        asset_subgroup="tax_efficient_equities",
        sub_category="ELSS",
    )
    return fixture_user_with_dob, elss_isin


@pytest_asyncio.fixture
async def fixture_user_with_stock_holding(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> tuple[User, float]:
    """User with one direct-stock holding; returns (user, expected_total_inr)."""
    from app.models.stocks.company_metadata import CompanyMetadata
    from app.models.stocks.enums import StockTransactionType
    from app.models.stocks.stock_transaction import StockTransaction

    existing = (await db_session.execute(
        select(CompanyMetadata).where(CompanyMetadata.symbol == "RELIANCE")
    )).scalar_one_or_none()
    if existing is None:
        db_session.add(CompanyMetadata(symbol="RELIANCE", name="Reliance Industries"))
        await db_session.flush()

    buy_amount = 200_000.0  # 100 shares @ 2000
    db_session.add(StockTransaction(
        user_id=fixture_user_with_dob.id,
        symbol="RELIANCE",
        transaction_type=StockTransactionType.BUY,
        transaction_date=date(2024, 1, 1),
        quantity=Decimal("100"),
        price=Decimal("2000"),
        amount=Decimal(str(buy_amount)),
    ))
    await db_session.flush()
    return fixture_user_with_dob, buy_amount
```

If `CompanyMetadata` has more required fields, read `app/models/stocks/company_metadata.py` and populate them. If `select` is not already imported at the top of `conftest.py`, add `from sqlalchemy import select`.

- [ ] **Step 3: Run — expect FAILs on the new tests**

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
pytest app/services/ai_bridge/rebalancing/tests/test_input_builder.py -v
```

Expected: 4 new tests FAIL (builder doesn't filter ELSS; no `practical_allocation_input`; targets are non-zero). Existing tests continue to use `request.total_corpus` via the property — should pass.

- [ ] **Step 4: Edit `input_builder.py`**

Add imports near the existing ones (around line 9):

```diff
 from sqlalchemy import select

 from app.models.profile.tax_profile import TaxProfile
+from app.models.stocks.enums import StockTransactionType
+from app.models.stocks.stock_transaction import StockTransaction
 from app.services.ai_bridge.common import ensure_ai_agents_path
```

Add the practical import in the `ensure_ai_agents_path()` block (around line 37):

```diff
 from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
     GoalAllocationOutput,
 )
+from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]  # noqa: E402
+    PracticalAllocationInput,
+)
 from Rebalancing.models import (  # type: ignore[import-not-found]  # noqa: E402
     FundRowInput,
     RebalancingComputeRequest,
 )
```

Add a helper for the stock sum (near `_resolve_tax_inputs`):

```python
async def _sum_non_mf_equity(db: "AsyncSession", *, user_id: "uuid.UUID") -> Decimal:
    """Signed cost basis from StockTransaction: sum(amount where BUY) − sum(amount where SELL).

    Mark-to-market price is out of scope for v1 — uses cost basis (StockTransaction.amount),
    matching how MfTransaction-derived MF rows are currently summed. Replace with a
    current-price multiplication once StockPriceHistory is reliably fresh.
    """
    rows = (await db.execute(
        select(StockTransaction.transaction_type, StockTransaction.amount)
        .where(StockTransaction.user_id == user_id)
    )).all()
    total = Decimal(0)
    for ttype, amt in rows:
        sign = Decimal(1) if ttype == StockTransactionType.BUY else Decimal(-1)
        total += sign * Decimal(str(amt))
    return max(total, Decimal(0))


def _is_elss_row(row: FundRowInput) -> bool:
    return row.asset_subgroup == "tax_efficient_equities"
```

Rewrite the tail of `build_rebalancing_input_for_user` (currently from line 261 — "7. Total corpus = sum of held market values."):

```diff
-    # 7. Total corpus = sum of held market values.
-    total_corpus = sum(
-        (r.present_allocation_inr for r in rows if r.present_allocation_inr > 0),
-        start=Decimal(0),
-    )
-
-    # 8. Tax inputs. Query directly — relationship may not be eager-loaded.
+    # 7. Partition rows: ELSS becomes a scalar (practical_allocation_input.elss_corpus);
+    #    the rest stays as the engine's per-fund row list.
+    elss_rows = [r for r in rows if _is_elss_row(r)]
+    mf_rows_only = [r for r in rows if not _is_elss_row(r)]
+
+    # Builder no longer pre-assigns targets — the engine pipeline lifts them
+    # from practical.aggregated_subgroups onto rank-1 rows post-step0.
+    mf_rows_only = [
+        r.model_copy(update={"target_amount_pre_cap": Decimal(0)})
+        for r in mf_rows_only
+    ]
+
+    # 8. Corpus scalars for the practical engine.
+    mf_corpus_inr = sum(
+        (r.present_allocation_inr for r in rows if r.present_allocation_inr > 0),
+        start=Decimal(0),
+    )
+    elss_corpus_inr = sum(
+        (r.present_allocation_inr for r in elss_rows),
+        start=Decimal(0),
+    )
+    non_mf_equity_corpus_inr = await _sum_non_mf_equity(db, user_id=user.id)
+    # TODO(cash): cash holdings (SB account, FD, RD, liquid scheme outside MF)
+    # are not yet aggregated by a dedicated table. Treat as 0 for v1; revisit
+    # once a cash-holdings source exists. This means total_corpus here is
+    # MF + non-MF equity only, which matches the current behaviour of the
+    # legacy `total_corpus = sum(present)` line replaced above for MF-only users.
+    cash_inr = Decimal(0)
+    total_corpus_inr = mf_corpus_inr + non_mf_equity_corpus_inr + cash_inr
+
+    # 9. Build PracticalAllocationInput.
+    practical_input = _build_practical_input(
+        allocation_output=allocation_output,
+        total_corpus_inr=total_corpus_inr,
+        mf_corpus_inr=mf_corpus_inr,
+        non_mf_equity_corpus_inr=non_mf_equity_corpus_inr,
+        elss_corpus_inr=elss_corpus_inr,
+    )
+
+    # 10. Tax inputs. Query directly — relationship may not be eager-loaded.
     tax_profile = (await db.execute(
         select(TaxProfile).where(TaxProfile.user_id == user.id)
     )).scalar_one_or_none()
     tax_inputs = _resolve_tax_inputs(tax_profile)

     # ... (chat_overrides block unchanged) ...

     request = RebalancingComputeRequest(
-        total_corpus=total_corpus,
+        practical_allocation_input=practical_input,
         tax_regime=tax_inputs["tax_regime"],
         # ... (remaining constructor args unchanged) ...
-        rows=rows,
+        rows=mf_rows_only,
     )
     debug = {
-        "total_corpus": str(total_corpus),
+        "total_corpus": str(total_corpus_inr),
+        "mf_corpus": str(mf_corpus_inr),
+        "elss_corpus": str(elss_corpus_inr),
+        "non_mf_equity_corpus": str(non_mf_equity_corpus_inr),
         "lots_per_isin": {e.isin: len(e.lots) for e in ledger},
         "bad_fund_count": bad_count,
-        "row_count": len(rows),
+        "row_count": len(mf_rows_only),
     }
     return request, debug
```

Add `_build_practical_input` thin helper at the bottom of the file:

```python
def _build_practical_input(
    *,
    allocation_output: GoalAllocationOutput,
    total_corpus_inr: Decimal,
    mf_corpus_inr: Decimal,
    non_mf_equity_corpus_inr: Decimal,
    elss_corpus_inr: Decimal,
) -> "PracticalAllocationInput":
    """Bridge-side PracticalAllocationInput builder.

    Reuses everything we know from the upstream allocation output's
    `client_summary` (profile, goals) and falls back to safe defaults for
    fields the bridge doesn't currently surface (market_commentary,
    multi_asset_composition, advisor cap override). Once Plan B's
    `app/services/ai_bridge/practical_allocation/input_builder.py` lands,
    delegate to it and delete this thin shim.
    """
    cs = allocation_output.client_summary
    return PracticalAllocationInput(
        # Profile + goals carried from the ideal-allocation output.
        effective_risk_score=cs.effective_risk_score,
        age=cs.age,
        annual_income=getattr(cs, "annual_income", 0) or 0,
        osi=getattr(cs, "osi", 0.0) or 0.0,
        savings_rate_adjustment=getattr(cs, "savings_rate_adjustment", "none") or "none",
        gap_exceeds_3=False,
        shortfall_amount=0.0,
        total_corpus=float(total_corpus_inr),
        monthly_household_expense=getattr(cs, "monthly_household_expense", 0) or 0,
        effective_tax_rate=15.0,  # overridden downstream by TaxProfile
        net_financial_assets=float(total_corpus_inr),
        goals=list(cs.goals),
        # Four NEW corpus scalars.
        mf_corpus=float(mf_corpus_inr),
        non_mf_equity_corpus=float(non_mf_equity_corpus_inr),
        elss_corpus=float(elss_corpus_inr),
        # Optional advisor override left unset.
        max_non_mf_equity_pct_client_input=None,
    )
```

**Note on field defaults:** Read `practical_asset_allocation/pipeline.py` for the exact `PracticalAllocationInput` field list — Part B may have required `market_commentary` / `multi_asset_composition` fields. If required, pass default `MarketCommentaryScores()` and `MultiAssetFundComposition()` instances; if Optional, leave unset. **Verify against the merged Part B code before running the test.**

- [ ] **Step 5: Run the bridge tests — should pass**

```bash
pytest app/services/ai_bridge/rebalancing/tests/test_input_builder.py -v
```

Expected: all green (including the 4 new tests).

- [ ] **Step 6: Commit**

```bash
git add app/services/ai_bridge/rebalancing/input_builder.py \
        app/services/ai_bridge/rebalancing/tests/conftest.py \
        app/services/ai_bridge/rebalancing/tests/test_input_builder.py
git commit -m "feat(rebalancing-bridge): build PracticalAllocationInput; filter ELSS rows; sum stock holdings (C.10)

build_rebalancing_input_for_user now:
- Reads StockTransaction for the user and sums cost basis as
  non_mf_equity_corpus (BUY − SELL).
- Sums MF holdings as mf_corpus (ELSS included).
- Sums the ELSS subset of MF holdings as elss_corpus.
- Filters ELSS rows out of the engine's row list (they ride on the
  scalar elss_corpus instead).
- Zeroes target_amount_pre_cap on the remaining MF rows; the engine
  pipeline (_assign_targets_to_rank1) lifts targets after running the
  practical engine.
- Wraps the new scalars in a PracticalAllocationInput and nests it on
  the request as practical_allocation_input.

Cash is treated as 0 for v1 (no dedicated cash-holdings table yet);
TODO comment marks the spot for revisit. Adds two conftest fixtures
(fixture_user_with_elss_holding, fixture_user_with_stock_holding) and
four integration tests covering the new contract.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.10"
```

---

### Task 11: Update bridge `test_service.py` helpers to include `practical_allocation` on the response

**Files:**
- Modify: `app/services/ai_bridge/rebalancing/tests/test_service.py:21-72`, `:75-141`, `:220-285` (the three `_build_*` response factories).

**Why:** The new `RebalancingComputeResponse` carries a non-Optional `practical_allocation: PracticalAllocationOutput`. The existing three response factories in `test_service.py` (`_build_min_response`, `_build_response_with_subgroup`, `_build_response_with_funds`) will now fail validation. Add a `_build_min_practical_output()` helper and thread it into each factory.

- [ ] **Step 1: Run the suite — expect FAIL**

```bash
pytest app/services/ai_bridge/rebalancing/tests/test_service.py -v
```

Expected: `ValidationError: practical_allocation` missing on every test that calls a `_build_*` helper.

- [ ] **Step 2: Add a minimal practical-output factory near the top of `test_service.py`**

After the existing `def _build_min_response():` block:

```python
def _build_min_practical_output():
    """Minimal PracticalAllocationOutput for tests that don't exercise it."""
    from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]
        AssetClassBreakdown,
        AssetClassSplitBlock,
        BucketAssetClassSplit,
        ClientSummary,
    )
    from practical_asset_allocation.pipeline import (  # type: ignore[import-not-found]
        CorpusBreakdown,
        PracticalAllocationOutput,
    )

    empty_bucket = BucketAssetClassSplit(
        bucket="long_term", equity=0, debt=0, others=0,
        equity_pct=0.0, debt_pct=0.0, others_pct=0.0,
    )
    block = AssetClassSplitBlock(
        per_bucket=[empty_bucket],
        equity_total=0, debt_total=0, others_total=0,
        equity_total_pct=0.0, debt_total_pct=0.0, others_total_pct=0.0,
    )
    return PracticalAllocationOutput(
        client_summary=ClientSummary(
            age=35, effective_risk_score=5.0, total_corpus=0.0, goals=[],
        ),
        bucket_allocations=[],
        aggregated_subgroups=[],
        future_investments_summary=[],
        grand_total=0.0,
        all_amounts_in_multiples_of_100=True,
        asset_class_breakdown=AssetClassBreakdown(
            planned=block, recommended=block,
            recommended_sum_matches_grand_total=True,
        ),
        corpus_breakdown=CorpusBreakdown(
            total_corpus_inr=0, mf_corpus_inr=0,
            non_mf_equity_input_inr=0, elss_corpus_inr=0,
            rebalancing_corpus_inr=0, non_mf_equity_actual_inr=0,
            excess_direct_stocks_inr=0, max_non_mf_equity_pct_computed=33.0,
        ),
    )
```

Patch each factory to include `practical_allocation=_build_min_practical_output()`:

```diff
     return RebalancingComputeResponse(
         rows=[],
         subgroups=[],
         ...
         trade_list=[],
+        practical_allocation=_build_min_practical_output(),
     )
```

Apply to all three factories: `_build_min_response`, `_build_response_with_subgroup`, `_build_response_with_funds`.

- [ ] **Step 3: Run the suite — should pass**

```bash
pytest app/services/ai_bridge/rebalancing/tests/test_service.py -v
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add app/services/ai_bridge/rebalancing/tests/test_service.py
git commit -m "test(rebalancing-bridge): thread minimal PracticalAllocationOutput through response factories (C.2)

RebalancingComputeResponse now carries a non-Optional practical_allocation
field; update _build_min_response, _build_response_with_subgroup, and
_build_response_with_funds to construct a stub PracticalAllocationOutput
+ CorpusBreakdown via the new _build_min_practical_output helper.

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.2"
```

---

### Task 12: Verification — full suites, lint, types

**Files:** (read-only)

- [ ] **Step 1: Engine-internal pytest** (local — gitignored tests)

```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/test_part_c.py -v
```

Expected: all green (Part-C local tests).

```bash
PYTHONPATH=AI_Agents/src pytest AI_Agents/src/Rebalancing/Testing/ -v
```

Expected: known-existing failures in per-step files where the old positional `total_corpus=…` request shape is used. **Per project-memory note** ("rebalancing test damage … defer to upcoming rebalancing module rewrite") these are not in scope to fix here. If a previously-passing per-step test fails for a *different* reason (e.g., a typo introduced by Task 4/7/9), fix it before committing.

- [ ] **Step 2: Bridge integration suite** (committed)

```bash
pytest app/services/ai_bridge/rebalancing/tests/ -v
```

Expected: all green. If `test_rebalancing_e2e.py` or other tests use the old request shape, update them in a small follow-up commit (same task).

- [ ] **Step 3: Lint**

```bash
ruff check AI_Agents/src/Rebalancing/ app/services/ai_bridge/rebalancing/
```

Expected: clean.

- [ ] **Step 4: Types**

```bash
pyright AI_Agents/src/Rebalancing/ app/services/ai_bridge/rebalancing/
```

Expected: clean (or no new errors vs baseline; the cross-agent `practical_asset_allocation` imports are typed `# type: ignore[import-not-found]` because the module path is only resolvable after `ensure_ai_agents_path()` runs).

- [ ] **Step 5: Spot-check the chat/persist surface**

```bash
pytest app/services/ai_bridge/rebalancing/tests/test_chat.py \
       app/services/ai_bridge/rebalancing/tests/test_persist.py -v
```

Expected: all green. These tests don't construct responses directly but read them — if a persist test deserializes a stored response that pre-dates the new `practical_allocation` field, a migration / fixture update may be needed. Address in a follow-up commit within this task.

- [ ] **Step 6: If any per-step file or bridge test required a fixture update, commit it separately**

```bash
git add <updated test files>
git commit -m "test(rebalancing): update fixtures for v2 nested practical_allocation_input (Part C)

Spec ref: docs/superpowers/specs/2026-05-23-allocation-rebalancing-v2-design.md §C.1 §C.2"
```

- [ ] **Step 7: Branch is ready for review.** Do not push; defer to local Git workflow.

---

## Self-review checklist

- ✅ Each Part-C spec section C.1–C.12 mapped to at least one task:
  - C.1 (request shape) → Task 5.
  - C.2 (response shape) → Task 5 + Task 11.
  - C.3 (pipeline orchestration) → Task 1 (doc) + Task 8 (code).
  - C.4 (step2 ELSS removal) → Task 7.
  - C.5 (short_debt cap) → Task 2 (config) + Task 3 (tables) + Task 4 (step1).
  - C.6 (step6 frozen subgroups + SELL_DIRECT_STOCKS) → Task 9.
  - C.7 (rationale) → Task 6.
  - C.8 / C.9 (parity confirmed — no change) → no task.
  - C.10 (input-builder plumbing) → Task 10.
  - C.11 (items kept as-is — no change) → no task.
  - C.12 (deferrals — no change) → no task.
- ✅ Every step shows real code; no `TBD` placeholders.
- ✅ Failing-test → green-test cycle for every code change; expected fail outcomes named.
- ✅ All file paths absolute. Line numbers cited against the actual repo at plan-writing time:
  - `models.py` 115–130 (RebalancingComputeRequest), 184–198 (TradeAction), 201–222 (SubgroupSummary), 225–231 (RebalancingComputeResponse).
  - `step1_cap_and_spill.py` 18, 26, 30–31 (helper), 61 + 104 (call-sites).
  - `step2_compare_and_decide.py` 36–77 (ELSS branch + BAD_FUND_DETECTED clause).
  - `step6_presentation.py` 16–41 (imports + knob snapshot), 167–240 (apply).
  - `config.py` 16–17 (insert point).
  - `tables.py` (full rewrite — 12 lines).
  - `pipeline.py` (full rewrite — 33 lines).
  - `rationales.py` 48–55 (append after `exit_low_rated`).
  - `input_builder.py` 9 (model imports), 37 (cross-agent imports), 261 (tail rewrite).
  - `test_input_builder.py` 113–133 (existing test preserved by `total_corpus` property).
  - `test_service.py` 21–72, 75–141, 220–285 (three response factories).
- ✅ The Part-B contract surface used in this plan matches spec §B.2 / §B.3:
  - `PracticalAllocationInput.{total_corpus, mf_corpus, non_mf_equity_corpus, elss_corpus, max_non_mf_equity_pct_client_input}`.
  - `PracticalAllocationOutput.{corpus_breakdown, aggregated_subgroups, grand_total, client_summary, bucket_allocations, future_investments_summary, all_amounts_in_multiples_of_100, asset_class_breakdown}`.
  - `CorpusBreakdown.{total_corpus_inr, mf_corpus_inr, non_mf_equity_input_inr, elss_corpus_inr, rebalancing_corpus_inr, non_mf_equity_actual_inr, excess_direct_stocks_inr, max_non_mf_equity_pct_computed}`.
- ✅ The `_assign_targets_to_rank1` helper deliberately skips `tax_efficient_equities` and `non_mf_equities` so the frozen-subgroup amounts don't get double-counted into MF rows.
- ✅ `model_copy(update=...)` is used (not mutation) so input rows are immutable from the caller's perspective.
- ✅ The `cap_pct_for` helper in `tables.py` returns `OTHERS_FUND_CAP_PCT` for unknown keys; tested in Task 4.
- ✅ The backwards-compat `total_corpus` `@property` on `RebalancingComputeRequest` keeps the bridge's existing `test_total_corpus_sums_held_market_values` test green without modification, while making the breaking field-removal explicit at the constructor.
- ✅ `TradeAction.action` literal includes `"SELL_DIRECT_STOCKS"`; `isin`/`sub_category`/`recommended_fund` are Optional so the no-fund stock-trim trade validates.
- ✅ The `sell_excess_direct_stocks` rationale uses an `{amount}` placeholder that step6 substitutes via `common.format_inr_indian` — matches the spec example body.
- ✅ Cash-handling TODO surfaced explicitly in `_build_practical_input` rather than silently zeroed. Per spec §B.2, `cash = total_corpus − mf_corpus − non_mf_equity_corpus` is implicit; v1 carries cash = 0 until a holdings source exists.
- ✅ Gitignore convention respected: `AI_Agents/src/Rebalancing/Testing/test_part_c.py` is created but never `git add`-ed; `app/services/ai_bridge/rebalancing/tests/*` ARE committed.
- ✅ Cross-agent import flagged as a documented exception in three places: `Rebalancing/CLAUDE.md` (Task 1), `models.py` import comment (Task 5), `pipeline.py` import comment (Task 8).
- ✅ Memory note about "rebalancing test damage" surfaced as a non-blocker in the Conventions section and again in Task 9 / Task 12.
- ✅ No contradictions with the spec:
  - C.10 specifies "Read customer profile → fields shared with AllocationInput" — handled via `allocation_output.client_summary` + safe defaults, with a delegation TODO to a future Plan-B bridge helper.
  - C.10 specifies "Compute total_corpus = MF + ELSS (already in MF) + StockTransaction sum + cash". Note: ELSS is **already part of** MF sum (since ELSS rows live in MfTransaction), so we sum once (mf_corpus includes ELSS) and add stocks + cash — no double-count.
  - C.6 example body has `₹{amount}`; the rationale entry includes the literal `₹` prefix so the substitution produces `₹2,50,000` (Indian formatting via `format_inr_indian`).
- ✅ Memory rule honoured — plan file should be moved to `docs/superpowers/plans/` but **never** `git add`-ed.

---

## Done criteria

- All 12 tasks' checkboxes ticked.
- All commits land on `Amoul_pre_dep` (or the active working branch).
- Task 12 verification: `app/services/ai_bridge/rebalancing/tests/` green, lint clean, types clean (or unchanged-from-baseline).
- New engine contract:
  - `RebalancingComputeRequest.practical_allocation_input: PracticalAllocationInput` (top-level `total_corpus` scalar removed; `@property` kept for back-compat).
  - `RebalancingComputeResponse.practical_allocation: PracticalAllocationOutput`.
  - `TradeAction.action` accepts `SELL_DIRECT_STOCKS`; `isin`/`sub_category`/`recommended_fund` Optional.
  - `step6.subgroups` contains frozen entries `tax_efficient_equities` (when `elss_corpus > 0`) and `non_mf_equities` (when input or actual > 0).
  - `step6.trade_list` contains exactly one `SELL_DIRECT_STOCKS` when `excess_direct_stocks_inr > 0`, else zero.
- `Rebalancing/CLAUDE.md` declares the upstream dependency on `practical_asset_allocation`.
- Plan file (`/tmp/2026-05-23-allocation-rebalancing-v2-part-c-rebalancing-plan.md`) **not** committed.
