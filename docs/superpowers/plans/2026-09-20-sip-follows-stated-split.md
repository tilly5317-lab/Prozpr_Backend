# SIP Follows the Customer's Stated Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a customer's plan was shaped by an investment preference, their monthly SIP deploys in the percentages that preference states — instead of being weighted toward the nearest unfunded goal, which today produces a ₹0 SIP for anyone with a goal inside five years.

**Architecture:** Under a stated preference the allocation engine suspends its bucket carve-outs, so every row's `long_term` column equals its `total` and *is* the stated split. `compute_targets` already weights by whichever bucket column it is handed; it is simply handed the wrong one. The whole behaviour change is one branch in the app-layer input builder that forces the two goal-funding flags true for a preference SIP, selecting `LONG_TERM`. **Nothing under `AI_Agents/src/` changes.** The rest is supporting work: a preference-gated rescue floor, one record-honesty line, a version stamp, and the narration that would otherwise assert falsehoods.

**Tech Stack:** Python 3.12, pydantic v2, FastAPI, SQLAlchemy async, pytest (`asyncio_mode=auto`).

**Spec:** `docs/superpowers/specs/2026-09-20-sip-follows-requested-split-design.md`

**Review history:** an 8-task draft was reviewed as-if-merged and cut to 4. Three of its tasks shipped code that would not run; one changed behaviour for customers the spec declares untouched; one reinvented an existing cross-domain key. Those corrections are folded in below — see **Rejected approaches** at the end so they are not retried.

## Global Constraints

- **Run tests with** `.venv-mac/bin/python -m pytest` from `Prozpr_Backend/`. Bare `pytest` collects nothing.
- **Do NOT commit.** Leave every change in the working tree and report what is ready. (Standing user preference.)
- **Baseline**, measured 2026-09-20 — run before you start and after every task:
  `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing AI_Agents/tests/test_preference_propagation_e2e.py app/domains/additional_investment -q` → **220 passed**.
- **Nothing under `AI_Agents/src/` may change.** No engine file, model or pipeline. `AI_Agents/tests/` and the engine's `CLAUDE.md` are fine.
- **Read `human_override_applied` and `grand_total` with `getattr`.** Test stubs are bare `SimpleNamespace`; a direct attribute read breaks the baseline in both files that use it.
- **Money is `float` in this domain**, not `Decimal`.
- **Comments are sparse.** No audit narrative in source — at most two lines per branch, and never a line-range pointer into another package (they rot).
- **`AI_Agents/src/*/Testing/` is gitignored** but is collected by the baseline command.
- **Trigger wording:** the branch fires when *a preference shaped this run* — saved **or** a per-turn one-off (`chat.py:1049` makes `human_override_applied` non-None). Not "saved a preference". This distinction is why the narration reader in Task 2 must fail closed.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `app/domains/additional_investment/services/ainv_engine/input_builder.py` | The branch | 1 |
| `app/domains/additional_investment/services/ainv_engine/service.py` | Gated rescue floor, record-honesty line, version stamp | 1 |
| `app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py` | Branch unit tests | 1 |
| `app/domains/additional_investment/services/ainv_engine/tests/test_service.py` | Rescue + extras tests | 1 |
| `app/domains/additional_investment/services/ainv_engine/tests/test_persist.py` | Version literal | 1 |
| `app/domains/profile/services/preference_view.py` | `categories_set` on the existing block | 2 |
| `app/domains/additional_investment/services/ainv_engine/chat.py` | `active_preferences` through the formatter + three prompt regions | 2 |
| `app/domains/additional_investment/services/ainv_engine/tests/test_chat.py` | Narration tests | 2 |
| `AI_Agents/tests/test_ainv_preference_split_e2e.py` | **New.** Plan-to-fund fidelity at a stated tolerance | 3 |
| `AI_Agents/tests/test_carveout_suspension.py` | The equivalence Task 1 depends on | 3 |
| `AI_Agents/src/additional_investment/CLAUDE.md`, `app/domains/additional_investment/CLAUDE.md` | Two clauses | 4 |
| `AI_Agents/tests/test_preference_propagation_e2e.py`, `…/tests/test_service.py` | Two stale docstrings | 4 |

---

### Task 1: The branch, the gated rescue floor, the record line, the version stamp

All four edits live in two adjacent files and share one test cycle.

**Files:**
- Modify: `ainv_engine/input_builder.py:126-131`
- Modify: `ainv_engine/service.py` — constant near `:92`, rescue at `:314`, `_extras` at `:396`, version at `:110`
- Test: `tests/test_input_builder.py`, `tests/test_service.py`, `tests/test_persist.py`

**Interfaces:**
- Consumes: `allocation_output` (the builder's 2nd positional parameter, typed `Any`), `cadence`, and in the service `paa_outcome.result`.
- Produces: `_SIP_MIN_FAITHFUL_CORPUS_INR = 10_000.0`; `request_extras["goal_funding_flags_forced"]`; `AINV_ENGINE_VERSION == "ainv-3.3.0"`.

- [ ] **Step 1: Write the failing builder tests**

Append to `test_input_builder.py`, extending the file's existing `_alloc(rows)` stand-in rather than introducing a `conftest.py` — every test file in this directory keeps private helpers:

```python
def _alloc_with_preference(rows) -> SimpleNamespace:
    """`_alloc` for a plan a stated preference shaped."""
    return SimpleNamespace(
        aggregated_subgroups=rows,
        human_override_applied=SimpleNamespace(
            preference_applied=True, shortfall_reason=None
        ),
    )


async def test_preference_sip_sets_both_flags_true(monkeypatch):
    monkeypatch.setattr(mod, "_goal_funding_flags", _fake_flags(short=False, medium=False))
    inp, _ = await mod.build_additional_investment_input_for_user(
        _ctx(), _alloc_with_preference(_rows()),
        deploy_amount_inr=25_000.0, cadence=Cadence.SIP_MONTHLY,
    )
    assert (inp.short_term_fulfilled, inp.medium_term_fulfilled) == (True, True)


async def test_no_preference_sip_keeps_the_goal_funding_flags(monkeypatch):
    monkeypatch.setattr(mod, "_goal_funding_flags", _fake_flags(short=False, medium=False))
    inp, _ = await mod.build_additional_investment_input_for_user(
        _ctx(), _alloc(_rows()),
        deploy_amount_inr=25_000.0, cadence=Cadence.SIP_MONTHLY,
    )
    assert (inp.short_term_fulfilled, inp.medium_term_fulfilled) == (False, False)


async def test_lumpsum_with_preference_keeps_the_goal_funding_flags(monkeypatch):
    monkeypatch.setattr(mod, "_goal_funding_flags", _fake_flags(short=False, medium=False))
    inp, _ = await mod.build_additional_investment_input_for_user(
        _ctx(), _alloc_with_preference(_rows()),
        deploy_amount_inr=25_000.0, cadence=Cadence.LUMPSUM,
    )
    assert (inp.short_term_fulfilled, inp.medium_term_fulfilled) == (False, False)
```

**`mod`, `_ctx()`, `_rows()` and `_fake_flags` are placeholders for whatever this file actually calls them** — read its imports and `test_short_term_unfunded_sets_flag_false` (`:161`) first and match. If no `_fake_flags` exists, write one returning an async callable that returns `(short, medium)`.

- [ ] **Step 2: Run them — expect exactly one failure**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -v`
Expected: `test_preference_sip_sets_both_flags_true` FAILS (`(False, False) != (True, True)`); the other two PASS, since they assert today's behaviour.

- [ ] **Step 3: Add the branch**

In `input_builder.py`, the `else` arm becomes:

```python
    else:
        short_term_fulfilled, medium_term_fulfilled = await _goal_funding_flags(
            user, asof
        )
        # A stated preference suspends the bucket carve-outs, so this plan's
        # long_term column IS the stated split (spec 2026-09-20 §2).
        # getattr: this parameter is duck-typed and stubs omit the attribute.
        if cadence is Cadence.SIP_MONTHLY and (
            getattr(allocation_output, "human_override_applied", None) is not None
        ):
            short_term_fulfilled = medium_term_fulfilled = True
```

Two comment lines, not five. Do not add a line-range pointer into `practical_asset_allocation`.

- [ ] **Step 4: Verify the branch and the baseline**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -v` → all PASS.
Then the baseline command → **223 passed**. If you see `AttributeError` on `SimpleNamespace`, you dropped the `getattr`.

- [ ] **Step 5: Write the failing rescue test**

The ₹1-crore rescue fires only `if not response.buys` (`service.py:314`). After Step 3 a 1-row allocation produces buys, so for `0 < corpus < ~₹1,000` it stops firing and a degenerate single-class SIP ships — measured at corpus ₹100 against a stated 50/30/20: **100% equity**. Corpus floors to ₹100 multiples (`aa_engine/input_builder.py:236`), so the reachable band is ₹100–900: the no-CAMS cohort.

Append to `test_service.py`, extending its existing `_fake_alloc()` (`:163`) with `grand_total` and `human_override_applied` rather than writing a new fixture:

```python
async def test_tiny_corpus_preference_sip_triggers_sized_fallback(monkeypatch):
    """corpus ₹100 with a preference now produces buys, so `not response.buys`
    no longer fires. The sized re-derivation must still run."""
    pins = []

    async def _fake_paa(*args, **kwargs):
        pins.append(kwargs.get("corpus_pin"))
        return _fake_alloc(grand_total=100.0, with_preference=True)

    monkeypatch.setattr(mod, "compute_practical_allocation_result", _fake_paa)
    await mod.compute_additional_investment_result(...)
    assert len(pins) == 2 and pins[0] is None
    assert pins[1].total_corpus == mod._SIP_RATIO_SIZING_CORPUS_INR


async def test_no_preference_tiny_corpus_does_not_gain_a_new_trigger(monkeypatch):
    """Spec §6: no-preference customers are untouched. With buys present and no
    preference, the corpus floor must not fire."""
```

- [ ] **Step 6: Run — expect the first to fail**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service.py -k tiny_corpus -v`
Expected: `..._triggers_sized_fallback` FAILS (`1 != 2`); the no-preference guard PASSES.

- [ ] **Step 7: Add the constant and the gated trigger**

Beside `_SIP_RATIO_SIZING_CORPUS_INR` (near `service.py:92`):

```python
# Below this the plan emits too few rows for a faithful split — measured, a
# stated 50/30/20 lands at 100% equity at ₹100 corpus, and is correct from
# ₹10,000 up.
_SIP_MIN_FAITHFUL_CORPUS_INR = 10_000.0
```

`service.py:314` becomes:

```python
    _pref_shaped = (
        getattr(paa_outcome.result, "human_override_applied", None) is not None
    )
    if cadence is Cadence.SIP_MONTHLY and (
        not response.buys
        or (
            _pref_shaped
            and getattr(paa_outcome.result, "grand_total", float("inf"))
            < _SIP_MIN_FAITHFUL_CORPUS_INR
        )
    ):
```

**The `_pref_shaped` conjunct is load-bearing**, not defensive: without it the new arm fires for every SIP under ₹10,000 corpus, including no-preference customers whom spec §6 declares untouched. **The `getattr` on `grand_total` is also load-bearing** — the bare form breaks six baseline tests (four in `test_service.py`, two in `test_preference_chat_ainv.py`), and `service.py:314` sits outside every `try`, so the `AttributeError` escapes `compute_additional_investment_result`.

Update the comment block above the `if` to describe both triggers.

- [ ] **Step 8: Add the record-honesty line**

One line in the existing `_extras` assembly (`service.py:396`, beside `focus_category`). Do **not** touch the builder debug dict — spec §6 lists it as deliberately unchanged:

```python
    if cadence is Cadence.SIP_MONTHLY and _pref_shaped:
        # The engine-input dump lands in request_input and ships in the DPDP
        # export; it would otherwise assert this customer's near-term goals are
        # funded.
        _extras["goal_funding_flags_forced"] = "stated_preference_suspends_carve_outs"
```

Test it by asserting the key lands in the `request_extras` passed to the persist call. **The symbol to patch is `persist_additional_investment_recommendation`** (`additional_investment_persist_service.py:42`) — there is no `persist_additional_investment_run`. Note `persist_practical_allocation_run` runs first, unpatched, inside the same `try`.

- [ ] **Step 9: Bump the version**

After the `3.2.0:` changelog line in `service.py`:

```python
# 3.3.0: a SIP whose plan was shaped by a stated preference targets the
# long-term column — the stated split — instead of the nearest unfunded goal
# (spec 2026-09-20).
AINV_ENGINE_VERSION = "ainv-3.3.0"
```

`ainv-3.2.0` has exactly **three** live hits: `service.py:110`, `tests/test_persist.py:166`, and `app/domains/additional_investment/CLAUDE.md:16`. Update the first two here; the third is Task 4's line. Confirm with:

```bash
grep -rn "ainv-3\.2\.0" --include="*.py" --include="*.md" app/ AI_Agents/
```

- [ ] **Step 10: Run everything**

Run the baseline command. Expected: **226 passed**. Report the count; do not commit.

---

### Task 2: Stop the reply explaining the split with a goal horizon

**Files:**
- Modify: `app/domains/profile/services/preference_view.py:136-140` (`active_preferences_block`)
- Modify: `ainv_engine/chat.py` — `_format_or_fallback_ainv` (`~:795`), `build_ainv_facts_pack` (`:585`), and three regions of `_AINV_FORMATTER_BODY` (`:289-296`, `:356`/`:359`, `:379-380`)
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `outcome.practical_result` — **already on `AdditionalInvestmentRunOutcome`** (`service.py:142`).
- Produces: `build_ainv_facts_pack(..., active_preferences: dict | None = None)` and `facts["active_preferences"]`, carrying a new `categories_set: bool`.

**Use the existing cross-domain key, not a new one.** `active_preferences` is already the facts key for exactly this in both sibling chat modules — `aa_engine/chat.py:1060` and `rebal_engine/chat.py:831`, both documented in their prompt bodies (`aa_engine/chat.py:297`, `rebal_engine/chat.py:521`). And `app/domains/profile/CLAUDE.md:16` mandates the reader: *"Outside this domain, read a preference via `preference_view.active_preferences_for`, never the relationship"*, with `preference_view.py:143-145` adding that **chat modules are not on the sanctioned allow-list**. Importing `load_human_override_for_user` into `chat.py` would breach that.

- [ ] **Step 1: Add `categories_set` to the existing block**

`preference_view.py`, in `active_preferences_block`'s return dict:

```python
        "categories_set": bool(getattr(row, "resolved_targets", None)),
```

This is the case-1 / case-2 discriminator: `resolved_targets` is set only when the customer named sub-categories. It is **not** on `HumanOverrideApplied` (`preference_applied` + `shortfall_reason` only), which is why a second reader looked necessary.

- [ ] **Step 2: Write the failing narration tests**

```python
def test_facts_pack_omits_active_preferences_by_default():
    assert "active_preferences" not in build_ainv_facts_pack(_output())


async def test_class_only_preference_marks_categories_unset():
    """Case 1: the customer set the class bars only, so the reply must say we
    chose the categories."""
    ...  # run _format_or_fallback_ainv with a user_ctx whose saved row has
         # resolved_targets None, assert facts["active_preferences"]["categories_set"] is False


async def test_subcategory_preference_marks_categories_set():
    """Case 2: resolved_targets present."""
```

Follow `test_chat.py:564` and `:474` for the omits-by-default shape. Assert **behaviour** — the block's presence and `categories_set` — not kwarg plumbing.

- [ ] **Step 3: Compute it once, inside the formatter wrapper**

Add `practical_result` to `_format_or_fallback_ainv` and compute there, **not** at the call site:

```python
    active_preferences = (
        pref_view.active_preferences_for(ctx.user_ctx, practical_result)
        if practical_result is not None
        else None
    )
```

then pass `active_preferences=active_preferences` into `build_ainv_facts_pack`, which sets `facts["active_preferences"]` only when not None.

**Computing inside the wrapper covers both callers.** `_ordinary_deploy` (`chat.py:958`) is the dominant path, but `_handle_preference_what_if_ainv` calls the same formatter at `chat.py:1101` — and that path sets a one-off override (`chat.py:1049`), so Task 1's branch fires there too and the same three prompt regions would assert the same falsehoods. That seam is UNREFERENCED today (`app/domains/additional_investment/CLAUDE.md:24`); when it is re-enabled, note that `active_preferences_for` reads the **saved** row, so a chat-only one-off yields `None` and falls back to today's narration — acceptable, and `rebal_engine/chat.py:833`'s `active_preferences_block(candidate_row, practical)` form is how to close it.

- [ ] **Step 4: Branch the three prompt regions of `_AINV_FORMATTER_BODY`**

1. `:289-296` — the `target_bucket` description explains `long_term` as "the short and medium goals are funded (or there are none)". Add: when `active_preferences` is present, `target_bucket` is not the reason for the split and must not be used to explain it.
2. `:379-380` — `(derived from target_bucket)` becomes conditional: with `active_preferences` present, the split follows the preference; and when `categories_set` is false, add that they set the equity/debt/gold split and we chose the categories inside each, settable on the preferences page.
3. `:356` and `:359` — `plan_by_goals — (SIP) the plan deploys by goals` and *"ALWAYS close the category topic with the caveat: … the plan spreads by their goals"*. `plan_by_goals` is a live SIP status (`category.py:64, :73`), so these fire on any category ask. Make both conditional; with `active_preferences` present the caveat becomes that the plan follows the customer's own split.

- [ ] **Step 5: Run**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_chat.py app/domains/profile -v` → all PASS.
Then the baseline command. Expected: **229 passed**.

---

### Task 3: Fidelity through the real engines, and the equivalence the branch rests on

**Files:**
- Create: `AI_Agents/tests/test_ainv_preference_split_e2e.py`
- Modify: `AI_Agents/tests/test_carveout_suspension.py`

**Interfaces:** consumes Task 1. Produces nothing importable.

**Two traps baked into the code below — do not "simplify" either away:**

1. `make_practical_input` defaults to `non_mf_equity_corpus=1_000_000.0, elss_corpus=1_000_000.0`. The SIP passes **no `CorpusPin`**, so on the real SIP path both are `0.0` and the two frozen rows are absent from the output entirely. Leave the overrides in or you measure the *lumpsum* path.
2. `build_ainv_asset_class_breakdown` returns **rupee rows** (`rows[].asset_class` ∈ `{"Equity","Debt","Others"}`, `rows[].target_inr`) plus `target_total_inr` — **not** percentages, and not the engine's `AssetClassSplitBlock`.

**Scope note:** this file hard-codes both flags `True`, so it exercises the engine-and-ranking fidelity that Task 1's branch *unlocks*, not the branch itself — the branch is covered by Task 1's unit tests and the tiny-corpus case by Task 1 Step 5. Do not add a corpus-₹100 case here; at this layer it is unsatisfiable.

- [ ] **Step 1: Write the file**

```python
"""SIP fidelity under a stated preference — the acceptance test for
docs/superpowers/specs/2026-09-20-sip-follows-requested-split-design.md.

Runs the REAL practical allocation and the REAL additional-investment engine
against the live fund ranking, at the bucket Task 1's branch selects. Asserts on
the look-through class rollup, never on raw subgroup buys: multi_asset is a
hybrid the customer-facing bars unbundle 65/25/10, so a correctly-honoured
50/30/20 plan sums RAW to roughly 25/20/55.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from test_human_override_golden import make_practical_input  # noqa: E402

TOLERANCE_PP = 0.25
SIP_INR = 25_000.0
_CLASS_KEY = {"Equity": "equity", "Debt": "debt", "Others": "others"}


def _run_sip(class_mix, pins, corpus, sip_inr=SIP_INR):
    """Stated preference -> the realised SIP.

    Returns (class_pct, subgroup_pct, deployed_inr).
    """
    from additional_investment.models import (
        AdditionalInvestmentInput, Cadence, RankedFund, SubgroupBucketAmounts,
    )
    from additional_investment.pipeline import run_additional_investment
    from practical_asset_allocation.human_override import HumanOverridePreferences
    from practical_asset_allocation.pipeline import run_practical_allocation
    from Rebalancing.config import AINV_SIP_FUND_CAP_FLOOR_INR, OTHERS_FUND_CAP_PCT
    from Rebalancing.tables import cap_pct_for

    from app.domains.additional_investment.services.additional_investment_read_service import (
        build_ainv_asset_class_breakdown,
    )
    from app.domains.additional_investment.services.ainv_engine.input_builder import (
        _EXCLUDE_SUBGROUPS,
    )
    from app.domains.profile.services.screen_preference_service import (
        resolve_screen_preferences,
    )
    from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking

    resolved = resolve_screen_preferences(class_mix, pins)
    prefs = HumanOverridePreferences(
        asset_class_requested=resolved.asset_class_requested,
        subgroup_emphasis=resolved.subgroup_emphasis,
    )
    practical = run_practical_allocation(
        make_practical_input(
            total_corpus=corpus, mf_corpus=corpus,
            non_mf_equity_corpus=0.0, elss_corpus=0.0,
            net_financial_assets=corpus,
        ).model_copy(update={"human_override": prefs})
    )
    subgroups = [
        SubgroupBucketAmounts(**row.model_dump())
        for row in practical.aggregated_subgroups
    ]
    ranked = [
        RankedFund(
            asset_subgroup=rr.asset_subgroup, sub_category=rr.sub_category,
            rank=rr.rank, isin=rr.isin, scheme_code=rr.scheme_code,
            recommended_fund=rr.fund_name,
        )
        for rows in get_fund_ranking().values() for rr in rows
    ]
    out = run_additional_investment(
        AdditionalInvestmentInput(
            deploy_amount_inr=sip_inr,
            cadence=Cadence.SIP_MONTHLY,
            subgroups=subgroups,
            # What Task 1's branch forces for a preference SIP.
            short_term_fulfilled=True,
            medium_term_fulfilled=True,
            ranked_funds=ranked,
            cap_pct_by_subgroup={
                s.subgroup: cap_pct_for(s.subgroup)
                for s in subgroups if s.subgroup not in _EXCLUDE_SUBGROUPS
            },
            default_cap_pct=OTHERS_FUND_CAP_PCT,
            exclude_subgroups=set(_EXCLUDE_SUBGROUPS),
            sip_fund_cap_floor_inr=AINV_SIP_FUND_CAP_FLOOR_INR,
        )
    )
    deployed = float(out.deployed_inr)
    breakdown = build_ainv_asset_class_breakdown(
        (b.asset_subgroup, b.sub_category, float(b.amount_inr)) for b in out.buys
    )
    class_pct = {"equity": 0.0, "debt": 0.0, "others": 0.0}
    if breakdown is not None and breakdown.target_total_inr > 0:
        for row in breakdown.rows:
            class_pct[_CLASS_KEY[row.asset_class]] = (
                row.target_inr / breakdown.target_total_inr * 100.0
            )
    subgroup_pct: dict[str, float] = {}
    for b in out.buys:
        subgroup_pct[b.asset_subgroup] = (
            subgroup_pct.get(b.asset_subgroup, 0.0)
            + float(b.amount_inr) / deployed * 100.0
        )
    return class_pct, subgroup_pct, deployed


@pytest.mark.parametrize("class_mix", [
    {"equity": 50.0, "debt": 30.0, "others": 20.0},
    {"equity": 80.0, "debt": 15.0, "others": 5.0},
    {"equity": 30.0, "debt": 60.0, "others": 10.0},
])
def test_case1_class_only_preference_reaches_the_sip(class_mix):
    class_pct, _, deployed = _run_sip(class_mix, pins=[], corpus=5_000_000.0)
    for cls, stated in class_mix.items():
        assert abs(class_pct[cls] - stated) <= TOLERANCE_PP, (
            f"{cls}: stated {stated}, realised {class_pct[cls]:.3f}"
        )
    assert deployed == pytest.approx(SIP_INR, abs=200.0)


def test_case2_subcategory_preference_reaches_the_sip():
    class_mix = {"equity": 50.0, "debt": 30.0, "others": 20.0}
    asked = {
        "low_beta_equities": 25.0, "medium_beta_equities": 15.0,
        "us_equities": 10.0, "arbitrage_plus_income": 30.0,
        "gold_commodities": 20.0,
    }
    pins = [{"subgroup": sg, "pct_of_total": p} for sg, p in asked.items()]
    _, subgroup_pct, deployed = _run_sip(class_mix, pins, corpus=5_000_000.0)
    for sg, stated in asked.items():
        assert abs(subgroup_pct.get(sg, 0.0) - stated) <= TOLERANCE_PP, (
            f"{sg}: stated {stated}, realised {subgroup_pct.get(sg, 0.0):.3f}"
        )
    assert deployed == pytest.approx(SIP_INR, abs=200.0)


def test_case2_a_typed_zero_buys_nothing():
    class_mix = {"equity": 50.0, "debt": 30.0, "others": 20.0}
    asked = {
        "low_beta_equities": 50.0, "us_equities": 0.0,
        "arbitrage_plus_income": 30.0, "gold_commodities": 20.0,
    }
    pins = [{"subgroup": sg, "pct_of_total": p} for sg, p in asked.items()]
    _, subgroup_pct, _ = _run_sip(class_mix, pins, corpus=5_000_000.0)
    assert subgroup_pct.get("us_equities", 0.0) == 0.0
```

- [ ] **Step 2: Run it**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests/test_ainv_preference_split_e2e.py -v` → 5 PASS.
If a class misses by ~4pp, you summed raw subgroup buys instead of the rollup.

- [ ] **Step 3: Pin the equivalence the branch rests on**

Task 1's branch assumes *`human_override_applied is not None` ⟹ the carve-outs were suspended*. Those are two predicates in two files — `preference_set` (`practical_asset_allocation/pipeline.py:1156`) and `prefs.is_empty()` / the strict no-op (`human_override.py:85-89`, `:158`). If either is ever narrowed — say "an exclusion alone should not suspend the emergency reserve" — the SIP would target a `long_term` column that is no longer the full split, and **nothing in this plan would fail.**

In `AI_Agents/tests/test_carveout_suspension.py`, add `assert out.human_override_applied is not None` alongside the existing bucket assertions in `test_a_subgroup_preference_suspends_both_carve_outs` (`:83`) and `test_a_bare_class_only_preference_suspends_them_too` (`:102`), and add one exclusion-only case (`subgroup_emphasis={"gold_commodities": 0.0}`) asserting the same pair.

- [ ] **Step 4: Run**

Run: `.venv-mac/bin/python -m pytest AI_Agents/tests -q` → all PASS. Then the baseline command.

---

### Task 4: Make the context layer and two stale docstrings tell the truth

**Files:** the two `CLAUDE.md` files; `AI_Agents/tests/test_preference_propagation_e2e.py`; `ainv_engine/tests/test_service.py`.

A green test whose stated contract has gone false is worse than a red one. Two qualify.

- [ ] **Step 1: Rewrite `test_preference_moves_the_ainv_subgroup_split`** (`test_preference_propagation_e2e.py:61`)

Both assertions stay true; only the docstring's claim goes false. Rewrite it as what it is — an **engine** contract test: *`compute_targets` weights by whichever column it is handed; under a preference the long-term column is the stated split.*

Three things to get right: **do not** cite `ainv_engine/input_builder.py` as the production link — the test never imports it, and asserting an untested link is the same sin the rewrite exists to remove. **Do not** claim it "exercises the same bucket production selects" — false for the neutral `_mirrors_the_foundation(run_practical_allocation(make_practical_input()))` call at `:183`. **Keep** the note that ranked funds are synthetic so the assertion rides on `per_subgroup_target`. Also update the now-superseded inline comment at `:130-135` ("the CONTRACT is that AINV has no preference code of its own") — otherwise the file states the new truth at the top and the old claim in the middle. Consider renaming to `test_preference_reaches_the_long_term_column`.

- [ ] **Step 2: Rewrite `test_funded_sip_does_not_trigger_sized_fallback`** (`tests/test_service.py:476`)

Its docstring says the fallback "fires only on an empty plan, so funded/CAMS users are unaffected". After Task 1 there are two triggers and "funded" no longer means "has buys". Restate both triggers and rename to `test_funded_sip_with_a_real_corpus_does_not_trigger_sized_fallback`.

- [ ] **Step 3: The two CLAUDE.md clauses**

`app/domains/additional_investment/CLAUDE.md:16` — change `SIP keeps the legacy profile-corpus path.` to:

> SIP keeps the legacy profile-corpus path, targeting long-term when a preference shaped the plan (the preference suspends the carve-outs, so there is no near-term bucket to aim at — `ainv_engine/input_builder.py`); the sized re-derivation now also fires for a preference SIP under a ₹10,000 corpus.

Update `AINV_ENGINE_VERSION = "ainv-3.2.0"` on that same line to `"ainv-3.3.0"` — the third grep hit from Task 1 Step 9.

`AI_Agents/src/additional_investment/CLAUDE.md` — **leave the "Cadence doesn't change the ratio" bullet alone.** Appending "Cadence DOES change which bucket is targeted" to it is self-contradictory, and it is not cadence doing it: the engine reads two flags it is handed, and the app builder chooses them. Instead extend the adjacent **bucket-targeting** bullet with one clause naming the app builder as the chooser, with no app-layer file anchor — spec §6 lists the whole of `AI_Agents/src/` as unchanged and this doc should stay engine-shaped.

- [ ] **Step 4: Final run**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing AI_Agents/tests app/domains/additional_investment -q`
Report the count and every modified file. **Do not commit.**

---

## Existing tests: delete none

The founder asked specifically. The answer is that **no existing test loses its value** under this change:

- `AI_Agents/src/additional_investment/Testing/test_ratio.py` (`:17-67`) exercises `select_target_bucket` / `compute_targets`, untouched and still driving every no-preference SIP and both lumpsum paths.
- `test_input_builder.py`'s four flag tests (`:161-236`) all run LUMPSUM against a preference-free stub, so the new branch cannot fire in them.
- `test_service.py::test_sip_with_empty_target_bucket_still_names_funds` (`:431`) still guards the `not response.buys` arm, which survives for no-preference no-CAMS customers.
- `test_sizing_corpus_populates_the_target_bucket` (`:512`) still guards its constant.

Two need their **docstrings** rewritten (Task 4). Nothing gets deleted.

## Rejected approaches — do not retry

- **A `split_source` facts key with values `your_saved_split` / `your_class_split_our_categories`.** A third name for `active_preferences`, which both sibling chat modules already use. Facts *values* in this domain are third-person snake_case (`category_status`: `not_ranked`, `in_plan`, `plan_by_goals`); grepping `"your_` returns zero facts values anywhere.
- **Importing `load_human_override_for_user` into `chat.py`.** Breaches `app/domains/profile/CLAUDE.md:16`; chat modules are explicitly not on the sanctioned allow-list. It also returns `None` for a chat-only one-off, so it would fail *open* on the one path where the branch fires without a saved row.
- **Lowering `_under_deploy_note`'s suppression threshold.** It is shared by both cadences and both facts-pack call sites (`chat.py:646`, `:729`) and takes no preference or cadence argument, so it changes no-preference and lumpsum behaviour — which spec §6 forbids. It also fires on genuine ₹100 rounding at small instalments, producing the false "per-fund caps or a shortage of eligible funds" explanation. The small-SIP problem it chases is removed entirely by the Invest-page minimum in spec §12.1.
- **Carrying the true flag values through the builder debug dict.** Spec §6 lists the debug dict as unchanged, and the record is honest the moment it says the flags were forced and why.
- **A shared `conftest.py` for the `ainv_engine/tests/` directory.** No such file exists and all twelve test files keep private helpers. Extend `_fake_alloc()` / `_alloc()` in place.
- **`goal_flags_forced` / `goal_flags_actual`.** "goal flags" is a new noun; the helper is `_goal_funding_flags` and the fields are `*_fulfilled`.

## Founder decisions still open (spec §12)

Neither blocks this plan: the Invest-page **minimum monthly SIP** (§12.1 — a ₹5,000 floor caps the class error at ~1.3pp and removes the whole small-instalment family), and the **Invest-page copy** check, since `SipPlanResponse.target_bucket` flips to `long_term` for these customers and the frontend narrates a label from it.

## Interaction to check before starting

`docs/superpowers/plans/2026-09-20-subgroup-fund-count-selection.md` changes how many funds a subgroup splits across in the **rebalancing** engine, and the SIP mirrors the latest rebalancing run's BUY ISINs (`ainv_engine/service.py:238-258`). If it has landed, re-measure Task 3's fidelity numbers before trusting them.
