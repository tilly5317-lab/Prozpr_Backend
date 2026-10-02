# Lump sum goals · stocks removal · trade threshold — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** make three changes:
- A lump sum funds the short-term goals' full remaining need before going long-term.
- Direct stocks are invisible to every allocation, rebalancing and additional-investment engine.
- A rebalancing fund row trades only when its change is at least min(1% of the portfolio, 50% of the fund).

**Architecture:**
- **Lump sum:** one argument changes on the lump-sum practical-allocation (PAA) run: `monthly_sip=0.0`.
- **Stocks:** removed in three cuts, each green on its own:
  1. sources: no corpus includes stocks;
  2. consumers: nothing reads the stock outputs;
  3. producer: the PAA engine stops computing them.

  The dead code they leave is then deleted: `mf_corpus`, duplicated sets and rules, and optional trade fields.
- **Threshold:** one helper in `Rebalancing/config.py`, used by step 2 and step 2b.

**Tech Stack:** Python 3.12, pydantic v2 and pytest (`asyncio_mode=auto`). Engines live in `AI_Agents/src` and are loaded via `pythonpath`. The app runs on FastAPI.

**Spec:** `docs/superpowers/specs/2026-10-02-lumpsum-goals-stocks-removal-trade-threshold-design.md`. It was audited twice: the design on 2026-10-02, and this plan for dead code and simplicity on the same day.

## Global Constraints

- **Test command:** `.venv-mac/bin/python -m pytest -q -p no:cacheprovider <paths>`. A bare `pytest` runs zero tests.
- **No commits.** Amoul commits on request. Each task ends at a checkpoint with its work left in the working tree.
- **CRLF files:** many `app/` files use CRLF line endings. Edit them with the Edit tool, or with `perl -pi` for single-line substitutions. Never rewrite them with Python text-mode scripts.
- **Exhaustive sweeps:** the shell `grep` is ugrep and skips gitignored folders. Use `/usr/bin/grep -rn` for any sweep that must reach `Testing/`, `Master_testing/` or `AI_Agents/lifecycle_sim_testing/`.
- **Comments:** few. Keep only what would cause a bug if unknown. No dates, no change history.
- **Out of scope:**
  - Reference docs: do not touch `AI_Agents/Reference_docs/`.
  - Gitignored harness files: never delete one. They are not in git.
- **Version bumps:**
  - Rebalancing `ENGINE_VERSION`: `1.14.0` → `1.15.0`. Task 1 adds the note and Task 4 extends it.
  - `AINV_ENGINE_VERSION`: `ainv-3.5.0` → `ainv-3.6.0`. Task 2 adds the note and Task 3 extends it.
- **Threshold knobs:**
  - `REBALANCE_MIN_CHANGE_PORTFOLIO_PCT = 0.01` (env `REBAL_MIN_CHANGE_PORTFOLIO_PCT`)
  - `REBALANCE_MIN_CHANGE_FUND_PCT = 0.50` (env `REBAL_MIN_CHANGE_FUND_PCT`)
- **Database:** no migration. The `practical_asset_allocation_runs` columns `non_mf_equity_input`, `non_mf_equity_actual`, `excess_direct_stocks`, `max_non_mf_equity_pct_computed` and `mf_corpus` stay. Nothing writes them any more; they are NOT NULL with default 0.
- **The drift regex** `non_mf_equit|excess_direct_stocks|SELL_DIRECT_STOCKS` must not appear in runtime code. That includes new comments and version notes.

```bash
PYT=".venv-mac/bin/python -m pytest -q -p no:cacheprovider"
SCRATCH=/private/tmp/claude-502/-Users-Amoul-Documents-AILAX-AI-Financial-advisor-ailax-Prozpr-Backend/992de43d-bad8-4496-9d96-cf09926894dd/scratchpad
```

---

### Task 0: Baseline

- [ ] **Step 1: Run the full suite before any change**

```bash
.venv-mac/bin/python -m pytest -q -rfE --continue-on-collection-errors -p no:cacheprovider > $SCRATCH/baseline_full.txt 2>&1; tail -3 $SCRATCH/baseline_full.txt
/usr/bin/grep -E "^(FAILED|ERROR) " $SCRATCH/baseline_full.txt | sed 's/ - .*//' | sort > $SCRATCH/baseline_ids.txt; wc -l < $SCRATCH/baseline_ids.txt
```

Expected: about 12 failed, 1989 passed and 10 errors, in about 1 minute. Every later task compares against these node ids, not against the counts.

---

### Task 1: Trade threshold = min(1% of portfolio, 50% of fund)

**Files:**
- Create: `AI_Agents/tests/test_min_change_threshold.py`
- Modify: `AI_Agents/tests/test_debt_netting_keeps_held_debt.py` (append two tests)
- Modify: `AI_Agents/src/Rebalancing/config.py` (line 31, and the version block ending at 146)
- Modify: `AI_Agents/src/Rebalancing/steps/step2_compare_and_decide.py` (lines 18-20, 34-50)
- Modify: `AI_Agents/src/Rebalancing/steps/step2b_suppress_debt_switch.py` (lines 28-32, 111, 180-183)
- Modify: `AI_Agents/src/Rebalancing/models.py:238`
- Modify: `AI_Agents/src/Rebalancing/steps/step6_presentation.py` (lines 29, 60)
- Modify (fixtures):
  - `app/domains/rebalancing/services/rebal_engine/tests/conftest.py:601`
  - `test_service.py:62,131,293`
  - `test_formatter.py:166`

**Interfaces:**
- Produces:
  - `Rebalancing.config.min_change_threshold(scale: Decimal, corpus: Decimal) -> Decimal`
  - `KnobSnapshot.rebalance_min_change_portfolio_pct: Optional[float]`
  - `KnobSnapshot.rebalance_min_change_fund_pct: Optional[float]`
- Removes: `REBALANCE_MIN_CHANGE_PCT` and `KnobSnapshot.rebalance_min_change_pct`.

- [ ] **Step 1: Write the failing tests**

Create `AI_Agents/tests/test_min_change_threshold.py`:

```python
"""A fund row trades only when |target − present| reaches min(1% of the
portfolio, 50% of max(target, present)), or when it is flagged for exit."""

from __future__ import annotations

from decimal import Decimal

from practical_asset_allocation.pipeline import PracticalAllocationInput
from Rebalancing.config import EXIT_FLOOR_RATING
from Rebalancing.models import FundRowInput, KnobSnapshot, RebalancingComputeRequest
from Rebalancing.steps import step1_cap_and_spill, step2_compare_and_decide
from Rebalancing.steps.step6_presentation import _build_knob_snapshot

CORPUS = Decimal("10000000")  # ₹1 crore


def _row(isin, subgroup, target, present, rating=8):
    return FundRowInput(
        asset_subgroup=subgroup, sub_category="Large Cap Fund",
        recommended_fund=f"Fund {isin}", isin=isin, rank=1,
        target_amount_pre_cap=Decimal(target), present_allocation_inr=Decimal(present),
        invested_cost_inr=Decimal(present) * Decimal("0.85"),
        lt_value_inr=Decimal(present), lt_cost_inr=Decimal(present) * Decimal("0.85"),
        current_nav=Decimal("100"), fund_rating=rating,
    )


def test_step2_trades_only_past_the_lower_of_the_two_bars():
    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, shortfall_amount=0.0,
        total_corpus=float(CORPUS), monthly_household_expense=100_000,
        effective_tax_rate=15.0, goals=[], mf_corpus=float(CORPUS),
    )
    req = RebalancingComputeRequest(
        practical_allocation_input=inp, tax_regime="new", effective_tax_rate_pct=30.0,
        rows=[
            _row("BIG", "low_beta_equities", "2850000", "3000000"),    # -1.5L vs bar 1L
            _row("MID", "medium_beta_equities", "560000", "500000"),   # +60k vs bar 1L
            _row("SMALL", "high_beta_equities", "130000", "100000"),   # +30k vs bar 65k
            _row("NEW", "value_equities", "200000", "0"),
            _row("OUT", "us_equities", "0", "400000"),
            _row("BAD", "sector_equities", "50000", "50000", rating=EXIT_FLOOR_RATING - 1),
        ],
    )
    s1, _, _ = step1_cap_and_spill.apply(req.rows, req)
    s2, _ = step2_compare_and_decide.apply(s1, req)
    worth = {r.isin: r.worth_to_change for r in s2}
    assert worth == {
        "BIG": True, "MID": False, "SMALL": False, "NEW": True, "OUT": True, "BAD": True,
    }


def test_the_run_snapshot_records_both_knobs():
    snap = _build_knob_snapshot()
    assert snap.rebalance_min_change_portfolio_pct == 0.01
    assert snap.rebalance_min_change_fund_pct == 0.50


def test_a_snapshot_saved_before_these_knobs_still_loads():
    old = _build_knob_snapshot().model_dump(mode="json")
    old.pop("rebalance_min_change_portfolio_pct")
    old.pop("rebalance_min_change_fund_pct")
    old["rebalance_min_change_pct"] = 0.10
    snap = KnobSnapshot.model_validate(old)
    assert snap.rebalance_min_change_portfolio_pct is None
    assert snap.rebalance_min_change_fund_pct is None
```

Append to `AI_Agents/tests/test_debt_netting_keeps_held_debt.py`. It reuses that file's `_row` and `_step2b`; its `CORPUS` is ₹1Cr.

```python
def test_a_netted_residual_above_one_pct_of_the_portfolio_still_trades():
    by_isin = _step2b([
        _row("LIQ", "near_debt", 0, "0", "3000000", is_recommended=False),
        _row("ARB", "arbitrage", 1, "2850000", "0", sub_category="Arbitrage Fund"),
    ])
    assert by_isin["LIQ"].diff == Decimal("-150000")
    assert by_isin["LIQ"].worth_to_change is True


def test_a_netted_residual_below_the_bar_is_absorbed():
    by_isin = _step2b([
        _row("LIQ", "near_debt", 0, "0", "3000000", is_recommended=False),
        _row("ARB", "arbitrage", 1, "2950000", "0", sub_category="Arbitrage Fund"),
    ])
    assert by_isin["LIQ"].diff == Decimal(0)
    assert by_isin["LIQ"].worth_to_change is False
```

- [ ] **Step 2: Run, and confirm the tests fail**

Run: `$PYT AI_Agents/tests/test_min_change_threshold.py AI_Agents/tests/test_debt_netting_keeps_held_debt.py`
Expected:
- these FAIL:
  - `test_step2_trades_only_past_the_lower_of_the_two_bars` (BIG False, MID True, SMALL True under 10%);
  - both snapshot tests (no such field);
  - `test_a_netted_residual_above_one_pct…`, because the residual is absorbed at 10%;
- `…below_the_bar_is_absorbed` PASSES. It guards against a regression.

- [ ] **Step 3: `config.py` — two knobs and the helper**

Replace line 31 (`REBALANCE_MIN_CHANGE_PCT…`) with:

```python
# A fund row trades only when |target − present| reaches the LOWER of: a share of
# the whole portfolio, or a share of the fund itself (max(target, present)).
REBALANCE_MIN_CHANGE_PORTFOLIO_PCT: float = float(
    os.getenv("REBAL_MIN_CHANGE_PORTFOLIO_PCT", "0.01")
)
REBALANCE_MIN_CHANGE_FUND_PCT: float = float(os.getenv("REBAL_MIN_CHANGE_FUND_PCT", "0.50"))


def min_change_threshold(scale: Decimal, corpus: Decimal) -> Decimal:
    """Smallest |diff| worth trading for a fund of size `scale` in a `corpus` portfolio."""
    return min(
        corpus * Decimal(str(REBALANCE_MIN_CHANGE_PORTFOLIO_PCT)),
        scale * Decimal(str(REBALANCE_MIN_CHANGE_FUND_PCT)),
    )
```

At the end of the version comment block, add the following and set `ENGINE_VERSION: str = "1.15.0"`:

```python
# 1.15.0: a fund row trades only when its change reaches min(1% of the portfolio,
#         50% of max(target, present)).
```

- [ ] **Step 4: Step 2**

In `step2_compare_and_decide.py`:
- Delete `from decimal import Decimal`. It is no longer used, and ruff F401 would flag it.
- Change the config import to `from ..config import EXIT_FLOOR_RATING, FORCE_EXIT_RANK, min_change_threshold`.
- In `apply`, delete `_ = request  # …` and `threshold_factor = …`. Add `corpus = request.total_corpus` before the loop.
- Replace the threshold lines with:

```python
        scale = max(r.final_target_amount, r.present_allocation_inr)
        worth_to_change = (abs(diff) >= min_change_threshold(scale, corpus)) or exit_flag
```

- [ ] **Step 5: Step 2b**

In `step2b_suppress_debt_switch.py`:
- In the `from ..config import (…)` block, replace `REBALANCE_MIN_CHANGE_PCT,` with `min_change_threshold,`.
- Delete `threshold_factor = Decimal(str(REBALANCE_MIN_CHANGE_PCT))`.
- Replace the re-gate with:

```python
        worth_to_change = r.exit_flag or (
            diff != 0 and abs(diff) >= min_change_threshold(scale, corpus)
        )
```

- [ ] **Step 6: `KnobSnapshot` and step 6**

In `models.py`, replace `rebalance_min_change_pct: float` with:

```python
    # None = the snapshot predates these knobs (it ran at 10% of the fund).
    rebalance_min_change_portfolio_pct: Optional[float] = None
    rebalance_min_change_fund_pct: Optional[float] = None
```

In `step6_presentation.py`:
- Import `REBALANCE_MIN_CHANGE_FUND_PCT` and `REBALANCE_MIN_CHANGE_PORTFOLIO_PCT` instead of `REBALANCE_MIN_CHANGE_PCT`.
- In `_build_knob_snapshot`:

```python
        rebalance_min_change_portfolio_pct=REBALANCE_MIN_CHANGE_PORTFOLIO_PCT,
        rebalance_min_change_fund_pct=REBALANCE_MIN_CHANGE_FUND_PCT,
```

- [ ] **Step 7: App fixtures (CRLF-safe)**

```bash
perl -pi -e 's/rebalance_min_change_pct=0\.10,/rebalance_min_change_portfolio_pct=0.01, rebalance_min_change_fund_pct=0.50,/' app/domains/rebalancing/services/rebal_engine/tests/conftest.py app/domains/rebalancing/services/rebal_engine/tests/test_service.py app/domains/rebalancing/services/rebal_engine/tests/test_formatter.py
/usr/bin/grep -rn "REBALANCE_MIN_CHANGE_PCT\|rebalance_min_change_pct" app AI_Agents/src AI_Agents/tests --include="*.py" | grep -v "/Testing/\|Master_testing"
```

Expected: the grep prints only the `old["rebalance_min_change_pct"] = 0.10` line in the new test.

- [ ] **Step 8: Run, and confirm everything passes**

Run: `$PYT AI_Agents/tests/test_min_change_threshold.py AI_Agents/tests/test_debt_netting_keeps_held_debt.py AI_Agents/tests/test_rebalancing_practical_targets.py app/domains/rebalancing`
Expected: all pass.

- [ ] **Step 9: Checkpoint.** Do not commit.

---

### Task 2: A lump sum funds the short-term goals' full remaining need

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (line 252; version block ending at 132)
- Modify: `AI_Agents/src/asset_allocation_pydantic/steps/step2_short_term.py:128-133`
- Test: `AI_Agents/tests/test_goal_waterfall_step2.py` (append)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_service.py::test_lumpsum_uses_long_term_holdings_and_facts_count_goal_money`

**Interfaces:**
- Unchanged: `goal_waterfall` and `goal_share_for`.
- New behaviour: the lump-sum PAA run receives `monthly_sip=0.0`.

**Coverage note:** the spec's lump-sum examples need no new end-to-end test.
- The engine already funds the full gap when the SIP is 0: `test_goal_waterfall.py::test_no_sip_carves_the_gap_after_holdings` and the tests at `:54, :82`.
- The lump-sum share is `from_corpus` capped at the deploy amount: `ainv test_input_builder.py:213` and `test_ainv_goal_share.py:49-63`.
- The only new behaviour is the argument, and Step 1 pins it.

- [ ] **Step 1: Write the failing tests**

Append to `AI_Agents/tests/test_goal_waterfall_step2.py` (it reuses `_inp`):

```python
def test_shortfall_message_does_not_assume_a_monthly_investment():
    car = Goal(goal_name="Car", time_to_goal_months=12, amount_needed=600_000,
               goal_priority="non_negotiable", amount_needed_fv=600_000)
    out = step2_short_term.run(_inp([car], total_corpus=100_000.0), remaining_corpus=100_000)
    assert "monthly investment" not in out.future_investment.message
    assert "investing more each month" in out.future_investment.message
```

In `test_service.py::test_lumpsum_uses_long_term_holdings_and_facts_count_goal_money`, replace `assert paa.call_args.kwargs["monthly_sip"] is None` with:

```python
    assert paa.call_args.kwargs["monthly_sip"] == 0.0
```

- [ ] **Step 2: Run, and confirm both fail**

Run: `$PYT AI_Agents/tests/test_goal_waterfall_step2.py app/domains/additional_investment/services/ainv_engine/tests/test_service.py -k "monthly_investment or facts_count_goal_money"`
Expected: both FAIL.

- [ ] **Step 3: Pass `0.0` for a lump sum**

`ainv_engine/service.py:252`:

```python
        monthly_sip=deploy_amount_inr if cadence is Cadence.SIP_MONTHLY else 0.0,
```

Add the following above `AINV_ENGINE_VERSION`, and set `AINV_ENGINE_VERSION = "ainv-3.6.0"`:

```python
# 3.6.0: a lumpsum funds the short-term goals' full remaining need first — its
# practical run assumes no future SIP.
```

- [ ] **Step 4: Make the shortfall message independent of the SIP**

In `step2_short_term.py`, replace the `msg = (…)` assignment with:

```python
        msg = (
            "Your short-term goals won't be fully funded by their dates — the gap is "
            f"{format_inr_indian(funding.shortfall)}. You can close it by investing more "
            "each month or by reducing some of your goals."
        )
```

- [ ] **Step 5: Run, and confirm everything passes**

Run: `$PYT AI_Agents/tests/test_goal_waterfall.py AI_Agents/tests/test_goal_waterfall_step2.py AI_Agents/tests/test_human_override_golden.py app/domains/additional_investment app/domains/asset_allocation`
Expected: all pass. The goldens are unchanged because they have no goals.

If an `asset_allocation` test pins the old message text, update it to the new sentence.

- [ ] **Step 6: Checkpoint.** Do not commit.

---

### Task 3: Stocks leave every corpus source

**Files:**
- Modify: `app/domains/portfolio/services/holdings_snapshot.py`
- Modify: `app/domains/portfolio/services/allocation_rollup.py` (lines 21, 40)
- Modify: `app/domains/asset_allocation/services/aa_engine/input_builder.py::pick_total_corpus` (lines 69-80, plus imports)
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (lumpsum `CorpusPin` near lines 228-242, plus the version note)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_holdings_snapshot.py`
- Test: `app/domains/asset_allocation/services/aa_engine/tests/test_input_builder.py` (append)
- Test: `app/domains/additional_investment/services/ainv_engine/tests/test_service.py::test_lumpsum_pins_corpus_and_passes_map`

**Interfaces:**
- Produces: the public `holdings_snapshot.EQUITY_INSTRUMENT_TYPES`, imported by `allocation_rollup`.
- `HoldingsSnapshot` loses `non_mf_equity_inr`, and `by_subgroup` never has a `non_mf_equities` key.
- Until Task 5, the lumpsum `CorpusPin` passes `non_mf_equity_corpus=0.0`, and `mf_corpus` is the same expression as `total_corpus`.

- [ ] **Step 1: Write the failing tests**

In `test_holdings_snapshot.py`, replace `test_direct_stocks_bucket_to_non_mf_equities_not_unknown` with:

```python
def test_direct_stocks_are_left_out_of_the_snapshot():
    rows = [
        ("equity", 200000.0, None, "RELIANCE"),
        ("stock", 50000.0, None, "TCS"),
        ("mutual_fund", 100000.0, "Large Cap Fund", "Alpha Large Cap"),
    ]
    snap = aggregate_holdings(rows)
    assert snap.by_subgroup == {"low_beta_equities": pytest.approx(100000.0)}
    assert snap.unknown_inr == 0.0
    assert snap.total_inr == pytest.approx(100000.0)
```

Append these to the end of `aa_engine/tests/test_input_builder.py` as plain pytest functions. The module mixes styles, and pytest collects both.

```python
def _holding(itype, value, sub_category=None, name="X"):
    md = SimpleNamespace(sub_category=sub_category, scheme_name=name) if sub_category else None
    return SimpleNamespace(
        instrument_type=itype, current_value=value, instrument_name=name, fund_metadata=md
    )


def _pick(total_value, holdings):
    from app.domains.asset_allocation.services.aa_engine.input_builder import pick_total_corpus

    primary = SimpleNamespace(is_primary=True, total_value=total_value, holdings=holdings)
    return pick_total_corpus(
        SimpleNamespace(financial_assets=0.0), SimpleNamespace(portfolio_value=0.0), [primary]
    )


_MF = _holding("mutual_fund", 1_000_000.0, "Large Cap Fund", "Alpha Large Cap")
_STOCK = _holding("equity", 500_000.0, name="RELIANCE")


def test_direct_stocks_in_the_portfolio_total_are_left_out():
    assert _pick(1_500_000.0, [_MF, _STOCK]) == 1_000_000.0


def test_a_stock_row_left_behind_by_a_cams_upload_does_not_lower_the_corpus():
    # CAMS rewrites total_value as MF-only but keeps the bank-sync stock row.
    assert _pick(1_000_000.0, [_MF, _STOCK]) == 1_000_000.0


def test_a_portfolio_with_no_holdings_falls_back_to_its_total():
    assert _pick(800_000.0, []) == 800_000.0
```

In `test_service.py::test_lumpsum_pins_corpus_and_passes_map`:
- Delete the `"non_mf_equities": 30000.0,` snapshot entry, and change the trailing comment to `# total 470k`.
- Replace the four `pin` assertions with:

```python
    assert pin.total_corpus == pytest.approx(970000.0)        # 470k + 5L
    assert pin.elss_corpus == pytest.approx(50000.0)
```

- [ ] **Step 2: Run, and confirm the stock tests fail**

Run: `$PYT app/domains/additional_investment/services/ainv_engine/tests/test_holdings_snapshot.py app/domains/asset_allocation/services/aa_engine/tests/test_input_builder.py`
Expected:
- both `…left_out…` tests FAIL;
- the CAMS and no-holdings tests PASS. They guard against regressions.

- [ ] **Step 3: The snapshot skips direct-equity rows**

In `holdings_snapshot.py`:
- Delete `_SUBGROUP_NON_MF_EQUITIES` and the `non_mf_equity_inr` property.
- Replace the type set with:

```python
# Direct-equity instrument types. No engine corpus, target or trade includes
# these holdings; allocation_rollup still shows them as Equity.
EQUITY_INSTRUMENT_TYPES = frozenset({"equity", "stock", "share"})
```

- In `aggregate_holdings`, replace the `if … in _EQUITY_INSTRUMENT_TYPES: key = … else:` branch with:

```python
        if (instrument_type or "").strip().lower() in EQUITY_INSTRUMENT_TYPES:
            continue
        _asset_class, key = classify_holding(sub_category, scheme_name)
```

- Docstrings:
  - `HoldingsSnapshot`: "(frozen subgroups included)" becomes "(ELSS included; direct stocks never)".
  - `aggregate_holdings`: "Direct-stock rows … bucket to non_mf_equities WITHOUT classification" becomes "Direct-stock rows are skipped".

In `allocation_rollup.py`:
- Delete `_DIRECT_EQUITY_ITYPES`.
- Import the set with `from app.domains.portfolio.services.holdings_snapshot import EQUITY_INSTRUMENT_TYPES`.
- Line 40 uses `EQUITY_INSTRUMENT_TYPES`.

- [ ] **Step 4: `pick_total_corpus` uses the stock-free holdings sum**

In `aa_engine/input_builder.py`, add `from app.domains.portfolio.services.holdings_snapshot import snapshot_from_holdings` to the `app.domains` imports. Then replace the `if portfolios:` body with:

```python
    if portfolios:
        primary = next(
            (p for p in portfolios if getattr(p, "is_primary", False)),
            portfolios[0],
        )
        holdings = list(getattr(primary, "holdings", None) or [])
        # total_value can include bank-sync demat stocks, which no engine corpus
        # holds; a CAMS upload rewrites it but leaves those stock rows behind.
        primary_value = (
            snapshot_from_holdings(holdings).total_inr
            if holdings
            else _f(primary, "total_value")
        )
```

- [ ] **Step 5: The lumpsum `CorpusPin` drops stocks**

In `ainv_engine/service.py`, replace the lumpsum `CorpusPin(…)` and its `trace_line` with:

```python
        corpus_pin = CorpusPin(
            total_corpus=snapshot.total_inr + deploy_amount_inr,
            mf_corpus=snapshot.total_inr + deploy_amount_inr,
            non_mf_equity_corpus=0.0,
            elss_corpus=snapshot.elss_inr,
        )
        trace_line(
            f"additional_investment holdings snapshot: total={snapshot.total_inr}, "
            f"elss={snapshot.elss_inr}, unknown={snapshot.unknown_inr}"
        )
```

Extend the 3.6.0 note with `# Direct stocks are not part of any corpus.`

- [ ] **Step 6: Run, and confirm everything passes**

Run: `$PYT app/domains/additional_investment app/domains/asset_allocation app/domains/practical_asset_allocation app/domains/portfolio`
Expected: all pass.

- [ ] **Step 7: Checkpoint.** Do not commit.

---

### Task 4: Nothing downstream reads stock outputs

**Files:**
- Modify: `AI_Agents/src/Rebalancing/config.py` (lines 32-43, plus the 1.15.0 note)
- Modify: `AI_Agents/src/Rebalancing/pipeline.py`:
  - lines 54-64 (`_funds_per_subgroup`)
  - lines 186-188 (docstring)
  - lines 271-305 (`run_rebalancing`)
- Modify: `AI_Agents/src/Rebalancing/steps/step4_initial_trades_under_stcg_cap.py` (lines 271-323)
- Modify: `AI_Agents/src/Rebalancing/steps/step6_presentation.py`:
  - lines 82-151 (`_frozen_subgroups`, `_sell_direct_stocks_action`)
  - lines 367-369
- Modify: `AI_Agents/src/Rebalancing/models.py`:
  - line 156 (comment)
  - lines 269-281 (`TradeAction`)
  - lines 297-302 (docstring)
  - line 326 (comment)
- Modify: `AI_Agents/src/Rebalancing/rationales.py` (lines 65-74)
- Modify: `AI_Agents/src/additional_investment/pipeline.py` (lines 14, 20-28, 80)
- Modify: `AI_Agents/src/additional_investment/models.py` (lines 64-65, 81-83: comments)
- Modify: `app/domains/rebalancing/services/rebal_engine/service.py` (lines 311-315, 670-683)
- Modify: `app/domains/rebalancing/services/rebal_engine/chat.py:513`
- Modify: `app/domains/rebalancing/services/rebal_engine/input_builder.py:427-432` (comment)
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (lines 266-276)
- Modify: `app/domains/additional_investment/services/ainv_engine/input_builder.py:117-118` (comment)
- Modify: `app/domains/additional_investment/services/lumpsum_reasoning.py:39`
- Delete: `app/domains/rebalancing/services/rebal_engine/tests/test_facts_pack_direct_stock.py`
- Test:
  - `ainv_engine/tests/test_service.py:163-169`
  - `app/domains/additional_investment/tests/test_preference_chat_ainv.py:74`
  - new `AI_Agents/tests/test_funds_per_subgroup.py`

**Interfaces:**
- Produces:
  - `Rebalancing.config.funds_per_subgroup(corpus: Decimal | float) -> int`, the ONE fund-count rule, used by Rebalancing and additional_investment;
  - `step4.apply(rows, request)`, with no `extra_cash_inr`;
  - `TradeAction.action: Literal["BUY", "SELL", "EXIT"]`, with `isin`, `sub_category` and `recommended_fund` as required `str`.
- The frozen subgroup sets are NOT touched here. Task 5 shrinks and unifies them.

- [ ] **Step 1: Write the failing test**

Create `AI_Agents/tests/test_funds_per_subgroup.py`:

```python
"""One fund-count rule for rebalancing and additional investment: two funds per
subgroup at or above the line, one below it — on the whole corpus."""

from decimal import Decimal

import pytest

from Rebalancing.config import funds_per_subgroup


@pytest.mark.parametrize("corpus, n", [(Decimal("4999999"), 1), (Decimal("5000000"), 2), (5_000_000.0, 2)])
def test_two_funds_at_or_above_fifty_lakh(corpus, n):
    assert funds_per_subgroup(corpus) == n
```

- [ ] **Step 2: Run, and confirm it fails**

Run: `$PYT AI_Agents/tests/test_funds_per_subgroup.py`
Expected: ERROR, `ImportError: cannot import name 'funds_per_subgroup'`.

- [ ] **Step 3: One fund-count rule in `config.py`**

Replace the comment block above `SUBGROUP_FUND_COUNT_THRESHOLD_INR` (the part from "Measured on …" through "…every sim profile.") with nothing. Keep the first two lines and the "One knob, not three" lines. Below the constant, add:

```python
def funds_per_subgroup(corpus: Decimal | float) -> int:
    """Funds sharing one subgroup's deployable money: two at or above the line, else one."""
    return 2 if Decimal(str(corpus)) >= SUBGROUP_FUND_COUNT_THRESHOLD_INR else 1
```

Extend the 1.15.0 note. It must not name the removed action:

```python
#         Direct stocks are not an engine input.
```

In `Rebalancing/pipeline.py`:
- Delete `_funds_per_subgroup`.
- Import `funds_per_subgroup` from `.config`.
- In `run_rebalancing`, use `n_funds = funds_per_subgroup(request.total_corpus)`.

In `additional_investment/pipeline.py`:
- Delete the local `_funds_per_subgroup`.
- Replace the line-14 import of `SUBGROUP_FUND_COUNT_THRESHOLD_INR` with `funds_per_subgroup`.
- At line 80, call `funds_per_subgroup(inp.investable_corpus_inr)`.
- If the gitignored `additional_investment/Testing` imports `_funds_per_subgroup`, Task 7 fixes it.

- [ ] **Step 4: Rebalancing stops reading stock outputs**

In `Rebalancing/pipeline.py::run_rebalancing`:
- The `# 1.` comment becomes `# 1. Practical allocation (holdings-aware; consumes the ELSS scalar).`
- Delete the 3-line "Direct-stock proceeds…" comment.
- The step 4 call becomes:

```python
    s4_rows, s4_warnings = step4_initial_trades_under_stcg_cap.apply(s3_rows, request)
```

- In the `_assign_subgroup_targets` docstring (line 188), "Rows for frozen subgroups (ELSS, non-MF equity)" becomes "Rows for the frozen ELSS subgroup".

In `step4_initial_trades_under_stcg_cap.py::apply`:
- Delete the `extra_cash_inr` parameter and the whole docstring.
- `remaining_buy_demand = max(target_buy - forced_sold_total, Decimal(0))`.
- `available_cash = total_sold_final`.

In `step6_presentation.py`, replace `_frozen_subgroups` with:

```python
def _frozen_subgroups(practical: PracticalAllocationOutput) -> list[SubgroupSummary]:
    """ELSS is held but never traded per fund; surface it as a frozen entry."""
    elss = Decimal(str(practical.corpus_breakdown.elss_corpus_inr))
    if elss <= 0:
        return []
    return [
        SubgroupSummary(
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
        )
    ]
```

Also in `step6_presentation.py`:
- Delete `_sell_direct_stocks_action`.
- Delete the `sds = …` and `if sds is not None: trade_list.append(sds)` lines.
- Keep `get_rationale`; line 198 still uses it.

In `models.py`:
- `TradeAction` becomes:

```python
class TradeAction(BaseModel):
    isin: str
    asset_subgroup: str
    sub_category: str
    recommended_fund: str
    action: Literal["BUY", "SELL", "EXIT"]
    amount_inr: Decimal
    reason_code: str  # machine — stable, analytics
    reason_title: str  # customer card header
    reason_text: str  # customer card body, one sentence
    # Per-fund rationale from the ranking CSV. BUY or SELL-trim of a
    # recommended fund → selection_reason; EXIT of a BAD/off-list fund →
    # joined rejection reasons. None when no fund-specific reason exists.
    fund_reason: Optional[str] = None
```

- The `SubgroupSummary` docstring's frozen paragraph becomes:

```
    **Frozen subgroup** (`tax_efficient_equities`): step6 emits it with
    `actions = []` because ELSS has no MF rows in the engine — its amount comes
    straight from `practical_allocation.corpus_breakdown`.
```

- Line 156 becomes `# The corpus scalars (total / ELSS) and all profile/goal/market-view fields ride on this nested input.`
- Line 326 becomes `# ``corpus_breakdown`` block surfacing the ELSS numbers.`

In `rationales.py`, delete the `"sell_excess_direct_stocks": {…},` entry.

- [ ] **Step 5: The rebalancing chat stops carrying a stock sale**

`rebal_engine/service.py`:
- Delete the four shape-doc lines (`# Present only when the NFA band…` through `"direct_stock_sale_indian": <str>,`).
- Delete the block from `# Direct-stock proceeds.` through `pack["direct_stock_sale_indian"] = …`.

`rebal_engine/chat.py:513` (Edit tool): `so Net change is "—" / ₹0 unless direct_stock_sale is present)` becomes `so Net change is "—" / ₹0)`.

`rebal_engine/input_builder.py:432` (Edit tool): the last sentence of comment 7b becomes `ELSS defaults to 0 — no holdings breakdown wired yet.`

Delete `rebal_engine/tests/test_facts_pack_direct_stock.py`. The behaviour it tests no longer exists, and Task 5's drift test guards against its return.

- [ ] **Step 6: Additional investment stops subtracting stocks**

In `ainv_engine/service.py`, replace the comment and expression at lines 266-276 with:

```python
    # Investable corpus that decides 1 vs 2 funds per subgroup, off the practical
    # result already computed for this cadence (lumpsum's ran on corpus + deploy).
    # The sized SIP below rebuilds on a NOTIONAL corpus, so capture the real figure
    # here — never re-read it off the notional-sized result.
    investable_corpus_inr = float(paa_outcome.result.corpus_breakdown.total_corpus_inr)
```

If `cb` has no other use after this, it is now gone; check with the grep below.

- In `ainv_engine/input_builder.py:117-118`, the comment becomes `# 1 vs 2 funds per subgroup by corpus; pre-computed by the caller.`
- In `lumpsum_reasoning.py`, delete `"non_mf_equities": "direct stocks",`.
- In the engine's `additional_investment/models.py`:
  - the `investable_corpus_inr` comment becomes `# Investable corpus at deploy time (= total_corpus), pre-computed by the caller; decides 1 vs 2 funds per subgroup. 0 → 1.`;
  - the `exclude_subgroups` example becomes `# e.g. tax_efficient_equities (ELSS lock-in).`

Test edits:
- `test_service.py`: `_fake_corpus_breakdown(total_corpus_inr=1_000_000)` returns `SimpleNamespace(total_corpus_inr=total_corpus_inr)`. Its docstring becomes "Minimal corpus_breakdown stand-in — the service reads total_corpus_inr."
- `test_preference_chat_ainv.py:74`: drop `non_mf_equity_input_inr=0`.

```bash
/usr/bin/grep -n "\bcb\b\|_funds_per_subgroup\|extra_cash\|direct_stock" app/domains/additional_investment/services/ainv_engine/service.py AI_Agents/src/Rebalancing/*.py AI_Agents/src/Rebalancing/steps/*.py AI_Agents/src/additional_investment/*.py app/domains/rebalancing/services/rebal_engine/*.py
```

Expected: no hits, other than an unrelated `cb` in a different function, if one exists.

- [ ] **Step 7: Run, and confirm everything passes**

Run: `$PYT AI_Agents/tests app/domains/rebalancing app/domains/additional_investment app/domains/profile`
Expected: all pass, apart from node ids already in `$SCRATCH/baseline_ids.txt`.

- [ ] **Step 8: Checkpoint.** Do not commit.

---

### Task 5: The practical engine stops computing stocks; one frozen set

**Files:**
- Create: `AI_Agents/tests/test_no_direct_stock_machinery.py`
- Modify: `AI_Agents/src/practical_asset_allocation/pipeline.py` (all stock sites; listed in Step 3)
- Modify: `AI_Agents/src/practical_asset_allocation/__init__.py:4`
- Modify: `AI_Agents/src/practical_asset_allocation/human_override.py`:
  - lines 22-35
  - line 120 (docstring)
  - lines 215-228 (copy)
- Modify: `AI_Agents/src/practical_asset_allocation/allocation_snap.py`:
  - lines 15-16 (docstring)
  - lines 36-39
- Modify: `AI_Agents/src/Rebalancing/pipeline.py:43-51` (frozen set → import)
- Modify: `AI_Agents/src/asset_allocation_pydantic/models.py:93`
- Modify (comments only):
  - `AI_Agents/src/asset_allocation_pydantic/equity_subgroup_slider.py` (lines 18-23, 80-88)
  - `AI_Agents/src/asset_allocation_pydantic/steps/step4_long_term.py` (lines 445, 459)
- Modify: `app/domains/asset_allocation/services/aa_engine/input_builder.py:266`
- Modify: `app/domains/practical_asset_allocation/services/paa_engine/input_builder.py`:
  - lines 1-20 (docstring)
  - lines 53-64 (`CorpusPin`)
  - lines 150-160
  - lines 164-171
- Modify: `app/domains/practical_asset_allocation/services/paa_engine/service.py`:
  - lines 83-85
  - lines 120-125
  - lines 156-161
- Modify: `app/domains/practical_asset_allocation/services/practical_allocation_persist_service.py` (lines 64-69)
- Modify: `app/domains/additional_investment/services/ainv_engine/input_builder.py`:
  - lines 5-9 (docstring)
  - lines 34-40
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (both `CorpusPin` calls)
- Modify: `app/domains/profile/services/screen_preference_service.py:46-47` (comment)
- Tests: Step 7 lists the test edits; the forbid sweep is authoritative. Also re-pin `AI_Agents/tests/fixtures/golden_practical_no_pref.json`.

**Interfaces:**
- `PracticalAllocationInput` loses `non_mf_equity_corpus` and `max_non_mf_equity_pct_client_input`.
- `AllocationInput` loses `financial_assets`.
- `CorpusPin` loses `non_mf_equity_corpus`.
- `CorpusBreakdown` loses the 6 stock fields. (Task 6 removes `mf_corpus`.)
- `human_override.FROZEN_SUBGROUPS == frozenset({"tax_efficient_equities"})` is the ONLY frozen set:
  - Rebalancing's pipeline imports it;
  - `allocation_snap` imports it;
  - the app's ainv `_EXCLUDE_SUBGROUPS` is it.

- [ ] **Step 1: Write the drift test (red until this task lands)**

Create `AI_Agents/tests/test_no_direct_stock_machinery.py`:

```python
"""Direct stocks are not an engine input. Fails if the machinery reappears in
runtime code. The practical-run ORM model keeps its legacy columns."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"non_mf_equit|excess_direct_stocks|SELL_DIRECT_STOCKS")
SKIP_DIRS = {"tests", "Testing", "Master_testing", "__pycache__"}
ALLOW = {ROOT / "app/domains/practical_asset_allocation/models/run.py"}


def test_no_runtime_code_mentions_direct_stock_machinery():
    hits = []
    for base in (ROOT / "AI_Agents/src", ROOT / "app"):
        for path in base.rglob("*.py"):
            if path in ALLOW or SKIP_DIRS.intersection(path.relative_to(base).parts):
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if PATTERN.search(line):
                    hits.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)
```

- [ ] **Step 2: Run, and confirm it fails**

Run: `$PYT AI_Agents/tests/test_no_direct_stock_machinery.py`
Expected: FAIL. Every hit listed is work for Steps 3-6.

- [ ] **Step 3: Delete the stock machinery from the practical engine (`pipeline.py`)**

1. Delete the `ASSET_BAND_*` constants, their comment, and `_asset_banded_max_non_mf_equity_pct` (lines 95-117).
2. In `PracticalAllocationInput`:
   - delete `non_mf_equity_corpus` and `max_non_mf_equity_pct_client_input` and their docstrings;
   - the accounting docstring keeps only `rebalancing_corpus = total_corpus - elss_corpus` (plus the `mf_corpus` lines until Task 6).
3. In `CorpusBreakdown`:
   - delete `non_mf_equity_input_inr`, `non_mf_equity_actual_inr`, `excess_direct_stocks_inr`, `max_non_mf_equity_pct_computed`, `lt_equities_amount_inr` and `non_mf_equity_cap_inr` with their docstrings;
   - the class docstring's first sentence becomes "Practical-only block: how the customer's corpus splits, and what the engine deployed."
4. In the `PracticalAllocationOutput.aggregated_subgroups` docstring, the two-extra-rows text becomes "one extra row: 'tax_efficient_equities' (ELSS amount in long_term column)".
5. In `_PracticalLongTermResult`:
   - delete `max_non_mf_equity_pct_computed`, `max_non_mf_equity_pct_considered`, `max_equities_shares`, `non_mf_equity_actual` and `excess_direct_stocks`;
   - `# R177-R186 (Task 7):` becomes `# R177-R181 (Task 7):`.
6. In `_run_practical_long_term`:
   - delete the parameters `non_mf_equity_input`, `financial_assets` and `max_non_mf_equity_pct_client_input`;
   - in the docstring, the Task 7 line becomes `amounts, ELSS, residual_equity.`;
   - replace everything from `# R182-R184:` through the `residual_equity_corpus_pre_multi_asset = max(…)` statement with:

```python
    # Residual equity corpus available for MF subgroups (pre-multi-asset).
    residual_equity_corpus_pre_multi_asset = max(0, equities_amount - elss_amount_frozen)
```

   - the pin-room comment becomes `# Equity pins are funded out of what can actually be BOUGHT: the locked ELSS rupees are already carved off here.`
7. Stale prose:
   - `:400` "The frozen rows (ELSS, direct stock) are ignored — they are HOLDINGS" becomes "The frozen ELSS row is ignored — it is a HOLDING".
   - `:474` "post-ELSS, post-direct-stock equity residual" becomes "post-ELSS equity residual".
   - `:794` "(post-ELSS, post-non-MF)" becomes "(post-ELSS)".
   - `:1121` "(adds two frozen rows)" becomes "(adds the frozen ELSS row)".
8. Slider (around line 925):
   - The comment's "Total = residual_equity_corpus_final + multi_asset_amount + non_mf_equity_actual (ELSS deliberately excluded …)" becomes "Total = residual_equity_corpus_final + multi_asset_amount (ELSS deliberately excluded — frozen and treated separately)".
   - Then:

```python
    total_equity_pool_for_shares = (
        residual_equity_corpus_final + multi_asset_block.multi_asset_amount
    )
```

   - Set `locked_amount=elss_amount_frozen,`.
9. In the `_PracticalLongTermResult(…)` construction, delete the five removed fields.
10. In the orchestrator:
    - the `_run_practical_long_term(…)` call drops the three kwargs;
    - the `_step5_aggregation_with_frozen(…)` call drops `non_mf_equity_actual=…`;
    - the trace `inputs` drops `non_mf_equity_corpus`, `financial_assets` and `max_non_mf_equity_pct_client_input`.
11. In `_practical_lt_result_to_dict`:
    - delete the five keys;
    - the comment becomes `# R177-R181 (Task 7 — amounts, ELSS, residual_equity)`.
12. In `_step5_aggregation_with_frozen`:
    - delete the `non_mf_equity_actual` parameter and the `if non_mf_equity_actual > 0:` block;
    - the docstring becomes "Wraps upstream step5_aggregation.run and appends the frozen ELSS row (tax_efficient_equities). grand_total reconciles to total_corpus (NOT rebalancing_corpus) because the frozen row makes ELSS visible."
13. In `_build_asset_class_breakdown`:
    - delete `lt_subs["non_mf_equities"] = …`;
    - replace the `extended_map` lines (the comment, `extended_map = dict(SUBGROUP_TO_ASSET_CLASS)` and both `extended_map[…] = "equity"` lines) with nothing;
    - use the already-imported `CLASS_OF` wherever `extended_map` was read;
    - docstring and comment mentions become ELSS only.
14. In the `CorpusBreakdown(…)` construction, delete the six removed kwargs.

In `__init__.py:4`, "with ELSS freeze, non-MF equity cap banded on financial assets, and the v2" becomes "with ELSS freeze and the v2".

- [ ] **Step 4: One frozen set**

`human_override.py`:

```python
FROZEN_SUBGROUPS: frozenset[str] = frozenset({"tax_efficient_equities"})
...
# SUBGROUP_TO_ASSET_CLASS omits the frozen practical-only ELSS row; it IS equity
# for class-total purposes (it never scales).
CLASS_OF: dict[str, str] = {
    **SUBGROUP_TO_ASSET_CLASS,
    "tax_efficient_equities": "equity",
}
```

Also in `human_override.py`:
- In the `excludes` docstring, "Frozen rows (ELSS, direct stock) can never be refused — they are holdings" becomes "The frozen ELSS row can never be refused — it is a holding".
- The shortfall copy `"what's already committed (locked ELSS / direct stock, or your "` becomes `"what's already committed (locked ELSS, or your "`. The comment above it changes the same way.

`allocation_snap.py`:
- Delete `_FROZEN` and its comment.
- Add `from practical_asset_allocation.human_override import FROZEN_SUBGROUPS`.
- Use `FROZEN_SUBGROUPS` where `_FROZEN` was read.
- In the module docstring, "Frozen sleeves (ELSS, non-MF equity) are never snapped" becomes "The frozen ELSS sleeve is never snapped".

`Rebalancing/pipeline.py`:
- Delete `_FROZEN_SUBGROUPS` and its comment.
- Import `FROZEN_SUBGROUPS` from `practical_asset_allocation.human_override`, using the same `# type: ignore[import-not-found]` style as the file's other cross-agent import.
- Use it where `_FROZEN_SUBGROUPS` was read.

App `ainv_engine/input_builder.py`:
- Replace lines 34-40 with:

```python
# The engine never buys into a frozen holding row (ELSS lock-in): it is handed
# over as ``exclude_subgroups``, which zero-weights it and renormalises the split.
_EXCLUDE_SUBGROUPS = FROZEN_SUBGROUPS
```

- Add `from practical_asset_allocation.human_override import FROZEN_SUBGROUPS` to its AI_Agents imports.
- In the module docstring, "The two synthetic rows (ELSS + non-MF equity) are passed through" becomes "The synthetic ELSS row is passed through".

`screen_preference_service.py:46-47`: the comment becomes `# Every sub-group is settable except the frozen ELSS holding row — the sleeve`.

- [ ] **Step 5: Shared input and slider comments**

- In `asset_allocation_pydantic/models.py`, delete `financial_assets: Optional[float] = None`.
- In `aa_engine/input_builder.py:266`, delete `financial_assets=pf.financial_assets_pfp(pfp),`. `pick_total_corpus` still reads `pf.financial_assets_pfp`; that stays.
- In `equity_subgroup_slider.py` (lines 18-23, 80-88) and `step4_long_term.py` (lines 445, 459), rewrite any comment that says `locked_amount` is "ELSS + non-MF actual" to say "ELSS (practical engine; 0 in the ideal engine)". The code is unchanged.

- [ ] **Step 6: The app's PAA layer, persistence and pins**

`paa_engine/input_builder.py`:
- In the docstring's scalar list, delete the `non_mf_equity_corpus` and `max_non_mf_equity_pct_client_input` lines.
- In `CorpusPin`, delete `non_mf_equity_corpus: float`.
- In the kwargs, delete `non_mf_equity_corpus=…` and `max_non_mf_equity_pct_client_input=None,`. The comment becomes "Default source is the profile (ELSS 0); a CorpusPin supplies holdings-derived values (deficit-fill lumpsum path)."
- In `debug`, delete `"non_mf_equity_corpus"`.

`paa_engine/service.py`:
- The `trace_line` drops `stocks=…`.
- Delete the "Corpus split" block: the `cb = output.corpus_breakdown` line, the blank-line append and the `**Corpus split**` append.
- In the `build_practical_fallback_brief` docstring, delete "plus a practical-only corpus-split line (MF / non-MF equity / ELSS) drawn from ``corpus_breakdown``".

`practical_allocation_persist_service.py`: delete the kwargs `non_mf_equity_input=`, `non_mf_equity_actual=`, `excess_direct_stocks=` and `max_non_mf_equity_pct_computed=`.

`ainv_engine/service.py`: delete `non_mf_equity_corpus=0.0,` from both `CorpusPin(…)` calls.

- [ ] **Step 7: Flush stale callers, then fix the tests**

Temporarily add `model_config = ConfigDict(extra="forbid")` to `AllocationInput`, importing `ConfigDict` from pydantic. `PracticalAllocationInput` inherits it. Then:

```bash
$PYT AI_Agents/tests app/domains 2>&1 | /usr/bin/grep -E "Extra inputs are not permitted|^(FAILED|ERROR)" | sort | uniq | head -80
```

Fix every caller the sweep names. The sweep is authoritative; this list is what to expect:

- **`test_human_override_golden.py::make_practical_input`**
  - Delete `financial_assets=20_000_000.0,` and `non_mf_equity_corpus=1_000_000.0,`. `total_corpus` stays `20_000_000.0`.
  - Then `rm AI_Agents/tests/fixtures/golden_practical_no_pref.json`; Step 8 re-pins it.
- **`test_human_override_step.py`**
  - Line 53: delete the `non_mf_equities` `pytest.raises` pair.
  - Line 194: delete the `non_mf_equities` assertion.
  - Lines 202-207: the `_run_practical(…)` kwargs become `total_corpus=5_000_000.0, mf_corpus=5_000_000.0, elss_corpus=4_000_000.0`, with the comment `# elss 40L on a 50L corpus → frozen equity ≈ 80% floor.`
- **`test_preference_propagation_e2e.py:45-50`**: the same three kwargs.
- **`test_uniform_subgroup_pins.py`**
  - `test_frozen_holdings_rows_are_ignored` (lines 313-330): delete the `"non_mf_equities": 0.0,` entry. The docstring becomes "ELSS is a holding, not a preference…".
  - Lines 712 and 1009: delete the `+ s4["non_mf_equity_actual"]` terms.
  - Lines 934-936: `make_practical_input(elss_corpus=0.0, mf_corpus=20_000_000.0)`, asserting `clean.elss_corpus == 0.0`.
  - Delete any other `non_mf_equity_corpus=` or `financial_assets=` kwarg.
- **`test_rounding_at_final_step.py`** (lines 44-50, 63-64)
  - Delete `non_mf`, `non_mf_equity_corpus=…` and `financial_assets=corpus`.
  - The docstring stops mentioning direct stocks.
- **`test_carveout_suspension.py`**: delete every `non_mf_equity_corpus=0.0` (lines 67, 164, 200, 214, 373, 456, 533).
- **`test_debt_netting_keeps_held_debt.py:38`**: delete `financial_assets=…` and `non_mf_equity_corpus=0,`.
- **`test_rebalancing_practical_targets.py`**
  - `test_frozen_subgroups_still_pass_through_untouched` becomes `test_the_frozen_elss_subgroup_still_passes_through_untouched`.
  - Its docstring becomes "ELSS is absent from the target map BY DESIGN — step6 surfaces it from `corpus_breakdown`."
  - Delete the `non_mf_equities` row and its assertion.
  - Line 174: `frozen = {"tax_efficient_equities"}`.
- **App `rebal_engine/tests/conftest.py:313-314`** (Edit tool): delete `financial_assets=1_000_000.0,` and `non_mf_equity_corpus=0,`.
- **App `ainv_engine/tests/test_service.py:476-481`**: delete the `financial_assets=`, `non_mf_equity_corpus=` and `max_non_mf_equity_pct_client_input=` kwargs.
- **App `ainv_engine/tests/test_input_builder.py:70-94`**
  - Delete the synthetic `non_mf_equities` row (lines 74, 87) and anything asserted about it.
  - The expected exclude set is `{"tax_efficient_equities"}`.
- **App `ainv_engine/tests/test_category.py:77`**: `_EXCLUDE = {"tax_efficient_equities"}`.
- **App `paa_engine/tests/test_input_builder.py`**
  - The pin becomes `CorpusPin(total_corpus=550000.0, mf_corpus=500000.0, elss_corpus=20000.0)`.
  - Rename `…all_four_scalars` to `…all_three_scalars`.
  - Delete the `non_mf_equity_corpus` assertions.
- **App `profile/tests/test_screen_full_distribution.py:426`**: delete `assert inp.non_mf_equity_corpus == 0.0`.
  - **Keep line 52 (`financial_assets=CORPUS`).** It is the profile field `pick_total_corpus` reads (`portfolios=[]`).
- **App `profile/tests/test_screen_preference_service.py`**
  - Line 65 loops over `("tax_efficient_equities",)`.
  - Line 158 asserts only `"tax_efficient_equities" not in cat`.

Re-run the sweep until it prints nothing new. **Then remove the temporary `model_config` line and the `ConfigDict` import.**

- [ ] **Step 8: Re-pin the practical golden and the tests pinned to it**

```bash
$PYT AI_Agents/tests/test_human_override_golden.py
git diff --stat AI_Agents/tests/fixtures/
git diff AI_Agents/tests/fixtures/golden_practical_no_pref.json | head -120
```

**The ideal golden must NOT be in the diff.** If it is, stop: removing `financial_assets` must not move the ideal plan.

The practical diff may show only these changes:
- class totals unchanged to within ₹1;
- the `non_mf_equities` rows gone;
- the six stock `CorpusBreakdown` keys gone;
- the ₹10L moved into MF equity subgroups and a larger multi-asset sleeve;
- `arbitrage_plus_income` and `gold_commodities` shrunk by the sleeve's slices;
- the long-term bucket's `allocated_amount` up by ₹10L.

Simulated values: multi_asset 4,345,846 → 4,961,231; arbitrage_plus_income 7,581,538 → 7,427,692; gold 1,535,415 → 1,473,877. Any other kind of change is a bug.

Three tracked tests in `test_uniform_subgroup_pins.py` pin this base. Their "fix the engine, never this test" warning does not apply here: the base itself legitimately changed. Re-pin them to the simulated values:
- **`NEUTRAL_SUBGROUP_MIX` (lines 575-585):**

```python
NEUTRAL_SUBGROUP_MIX = {
    "short_debt": 1.5,
    "arbitrage_plus_income": 37.14,
    "multi_asset": 24.81,
    "low_beta_equities": 10.72,
    "medium_beta_equities": 5.77,
    "us_equities": 7.7,
    "gold_commodities": 7.37,
    "tax_efficient_equities": 5.0,
}
```

- **`test_the_variants_really_do_bind_on_different_terms` (lines 538-552).** The binding terms are unchanged:

```python
            "default": (4_961_231, 8_062_000, 8_668_000, 1_970_000),  # 0.40 cap binds
            "heavy_elss": (2_499_692, 4_062_000, 8_668_000, 1_970_000),  # 0.40 cap binds
            "risk_10": (8_668_000, 16_533_000, 2_167_000, 0),  # debt room binds
```

- **`test_the_risk_9_5_sleeve_survives_its_zero_commodity_class` (lines 818-823).** `others_amount` stays 0:

```python
        assert s4["multi_asset_block"]["multi_asset_amount"] == 9_810_462
```

If a live value differs from the simulated one, stop and investigate. Do not copy the live number into the test.

- [ ] **Step 9: Run, and confirm everything passes**

Run: `$PYT AI_Agents/tests app/domains`
Expected:
- the drift test PASSES;
- every other failure is already in `$SCRATCH/baseline_ids.txt`.

- [ ] **Step 10: Checkpoint.** Do not commit.

---

### Task 6: Delete `mf_corpus` (dead once stocks are gone)

After Task 5, every producer sets `mf_corpus` equal to `total_corpus`:
- the rebalancing builder;
- the PAA default;
- both ainv pins.

The engine only echoes it. This task deletes it. Its separate task boundary lets a reviewer reject it without rejecting Task 5.

**Files:**
- `AI_Agents/src/practical_asset_allocation/pipeline.py`:
  - the `mf_corpus` field and docstring
  - the accounting docstring
  - `CorpusBreakdown.mf_corpus_inr`
  - the trace key
  - the construction
- `app/domains/practical_asset_allocation/services/paa_engine/input_builder.py`:
  - `CorpusPin.mf_corpus`
  - the `mf_corpus=` kwarg
  - the docstring line
  - the debug key
- `app/domains/practical_asset_allocation/services/paa_engine/service.py`: the `mf=` term in `trace_line`
- `app/domains/practical_asset_allocation/services/practical_allocation_persist_service.py`: the `mf_corpus=` kwarg (the column stays at default 0)
- `app/domains/rebalancing/services/rebal_engine/input_builder.py:436-441`: the `"mf_corpus"` update key (Edit tool)
- `app/domains/additional_investment/services/ainv_engine/service.py`: `mf_corpus=` in both `CorpusPin` calls
- Tests: every `mf_corpus=` kwarg (Step 2 sweep). This includes `test_min_change_threshold.py`, `make_practical_input` and the Task 5 replacements.

**Interfaces:**
- `PracticalAllocationInput` has no `mf_corpus`.
- `CorpusPin(total_corpus, elss_corpus)`.
- `CorpusBreakdown(total_corpus_inr, elss_corpus_inr, rebalancing_corpus_inr)`.

- [ ] **Step 1: Delete the field everywhere in runtime code**

1. In `PracticalAllocationInput`, delete `mf_corpus: float = Field(..., ge=0)` and its docstring. The class docstring's accounting block becomes:

```
    Implicit corpus accounting (not separate inputs):
      rebalancing_corpus = total_corpus - elss_corpus
```

   and the `elss_corpus` docstring "(subset of mf_corpus)" becomes "(part of total_corpus)".
2. In `CorpusBreakdown`, delete `mf_corpus_inr` and `mf_corpus_inr=int(round(inp.mf_corpus)),`, and delete `"mf_corpus": …` from the trace `inputs`.
3. App changes:
   - `CorpusPin`: delete `mf_corpus: float`.
   - Builder: delete `mf_corpus=(corpus_pin.mf_corpus if corpus_pin else base_input.total_corpus),`, and delete the docstring line `mf_corpus = total_corpus …`.
   - `debug`: delete `"mf_corpus"`.
   - Service `trace_line`: delete `mf={practical_input.mf_corpus}, `.
   - Persist: delete `mf_corpus=…`.
   - Rebalancing builder: the `model_copy(update=…)` keeps only `"total_corpus"`.
   - Both ainv pins: delete `mf_corpus=…`.

- [ ] **Step 2: Sweep the callers**

Re-add the temporary `extra="forbid"` from Task 5 Step 7, then:

```bash
$PYT AI_Agents/tests app/domains 2>&1 | /usr/bin/grep -E "Extra inputs are not permitted|^(FAILED|ERROR)" | sort | uniq | head -80
/usr/bin/grep -rn "mf_corpus" app AI_Agents/src AI_Agents/tests --include="*.py" | grep -v "/Testing/\|Master_testing\|models/run.py\|non_mf"
```

Delete every `mf_corpus=` kwarg and `mf_corpus_inr` reference the two commands report. Then remove the temporary `model_config` and `ConfigDict` again.

Expected: the grep's only remaining hit is the ORM column in `models/run.py`, which the filter excludes.

- [ ] **Step 3: Re-pin the practical golden**

```bash
rm AI_Agents/tests/fixtures/golden_practical_no_pref.json
$PYT AI_Agents/tests/test_human_override_golden.py
git diff AI_Agents/tests/fixtures/golden_practical_no_pref.json
```

The fixture is still uncommitted from Task 5, so `git diff` compares against HEAD. The only change on top of Task 5's diff should be the missing `"mf_corpus_inr"` line. Check with the following, against the Task 5 output:

```bash
git diff AI_Agents/tests/fixtures/golden_practical_no_pref.json | /usr/bin/grep "^[-+]" | /usr/bin/grep -c mf_corpus
```

Expected: `1`.

- [ ] **Step 4: Run, and confirm everything passes**

Run: `$PYT AI_Agents/tests app/domains`
Expected: every failure is already in `$SCRATCH/baseline_ids.txt`.

- [ ] **Step 5: Checkpoint.** Do not commit.

---

### Task 7: Local harnesses, CLAUDE.md, full-suite diff

**Files:**
- Modify (gitignored, local only):
  - `AI_Agents/src/*/Testing/**`
  - `AI_Agents/src/*/Master_testing/**`
  - `AI_Agents/lifecycle_sim_testing/**`
- Modify these CLAUDE.md files:
  - `AI_Agents/src/practical_asset_allocation/CLAUDE.md`
  - `AI_Agents/src/Rebalancing/CLAUDE.md`
  - `AI_Agents/src/additional_investment/CLAUDE.md`
  - `app/domains/practical_asset_allocation/CLAUDE.md`
  - `app/domains/additional_investment/CLAUDE.md`
  - `app/domains/asset_allocation/CLAUDE.md`
  - `app/domains/portfolio/CLAUDE.md`

- [ ] **Step 1: Update the local harnesses**

```bash
/usr/bin/grep -rln "non_mf_equit\|excess_direct_stocks\|SELL_DIRECT_STOCKS\|max_non_mf\|financial_assets=\|mf_corpus\|REBALANCE_MIN_CHANGE_PCT\|rebalance_min_change_pct\|sell_excess_direct_stocks\|_funds_per_subgroup\|extra_cash_inr" AI_Agents/src/*/Testing AI_Agents/src/*/Master_testing AI_Agents/lifecycle_sim_testing
```

For each file, apply the edits from Tasks 1-6:
- delete stock, `financial_assets` and `mf_corpus` kwargs and fields;
- `_funds_per_subgroup(a, b)` calls become `funds_per_subgroup(a)`. This covers every call in `Rebalancing/Testing/test_subgroup_fund_count.py`;
- replace `rebalance_min_change_pct=0.10` with the two knobs;
- in `Rebalancing/Testing/Master_testing/runner.py:89`, replace the field name with the two new ones.

Then delete the stock-only test functions inside these files:
- `Rebalancing/Testing/test_step4_direct_stock_cash.py` (its tests);
- the stock cases in `test_part_c.py` and `test_subgroup_fund_count.py`;
- in PAA `Testing/`: the band and non-MF tests in `test_long_term_part3.py`, scenarios 4-5 of `test_scenarios_b9.py`, `test_build_output_smoke.py::test_build_output_records_non_mf_excess`, and the non-MF row in `test_step5_with_frozen.py`.

Do not delete whole files: these folders are not in git. A file emptied of tests can stay empty.

Two threshold tests:
- In `Rebalancing/Testing/test_step2b_debt_netting.py`, update `test_sub_threshold_residual_is_absorbed_not_abandoned` and `test_warning_reports_the_full_amount_kept_including_absorbed_residual`. Keep each residual below `min_change_threshold`, for example under 1% of that test's corpus, and keep each test's intent.
- `Rebalancing/Testing/test_step2_dual_threshold.py` tests an unbuilt design. Leave it failing as it already does at baseline, and mention it to Amoul.

Lifecycle sim:
- `engines.py` stops passing `non_mf_equity_corpus`, `mf_corpus` and `financial_assets` into engine inputs.
- `lumpsum()` passes `monthly_sip=0.0` to the practical allocation.
- Do NOT edit `run_ab.py`. It already references the never-merged `REBALANCE_MIN_BUY_CHANGE_PCT` design and is broken at baseline. Mention it to Amoul.

Then:

```bash
$PYT AI_Agents/src/Rebalancing/Testing AI_Agents/src/practical_asset_allocation/Testing AI_Agents/src/additional_investment/Testing AI_Agents/src/asset_allocation_pydantic/Testing AI_Agents/lifecycle_sim_testing/tests
```

Expected: no failure that is not already at baseline.

- [ ] **Step 2: Bring the CLAUDE.md files up to date**

Write them in the present tense with no history, using the Edit tool (several are CRLF):

- **PAA engine CLAUDE.md** (`:3`, `:21`):
  - the corpus scalars are total and ELSS;
  - ELSS is the only frozen row (`human_override.FROZEN_SUBGROUPS`, the single definition);
  - there is no non-MF input.
- **Rebalancing CLAUDE.md** (`:3`, `:25`):
  - ELSS is the only frozen subgroup;
  - a row trades when `|diff| ≥ min_change_threshold` = min(1% of `total_corpus`, 50% of max(target, present));
  - the fund count is `config.funds_per_subgroup`, shared with additional_investment.
- **additional_investment CLAUDE.md** (engine `:20`, app `:18`):
  - the lumpsum goal share is the goals' full remaining need (the lumpsum PAA run has `monthly_sip=0`);
  - only frozen ELSS is excluded.
- **App PAA CLAUDE.md:**
  - `:24`: drop the `non_mf_equity_corpus` mention.
  - `:25`: "only the SIP flow overrides `monthly_sip`" becomes "the SIP flow passes its deploy amount as `monthly_sip`; the lumpsum flow passes 0, so the lump sum funds the goals' full remaining need".
- **App asset_allocation CLAUDE.md:** add a gotcha.
  - Rule: `pick_total_corpus` takes the primary-portfolio candidate from the stock-free holdings snapshot, not from `total_value`.
  - Why: CAMS rewrites `total_value` but leaves bank-sync stock rows behind.
- **App portfolio CLAUDE.md:**
  - `holdings_snapshot` skips direct-equity rows (`EQUITY_INSTRUMENT_TYPES`);
  - `allocation_rollup` imports the same set and still shows them as Equity.

- [ ] **Step 3: Run the full suite and diff against the baseline**

```bash
.venv-mac/bin/python -m pytest -q -rfE --continue-on-collection-errors -p no:cacheprovider > $SCRATCH/after_full.txt 2>&1; tail -3 $SCRATCH/after_full.txt
/usr/bin/grep -E "^(FAILED|ERROR) " $SCRATCH/after_full.txt | sed 's/ - .*//' | sort > $SCRATCH/after_ids.txt
comm -13 $SCRATCH/baseline_ids.txt $SCRATCH/after_ids.txt
```

Expected: `comm` prints nothing, meaning no new failing or erroring node ids. Investigate any line before claiming done.

- [ ] **Step 4: Final check of the tracked changes**

```bash
git status --short
git diff --stat
```

Expected:
- only files named in Tasks 1-7 changed, plus `.claude/settings.local.json` from before this work;
- `AI_Agents/Reference_docs/` is untouched;
- nothing is committed.
