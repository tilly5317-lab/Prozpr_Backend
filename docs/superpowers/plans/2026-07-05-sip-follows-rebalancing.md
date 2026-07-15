# SIP Follows the Rebalancing Plan — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SIP fund selection mirrors the BUY trades of the customer's latest persisted rebalancing run (equal split per subgroup, rank-1 fallback, no caps), per spec `docs/superpowers/specs/2026-07-05-sip-follows-rebalancing-design.md`.

**Architecture:** A new pure selector `select_funds_sip` in the engine (`AI_Agents/src/additional_investment`), fed by a new optional input field. The app layer gains one read function in the rebalancing domain (latest run's BUY ISINs by subgroup) that `ainv_engine/service.py` calls on the SIP path only and forwards through the input builder. No formatter change. Persistence unchanged except the `sip_rebal_run_id` telemetry string.

**Tech Stack:** Python 3.12, pydantic v2, SQLAlchemy async, pytest (`asyncio_mode=auto`).

## Global Constraints

- Run all tests with `.venv-mac/bin/python -m pytest` from `Prozpr_Backend/` (the `.venv/` dir is a Windows venv — never use it).
- **No git commits.** The user commits themselves — leave all changes in the working tree (standing preference). Where a template would say "commit", verify tests instead.
- Engine purity: nothing under `AI_Agents/src/additional_investment/` may import from `app/` or do I/O.
- Money is plain `float` rupees in this domain (allocation family) — do NOT introduce `Decimal`.
- `FundBuy.reason` strings are contractual for this feature: `"Matches your rebalancing plan"` (mirrored) and `"Top-ranked fund for this category"` (fallback). Persisted for audit only — the chat facts pack deliberately drops `reason`; make NO formatter/chat.py change.
- Accepted trade-offs (spec §Accepted trade-offs) are pinned by characterization tests, not "fixed": independent-rounding overshoot, run status ignored, single-₹0-buy cases.
- The engine's `Testing/` dir is gitignored but is the live pytest suite — add tests there as usual.

---

### Task 1: Engine selector `select_funds_sip`

**Files:**
- Modify: `AI_Agents/src/additional_investment/selection.py` (append new function)
- Test (create): `AI_Agents/src/additional_investment/Testing/test_selection_sip.py`

**Interfaces:**
- Consumes: existing `_round_to_multiple`, models `RankedFund`, `SubgroupTarget`, `FundBuy` (all already in `additional_investment.models` / `selection.py`).
- Produces: `select_funds_sip(targets: list[SubgroupTarget], ranked_funds: list[RankedFund], rebal_buy_isins_by_subgroup: dict[str, list[str]] | None, rounding_multiple: int) -> list[FundBuy]` — Task 2's pipeline branch calls exactly this signature.

- [ ] **Step 1: Write the failing tests**

Create `AI_Agents/src/additional_investment/Testing/test_selection_sip.py`:

```python
from __future__ import annotations

from additional_investment.models import RankedFund, SubgroupTarget
from additional_investment.selection import select_funds_sip


def _rf(subgroup: str, rank: int, isin: str) -> RankedFund:
    return RankedFund(
        asset_subgroup=subgroup, sub_category=f"{subgroup} fund", rank=rank,
        isin=isin, scheme_code=f"S-{isin}", recommended_fund=f"Fund {isin}",
    )


RANKED = [
    _rf("large_cap_equities", 1, "INF001"),
    _rf("large_cap_equities", 2, "INF002"),
    _rf("large_cap_equities", 3, "INF003"),
    _rf("short_debt", 1, "INF010"),
    _rf("short_debt", 2, "INF011"),
]


def _t(subgroup: str, target: float) -> SubgroupTarget:
    return SubgroupTarget(subgroup=subgroup, ratio=1.0, target_inr=target)


def test_equal_split_across_rebal_buys_preserving_order():
    buys = select_funds_sip(
        [_t("large_cap_equities", 9000)], RANKED,
        {"large_cap_equities": ["INF002", "INF003", "INF001"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [
        ("INF002", 3000.0), ("INF003", 3000.0), ("INF001", 3000.0)
    ]
    assert all(b.reason == "Matches your rebalancing plan" for b in buys)
    assert all(b.asset_subgroup == "large_cap_equities" for b in buys)


def test_duplicate_isins_deduped_first_occurrence_wins():
    buys = select_funds_sip(
        [_t("large_cap_equities", 9000)], RANKED,
        {"large_cap_equities": ["INF002", "INF002", "INF001"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [
        ("INF002", 4500.0), ("INF001", 4500.0)
    ]


def test_wrong_subgroup_isin_dropped_per_subgroup_match():
    # INF010 is ranked, but under short_debt — a large_cap candidate list must
    # not pick it up (spec 6a: per-subgroup match, not global presence).
    buys = select_funds_sip(
        [_t("large_cap_equities", 9000)], RANKED,
        {"large_cap_equities": ["INF010", "INF002"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [("INF002", 9000.0)]


def test_all_candidates_stale_falls_back_to_rank1_whole_share():
    buys = select_funds_sip(
        [_t("large_cap_equities", 9000)], RANKED,
        {"large_cap_equities": ["INF999"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [("INF001", 9000.0)]
    assert buys[0].reason == "Top-ranked fund for this category"


def test_no_rebal_entry_and_none_map_fall_back_to_rank1():
    for rebal in (None, {}):
        buys = select_funds_sip([_t("short_debt", 5000)], RANKED, rebal, 100)
        assert [(b.isin, b.amount_inr) for b in buys] == [("INF010", 5000.0)]
        assert buys[0].reason == "Top-ranked fund for this category"


def test_no_ranked_funds_for_subgroup_emits_no_buy():
    # Defensive guard (spec 6d): unreachable in production, must not crash.
    buys = select_funds_sip([_t("sector_equities", 5000)], RANKED, None, 100)
    assert buys == []


def test_no_caps_single_candidate_takes_entire_large_share():
    buys = select_funds_sip(
        [_t("large_cap_equities", 50000)], RANKED,
        {"large_cap_equities": ["INF001"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [("INF001", 50000.0)]


def test_dust_consolidation_into_first_candidate():
    # 120/3 = 40 -> rounds to 0 (< 100): whole rounded target (100) to the FIRST
    # candidate instead of a spray of Rs 0 rows (spec 6b dust rule).
    buys = select_funds_sip(
        [_t("large_cap_equities", 120)], RANKED,
        {"large_cap_equities": ["INF003", "INF001", "INF002"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [("INF003", 100.0)]


def test_overshoot_characterization_shares_round_up_independently():
    # ACCEPTED trade-off (spec §Accepted trade-offs): 250/3 = 83.33 -> each
    # rounds to 100 -> deployed 300 > 250. Pin it so a "fix" is a loud diff.
    buys = select_funds_sip(
        [_t("large_cap_equities", 250)], RANKED,
        {"large_cap_equities": ["INF001", "INF002", "INF003"]}, 100,
    )
    assert [b.amount_inr for b in buys] == [100.0, 100.0, 100.0]


def test_sub_50_target_emits_single_zero_buy():
    # ACCEPTED trade-off: whole-target round of 0 still emits one Rs 0 row.
    assert [
        (b.isin, b.amount_inr)
        for b in select_funds_sip([_t("short_debt", 40)], RANKED, None, 100)
    ] == [("INF010", 0.0)]
    assert [
        (b.isin, b.amount_inr)
        for b in select_funds_sip(
            [_t("short_debt", 40)], RANKED, {"short_debt": ["INF011"]}, 100
        )
    ] == [("INF011", 0.0)]


def test_multiple_targets_processed_independently():
    buys = select_funds_sip(
        [_t("large_cap_equities", 6000), _t("short_debt", 4000)], RANKED,
        {"large_cap_equities": ["INF002", "INF001"]}, 100,
    )
    assert [(b.isin, b.amount_inr) for b in buys] == [
        ("INF002", 3000.0), ("INF001", 3000.0), ("INF010", 4000.0)
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_selection_sip.py -v`
Expected: FAIL with `ImportError: cannot import name 'select_funds_sip'`

- [ ] **Step 3: Implement `select_funds_sip`**

Append to `AI_Agents/src/additional_investment/selection.py` (after `select_funds`):

```python
def select_funds_sip(
    targets: list[SubgroupTarget],
    ranked_funds: list[RankedFund],
    rebal_buy_isins_by_subgroup: dict[str, list[str]] | None,
    rounding_multiple: int,
) -> list[FundBuy]:
    """SIP-only selection (spec 2026-07-05): mirror the rebalancing plan's BUY funds.

    Per subgroup target: the caller-supplied rebalancing BUY ISINs (latest
    persisted run, ordered by BUY amount desc) that exist in THIS subgroup's own
    ranked list get an equal split of the target; no candidates -> the whole
    target goes to the subgroup's rank-1 fund. NO per-fund caps on this path.
    Each amount rounds to the nearest ``rounding_multiple`` independently — the
    resulting drift (overshoot/undershoot vs the target) is an accepted
    trade-off, as is the single Rs 0 buy a sub-Rs 50 target produces.
    """
    ranked_by_sg: dict[str, list[RankedFund]] = {}
    for f in ranked_funds:
        ranked_by_sg.setdefault(f.asset_subgroup, []).append(f)
    for fl in ranked_by_sg.values():
        fl.sort(key=lambda x: x.rank)

    rebal = rebal_buy_isins_by_subgroup or {}
    buys: list[FundBuy] = []
    for t in targets:
        sg_funds = ranked_by_sg.get(t.subgroup, [])
        if not sg_funds:
            # No ranked funds for this subgroup: no buy — the amount surfaces in
            # undeployed_inr. Defensive: unreachable in production (every
            # producible subgroup carries ranked funds), but must not crash.
            continue
        by_isin = {f.isin: f for f in sg_funds}
        candidates: list[RankedFund] = []
        for isin in rebal.get(t.subgroup, []):
            fund = by_isin.get(isin)  # per-subgroup match: stale/wrong-subgroup ISINs drop out
            if fund is not None and fund not in candidates:
                candidates.append(fund)

        if candidates:
            reason = "Matches your rebalancing plan"
            share = _round_to_multiple(
                t.target_inr / len(candidates), rounding_multiple
            )
            if share >= rounding_multiple:
                chosen = [(f, share) for f in candidates]
            else:
                # Dust consolidation: equal shares would round to zero — the
                # whole rounded target goes to the first candidate instead.
                chosen = [
                    (candidates[0], _round_to_multiple(t.target_inr, rounding_multiple))
                ]
        else:
            reason = "Top-ranked fund for this category"
            chosen = [(sg_funds[0], _round_to_multiple(t.target_inr, rounding_multiple))]

        for fund, amount in chosen:
            buys.append(FundBuy(
                recommended_fund=fund.recommended_fund,
                isin=fund.isin,
                sub_category=fund.sub_category,
                asset_subgroup=t.subgroup,
                amount_inr=amount,
                reason=reason,
            ))
    return buys
```

Also update the module docstring's first line to mention both selectors, e.g.:
`"""BUY-only fund selection from the ranking. Pure, no state, no I/O.` … append one sentence: `select_funds` (lumpsum: caps + rank-spill) and `select_funds_sip` (SIP: mirror rebalancing BUYs / rank-1 fallback, no caps).`

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_selection_sip.py -v`
Expected: all PASS. Then run the whole engine suite to prove no regression:
`.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing -v`
Expected: all PASS (nothing existing touches the new function yet).

---

### Task 2: Engine input field + pipeline branch

**Files:**
- Modify: `AI_Agents/src/additional_investment/models.py` (add one field to `AdditionalInvestmentInput`, after `exclude_subgroups`, ~line 91)
- Modify: `AI_Agents/src/additional_investment/pipeline.py:35-45` (cadence branch)
- Test (modify): `AI_Agents/src/additional_investment/Testing/test_pipeline.py`

**Interfaces:**
- Consumes: `select_funds_sip` from Task 1.
- Produces: `AdditionalInvestmentInput.rebal_buy_isins_by_subgroup: Optional[dict[str, list[str]]] = None` — Tasks 4/5 set this field from the app layer.

- [ ] **Step 1: Write the failing tests**

Append to `AI_Agents/src/additional_investment/Testing/test_pipeline.py`:

```python
def _two_fund_ranking():
    return [
        RankedFund(asset_subgroup="large_cap_equities", sub_category="Large Cap Fund", rank=1, isin="INF001", scheme_code="L1", recommended_fund="Alpha Large Cap"),
        RankedFund(asset_subgroup="large_cap_equities", sub_category="Large Cap Fund", rank=2, isin="INF002", scheme_code="L2", recommended_fund="Beta Large Cap"),
        RankedFund(asset_subgroup="short_debt", sub_category="Short Duration Fund", rank=1, isin="INF010", scheme_code="D1", recommended_fund="Alpha Short Debt"),
    ]


def test_sip_mirrors_rebal_buys_ignoring_caps():
    # SIP routes through select_funds_sip: the rebal-mirrored rank-2 fund takes
    # the WHOLE large-cap share even with a 1% cap (caps are lumpsum-only).
    inp = _input(Cadence.SIP_MONTHLY, 40000, default_cap_pct=1.0).model_copy(
        update={
            "ranked_funds": _two_fund_ranking(),
            "rebal_buy_isins_by_subgroup": {"large_cap_equities": ["INF002"]},
        }
    )
    out = run_additional_investment(inp)
    by = {b.isin: b for b in out.buys}
    # long_term weights 300:100 -> large 30000 / short_debt 10000
    assert by["INF002"].amount_inr == 30000
    assert by["INF002"].reason == "Matches your rebalancing plan"
    assert by["INF002"].monthly_amount_inr == 30000
    # short_debt has no rebal BUYs -> rank-1 fallback, whole share
    assert by["INF010"].amount_inr == 10000
    assert by["INF010"].reason == "Top-ranked fund for this category"
    assert out.deployed_inr == 40000
    assert out.undeployed_inr == 0


def test_sip_without_rebal_map_uses_rank1_fallback():
    inp = _input(Cadence.SIP_MONTHLY, 40000, default_cap_pct=1.0).model_copy(
        update={"ranked_funds": _two_fund_ranking()}
    )
    out = run_additional_investment(inp)
    assert {b.isin for b in out.buys} == {"INF001", "INF010"}
    assert all(b.reason == "Top-ranked fund for this category" for b in out.buys)


def test_lumpsum_ignores_rebal_map_and_keeps_caps():
    # LUMPSUM still routes through select_funds: the 30% deposit cap binds and
    # the rebal map is ignored (spec: SIP-only field).
    inp = _input(Cadence.LUMPSUM, 100000, default_cap_pct=30.0).model_copy(
        update={
            "subgroups": [SubgroupBucketAmounts(subgroup="large_cap_equities", long_term=300, total=300)],
            "ranked_funds": [_two_fund_ranking()[0]],
            "rebal_buy_isins_by_subgroup": {"large_cap_equities": ["INF002"]},
        }
    )
    out = run_additional_investment(inp)
    assert [(b.isin, b.amount_inr) for b in out.buys] == [("INF001", 30000)]
    assert out.undeployed_inr == 70000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing/test_pipeline.py -v`
Expected: the three new tests FAIL (`ValidationError: rebal_buy_isins_by_subgroup — extra fields not permitted` or attribute error); existing tests still pass.

- [ ] **Step 3: Add the model field**

In `AI_Agents/src/additional_investment/models.py`, inside `AdditionalInvestmentInput` directly after the `exclude_subgroups` field:

```python
    # SIP-only (spec 2026-07-05): BUY-trade ISINs of the customer's latest
    # persisted rebalancing run, keyed by asset subgroup, ordered by BUY amount
    # desc. The SIP selector mirrors these funds (equal split per subgroup,
    # no caps); a missing subgroup — or None — falls back to that subgroup's
    # rank-1 ranked fund. Ignored on the LUMPSUM paths.
    rebal_buy_isins_by_subgroup: Optional[dict[str, list[str]]] = None
```

- [ ] **Step 4: Branch the pipeline**

In `AI_Agents/src/additional_investment/pipeline.py`, replace the current selection + SIP framing block (lines 35-45):

```python
    if inp.cadence is Cadence.SIP_MONTHLY:
        # SIP mirrors the latest rebalancing plan's BUY funds (spec 2026-07-05):
        # equal split per subgroup, rank-1 fallback, NO per-fund caps.
        buys = select_funds_sip(
            targets,
            inp.ranked_funds,
            inp.rebal_buy_isins_by_subgroup,
            inp.rounding_multiple_inr,
        )
        # deploy_amount_inr is the MONTHLY amount; per-fund amounts are monthly.
        buys = [b.model_copy(update={"monthly_amount_inr": b.amount_inr}) for b in buys]
    else:
        buys = select_funds(
            targets,
            inp.ranked_funds,
            inp.deploy_amount_inr,
            inp.cap_pct_by_subgroup,
            inp.default_cap_pct,
            inp.rounding_multiple_inr,
        )
```

and update the import at the top: `from .selection import select_funds, select_funds_sip`.

- [ ] **Step 5: Run the full engine suite**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing -v`
Expected: all PASS. Note: `test_sip_sets_monthly_amount` and `test_sip_ignores_current_map_entirely` now exercise the rank-1 fallback path — their fixtures are rank-1-only with non-binding caps, so amounts are unchanged. If any assertion on `reason` text fails, the expected new SIP reason is `"Top-ranked fund for this category"`.

---

### Task 3: Rebalancing read function

**Files:**
- Create: `app/domains/rebalancing/services/rebalancing_read_service.py`
- Test (create): `app/domains/rebalancing/services/tests/test_rebalancing_read_service.py`

**Interfaces:**
- Consumes: ORM models `RebalancingRun` (`app/domains/rebalancing/models/rebalancing_run.py:66`), `RebalancingTrade` + `TradeAction` (`app/domains/rebalancing/models/rebalancing_trade.py:33,46`).
- Produces: `async def latest_buy_trades_by_subgroup(db: AsyncSession, user_id: uuid.UUID) -> Optional[tuple[uuid.UUID, dict[str, list[str]]]]` — Task 5 imports this from `app.domains.rebalancing.services.rebalancing_read_service`.

- [ ] **Step 1: Write the failing tests**

Create `app/domains/rebalancing/services/tests/test_rebalancing_read_service.py`:

```python
"""latest_buy_trades_by_subgroup over real sqlite tables (spec 2026-07-05)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables with Base.metadata
from app.domains.rebalancing.models.rebalancing_run import (
    RebalancingRun,
    RebalancingRunStatus,
    TaxRegime,
)
from app.domains.rebalancing.models.rebalancing_trade import (
    RebalancingTrade,
    TradeAction,
)
from app.domains.rebalancing.services.rebalancing_read_service import (
    latest_buy_trades_by_subgroup,
)

T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Base.metadata.create_all FAILS on sqlite (unrelated Postgres ARRAY
        # model) — create only the tables under test.
        await conn.run_sync(RebalancingRun.__table__.create)
        await conn.run_sync(RebalancingTrade.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _run(user_id: uuid.UUID, created_at: datetime, **overrides) -> RebalancingRun:
    kwargs = dict(
        user_id=user_id,
        portfolio_id=uuid.uuid4(),
        source_allocation_run_id=uuid.uuid4(),
        engine_request_id=uuid.uuid4(),
        engine_version="rebal-test",
        computed_at=created_at,
        tax_regime=TaxRegime.new,
        effective_tax_rate_pct=30,
        total_corpus=1_000_000,
        created_at=created_at,
    )
    kwargs.update(overrides)
    return RebalancingRun(**kwargs)


def _trade(run_id, subgroup, isin, amount, action=TradeAction.BUY) -> RebalancingTrade:
    return RebalancingTrade(
        run_id=run_id, isin=isin, recommended_fund=f"Fund {isin}",
        asset_subgroup=subgroup, sub_category="X", action=action,
        amount_inr=amount, reason_code="rc", reason_title="rt", reason_text="body",
    )


async def _seed(db, run, trades):
    db.add(run)
    await db.flush()
    for t in trades:
        t.run_id = run.id
        db.add(t)
    await db.flush()
    return run.id


async def test_latest_run_buys_grouped_ordered_amount_desc(db_session):
    user = uuid.uuid4()
    old = await _seed(db_session, _run(user, T0), [
        _trade(None, "large_cap_equities", "OLD001", 5000),
    ])
    new = await _seed(db_session, _run(user, T0 + timedelta(days=1)), [
        _trade(None, "large_cap_equities", "INF002", 2000),
        _trade(None, "large_cap_equities", "INF001", 8000),
        _trade(None, "short_debt", "INF010", 3000),
    ])
    result = await latest_buy_trades_by_subgroup(db_session, user)
    assert result is not None
    run_id, by_sg = result
    assert run_id == new and run_id != old
    assert by_sg == {
        "large_cap_equities": ["INF001", "INF002"],  # amount desc
        "short_debt": ["INF010"],
    }


async def test_only_buy_actions_count_and_dupes_dedupe(db_session):
    user = uuid.uuid4()
    await _seed(db_session, _run(user, T0), [
        _trade(None, "large_cap_equities", "INF001", 8000),
        _trade(None, "large_cap_equities", "INF001", 100),  # dupe ISIN
        _trade(None, "large_cap_equities", "INF002", 9000, action=TradeAction.SELL),
        _trade(None, "short_debt", "INF010", 500, action=TradeAction.EXIT),
    ])
    result = await latest_buy_trades_by_subgroup(db_session, user)
    assert result is not None
    assert result[1] == {"large_cap_equities": ["INF001"]}


async def test_no_runs_returns_none(db_session):
    assert await latest_buy_trades_by_subgroup(db_session, uuid.uuid4()) is None


async def test_latest_run_with_zero_buys_returns_none(db_session):
    # Zero-BUY latest run means "rank-1 fallback ran" — telemetry must not
    # stamp a run id (spec step 1 / audit F4).
    user = uuid.uuid4()
    await _seed(db_session, _run(user, T0), [
        _trade(None, "large_cap_equities", "INF001", 9000, action=TradeAction.SELL),
    ])
    assert await latest_buy_trades_by_subgroup(db_session, user) is None


async def test_rejected_status_still_counts(db_session):
    # Product call (spec): all plans treated as accepted — status ignored.
    user = uuid.uuid4()
    rid = await _seed(
        db_session,
        _run(user, T0, status=RebalancingRunStatus.rejected),
        [_trade(None, "short_debt", "INF010", 3000)],
    )
    result = await latest_buy_trades_by_subgroup(db_session, user)
    assert result is not None and result[0] == rid


async def test_other_users_runs_invisible(db_session):
    await _seed(db_session, _run(uuid.uuid4(), T0), [
        _trade(None, "short_debt", "INF010", 3000),
    ])
    assert await latest_buy_trades_by_subgroup(db_session, uuid.uuid4()) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_rebalancing_read_service.py -v`
Expected: FAIL with `ModuleNotFoundError: ... rebalancing_read_service`

- [ ] **Step 3: Implement the read service**

Create `app/domains/rebalancing/services/rebalancing_read_service.py`:

```python
"""Read-side helpers over persisted rebalancing runs.

One consumer today: the additional-investment SIP path mirrors the BUY trades
of the customer's latest persisted rebalancing run (spec 2026-07-05). Lives in
the rebalancing domain because it queries this domain's tables; the ainv
service imports it (same direction as the existing fund_rank import — the
rebalancing domain never imports additional_investment, so no cycle).
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.rebalancing.models.rebalancing_run import RebalancingRun
from app.domains.rebalancing.models.rebalancing_trade import (
    RebalancingTrade,
    TradeAction,
)


async def latest_buy_trades_by_subgroup(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[tuple[uuid.UUID, dict[str, list[str]]]]:
    """``(run_id, subgroup -> BUY ISINs)`` from the user's latest rebalancing run.

    ``user_id`` is the acting (effective) user — the same identity the ainv
    path persists under, so a family member never sources funds from the
    primary account's run. The latest run is by ``created_at`` desc; status is
    deliberately ignored (product call, spec 2026-07-05: all plans are treated
    as accepted). ISINs within a subgroup are ordered by BUY ``amount_inr``
    desc and deduped (first occurrence wins). Returns None when the user has
    no run, or the latest run has no BUY trades — both mean "rank-1 fallback",
    and the caller must not stamp ``sip_rebal_run_id``.
    """
    run_id = (
        await db.execute(
            select(RebalancingRun.id)
            .where(RebalancingRun.user_id == user_id)
            .order_by(RebalancingRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if run_id is None:
        return None

    rows = (
        await db.execute(
            select(RebalancingTrade.asset_subgroup, RebalancingTrade.isin)
            .where(
                RebalancingTrade.run_id == run_id,
                RebalancingTrade.action == TradeAction.BUY,
            )
            .order_by(RebalancingTrade.amount_inr.desc())
        )
    ).all()

    by_subgroup: dict[str, list[str]] = {}
    for subgroup, isin in rows:
        bucket = by_subgroup.setdefault(subgroup, [])
        if isin not in bucket:
            bucket.append(isin)
    if not by_subgroup:
        return None
    return run_id, by_subgroup
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv-mac/bin/python -m pytest app/domains/rebalancing/services/tests/test_rebalancing_read_service.py -v`
Expected: all PASS.

---

### Task 4: Input-builder passthrough

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/input_builder.py:88-95` (signature) and `:152-165` (`AdditionalInvestmentInput(...)` construction)
- Test (modify): `app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py`

**Interfaces:**
- Consumes: `AdditionalInvestmentInput.rebal_buy_isins_by_subgroup` (Task 2).
- Produces: `build_additional_investment_input_for_user(..., rebal_buy_isins_by_subgroup: dict[str, list[str]] | None = None)` — Task 5 passes this kwarg.

- [ ] **Step 1: Write the failing test**

Append to `app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py`, reusing the module's `_ctx()`, `_alloc()`, `_Row`, `_patch()` stand-ins (see the top of that file):

```python
@pytest.mark.asyncio
async def test_rebal_buys_passthrough_and_default(monkeypatch):
    from additional_investment.models import Cadence

    _patch(monkeypatch, ranking={}, goals=())
    rows = [_Row(subgroup="large_cap_equities", long_term=100.0, total=100.0)]
    rebal_map = {"large_cap_equities": ["INF001"]}

    inp, _ = await build_additional_investment_input_for_user(
        _ctx(), _alloc(rows),
        deploy_amount_inr=5000.0, cadence=Cadence.SIP_MONTHLY,
        rebal_buy_isins_by_subgroup=rebal_map,
    )
    assert inp.rebal_buy_isins_by_subgroup == rebal_map

    inp2, _ = await build_additional_investment_input_for_user(
        _ctx(), _alloc(rows),
        deploy_amount_inr=5000.0, cadence=Cadence.SIP_MONTHLY,
    )
    assert inp2.rebal_buy_isins_by_subgroup is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py::test_rebal_buys_passthrough_and_default -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'rebal_buy_isins_by_subgroup'`

- [ ] **Step 3: Implement the passthrough**

In `build_additional_investment_input_for_user`, add the keyword-only parameter after `current_value_by_subgroup`:

```python
    current_value_by_subgroup: dict[str, float] | None = None,
    rebal_buy_isins_by_subgroup: dict[str, list[str]] | None = None,
```

and forward it in the `AdditionalInvestmentInput(...)` construction after `current_value_by_subgroup=...`:

```python
        # SIP-only: latest rebalancing run's BUY ISINs per subgroup (None on
        # lumpsum and when the read found nothing — engine falls back to rank-1).
        rebal_buy_isins_by_subgroup=rebal_buy_isins_by_subgroup,
```

- [ ] **Step 4: Run the builder suite**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_input_builder.py -v`
Expected: all PASS.

---

### Task 5: Service wiring, engine-version bump, telemetry

**Files:**
- Modify: `app/domains/additional_investment/services/ainv_engine/service.py` (import; `AINV_ENGINE_VERSION`; SIP read block; builder kwarg; `request_extras`)
- Test (modify): `app/domains/additional_investment/services/ainv_engine/tests/test_service.py` (update `test_sip_takes_no_snapshot_and_no_pin`; add three tests)
- Test (modify): `app/domains/additional_investment/services/ainv_engine/tests/test_persist.py:166` (version assertion)
- Test (modify): `app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py` (extras assertion — adapt to that file's existing persist-mock fixtures)

**Interfaces:**
- Consumes: `latest_buy_trades_by_subgroup` (Task 3), builder kwarg (Task 4).
- Produces: no new public surface; `request_extras["sip_rebal_run_id"]: str` in the persisted `request_input`.

- [ ] **Step 1: Write the failing tests**

In `test_service.py` — reuse the module's existing `_fake_alloc`/`_fake_ainv_input`/`ib_cadence` helpers and its `patch.object(svc, ...)` style; note every existing patch context must now also patch `latest_buy_trades_by_subgroup` on SIP-path tests:

```python
@pytest.mark.asyncio
async def test_sip_passes_rebal_buys_to_builder():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    run_id = uuid.uuid4()
    rebal_map = {"low_beta_equities": ["INF000000001"]}
    paa_mock = AsyncMock(return_value=_fake_alloc())
    sip_input = _fake_ainv_input(25000.0).model_copy(
        update={"cadence": ib_cadence().SIP_MONTHLY}
    )
    builder_mock = AsyncMock(return_value=(sip_input, {}))
    read_mock = AsyncMock(return_value=(run_id, rebal_map))

    with patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=read_mock
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=25000.0,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    read_mock.assert_awaited_once()
    assert read_mock.call_args.args[1] == user.id  # acting user id
    assert builder_mock.call_args.kwargs["rebal_buy_isins_by_subgroup"] == rebal_map
    assert outcome.output is not None


@pytest.mark.asyncio
async def test_sip_rebal_read_failure_degrades_to_fallback_not_a_gate():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc())
    sip_input = _fake_ainv_input(25000.0).model_copy(
        update={"cadence": ib_cadence().SIP_MONTHLY}
    )
    builder_mock = AsyncMock(return_value=(sip_input, {}))
    read_mock = AsyncMock(side_effect=RuntimeError("db down"))

    with patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=read_mock
    ):
        outcome = await svc.compute_additional_investment_result(
            user, "start a sip", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=25000.0,
            cadence=Cadence.SIP_MONTHLY, chat_ctx=SimpleNamespace(), persist=False,
        )

    assert outcome.output is not None
    assert outcome.blocking_message is None
    assert builder_mock.call_args.kwargs["rebal_buy_isins_by_subgroup"] is None


@pytest.mark.asyncio
async def test_lumpsum_never_reads_rebalancing():
    from additional_investment.models import Cadence
    from app.domains.additional_investment.services.ainv_engine import service as svc

    user = SimpleNamespace(id=uuid.uuid4())
    paa_mock = AsyncMock(return_value=_fake_alloc())
    builder_mock = AsyncMock(return_value=(_fake_ainv_input(100000.0), {}))
    read_mock = AsyncMock()

    with patch.object(
        svc, "load_holdings_snapshot", new=_empty_snapshot_mock()
    ), patch.object(
        svc, "compute_practical_allocation_result", new=paa_mock
    ), patch.object(
        svc, "build_additional_investment_input_for_user", new=builder_mock
    ), patch.object(
        svc, "latest_buy_trades_by_subgroup", new=read_mock
    ):
        await svc.compute_additional_investment_result(
            user, "invest 1 lakh", db=SimpleNamespace(), acting_user_id=user.id,
            chat_session_id=None, deploy_amount_inr=100000.0,
            cadence=Cadence.LUMPSUM, chat_ctx=SimpleNamespace(), persist=False,
        )

    read_mock.assert_not_called()
    assert builder_mock.call_args.kwargs["rebal_buy_isins_by_subgroup"] is None
```

Update the existing `test_sip_takes_no_snapshot_and_no_pin` (test_service.py:295): add `patch.object(svc, "latest_buy_trades_by_subgroup", new=AsyncMock(return_value=None))` to its `with` block (the dummy `db=SimpleNamespace()` would otherwise hit the real query and `AttributeError`).

In `test_persist.py:166`, change the assertion to `assert run.engine_version == "ainv-3.0.0"` (comment stays).

In `test_service_persist.py`, add one test in the file's existing style (it patches the two persist functions and asserts their kwargs): run a SIP compute with `persist=True`, a `chat_session_id`, and `latest_buy_trades_by_subgroup` mocked to return `(run_id, {...})`; assert the `persist_additional_investment_recommendation` mock was called with `request_extras == {"sip_rebal_run_id": str(run_id)}`. Add a companion assertion (same or separate test) that with the read mocked to `None`, `request_extras` is `None` (SIP adds no extras).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_service.py app/domains/additional_investment/services/ainv_engine/tests/test_service_persist.py -v`
Expected: new tests FAIL (`AttributeError: ... has no attribute 'latest_buy_trades_by_subgroup'` on the patch target); `test_persist.py` version test FAILS (still 2.0.0).

- [ ] **Step 3: Implement the wiring**

In `app/domains/additional_investment/services/ainv_engine/service.py`:

(a) Import (with the other app-layer imports, before `ensure_ai_agents_path()`):

```python
from app.domains.rebalancing.services.rebalancing_read_service import (
    latest_buy_trades_by_subgroup,
)
```

(b) Version bump (line 80) — replace the constant and extend the comment:

```python
# 2.0.0: lumpsum deployments switched from single-bucket targeting to
# holdings-aware deficit fill (spec 2026-07-03); SIP unchanged.
# 3.0.0: SIP selection mirrors the latest persisted rebalancing run's BUY
# funds (equal split, rank-1 fallback, no caps) — spec 2026-07-05.
AINV_ENGINE_VERSION = "ainv-3.0.0"
```

(c) SIP read block — insert directly after the `paa_outcome.result is None` gate (after line ~171) and before the builder `try`:

```python
    # SIP mirrors the customer's latest rebalancing plan (spec 2026-07-05).
    # Enhancement, never a gate: any read failure degrades to the rank-1
    # fallback instead of blocking the recommendation.
    rebal_run_id: Optional[uuid.UUID] = None
    rebal_buys: Optional[dict[str, list[str]]] = None
    if cadence is Cadence.SIP_MONTHLY:
        try:
            rebal = await latest_buy_trades_by_subgroup(db, acting_user_id)
        except Exception:  # noqa: BLE001 — degrade, never gate
            logger.exception(
                "additional_investment: latest rebalancing-run read failed — "
                "falling back to rank-1 SIP selection"
            )
            rebal = None
        if rebal is not None:
            rebal_run_id, rebal_buys = rebal
            trace_line(
                f"additional_investment SIP mirrors rebalancing run {rebal_run_id}"
            )
```

(d) Builder call — add the kwarg:

```python
            current_value_by_subgroup=(
                snapshot.by_subgroup if snapshot is not None else None
            ),
            rebal_buy_isins_by_subgroup=rebal_buys,
```

(e) `request_extras` block — after the `focus_category` line:

```python
    if rebal_run_id is not None:
        # str(), not the raw UUID: request_extras merges into the request_input
        # JSONB and json.dumps cannot serialise UUID (audit F4 — the best-effort
        # persist except would swallow the failure silently).
        _extras["sip_rebal_run_id"] = str(rebal_run_id)
```

- [ ] **Step 4: Run the app-layer suites**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment -v`
Expected: all PASS (including the updated version assertion and the patched SIP test).

---

### Task 6: Persist round-trip through a real JSON column

**Files:**
- Test (create): `app/domains/additional_investment/services/ainv_engine/tests/test_persist_roundtrip.py`

**Interfaces:**
- Consumes: `persist_additional_investment_recommendation` (existing, unchanged), engine `run_additional_investment` + Task 2 field.

- [ ] **Step 1: Write the test (expected to pass — it guards the contract)**

```python
"""Persist round-trip through REAL sqlite tables (JSON columns included).

Guards the request_extras JSON-serialisability contract (spec 2026-07-05 /
audit F4): a raw UUID in request_extras breaks json.dumps at flush, and the
orchestrator's best-effort except would swallow it — so this must round-trip
through a real column, not the suite's usual fake-db stand-ins.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401
from app.domains.ai_engine.common import ensure_ai_agents_path
from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    AdditionalInvestmentTarget,
)
from app.domains.additional_investment.services import (
    additional_investment_persist_service as persist_mod,
)

ensure_ai_agents_path()

from additional_investment.models import (  # noqa: E402
    AdditionalInvestmentInput,
    Cadence,
    RankedFund,
    SubgroupBucketAmounts,
)
from additional_investment.pipeline import run_additional_investment  # noqa: E402


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(AdditionalInvestmentRun.__table__.create)
        await conn.run_sync(AdditionalInvestmentTarget.__table__.create)
        await conn.run_sync(AdditionalInvestmentBuy.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


async def test_sip_rebal_run_persists_through_real_json_column(db_session):
    inp = AdditionalInvestmentInput(
        deploy_amount_inr=10000.0,
        cadence=Cadence.SIP_MONTHLY,
        subgroups=[
            SubgroupBucketAmounts(
                subgroup="large_cap_equities", long_term=100.0, total=100.0
            )
        ],
        short_term_fulfilled=True,
        medium_term_fulfilled=True,
        ranked_funds=[
            RankedFund(
                asset_subgroup="large_cap_equities", sub_category="Large Cap Fund",
                rank=1, isin="INF001", scheme_code="L1",
                recommended_fund="Alpha Large Cap",
            )
        ],
        rebal_buy_isins_by_subgroup={"large_cap_equities": ["INF001"]},
    )
    output = run_additional_investment(inp)
    rebal_run_id = uuid.uuid4()

    with patch.object(
        persist_mod,
        "get_or_create_primary_portfolio",
        new=AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    ):
        run_id = await persist_mod.persist_additional_investment_recommendation(
            db_session,
            uuid.uuid4(),
            output,
            source_allocation_run_id=uuid.uuid4(),
            chat_session_id=None,
            user_question="start a sip of 10k",
            request=inp,
            request_extras={"sip_rebal_run_id": str(rebal_run_id)},
        )
        await db_session.commit()  # the real flush is where a raw UUID would blow up

    run = (
        await db_session.execute(
            select(AdditionalInvestmentRun).where(
                AdditionalInvestmentRun.id == run_id
            )
        )
    ).scalar_one()
    assert run.request_input["sip_rebal_run_id"] == str(rebal_run_id)
    assert run.request_input["rebal_buy_isins_by_subgroup"] == {
        "large_cap_equities": ["INF001"]
    }
    buys = (
        (
            await db_session.execute(
                select(AdditionalInvestmentBuy).where(
                    AdditionalInvestmentBuy.run_id == run_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert [(b.isin, b.rank, b.scheme_code) for b in buys] == [("INF001", 1, "L1")]
    assert buys[0].monthly_amount_inr == 10000
```

- [ ] **Step 2: Run it**

Run: `.venv-mac/bin/python -m pytest app/domains/additional_investment/services/ainv_engine/tests/test_persist_roundtrip.py -v`
Expected: PASS. If it fails with a UUID-serialisation error, a raw UUID leaked into `request_extras` — fix the caller (Task 5 step 3e), never the test.

---

### Task 7: Docs — logic doc v1.1 + CLAUDE.md refreshes

**Files:**
- Modify: `AI_Agents/Reference_docs/Logics_reference_docs/Additional_Investment.md`
- Modify: `AI_Agents/src/additional_investment/CLAUDE.md`
- Modify: `AI_Agents/src/CLAUDE.md` (one line)
- Modify: `app/domains/additional_investment/CLAUDE.md`
- Modify: `app/domains/rebalancing/CLAUDE.md` (one line)

- [ ] **Step 1: Update the customer-facing logic doc**

Read `Additional_Investment.md` in full first, then (matching its existing plain-English voice, no jargon, no internal file names):

1. Bump the version header (line 4) to `Thesis version 1.1` and the footer `v1.0` → `v1.1`.
2. Principle 3 (line ~22, "A SIP follows your goals; a lumpsum follows your holdings"): append a sentence — draft: *"Monthly money also flows into the same funds your latest portfolio-review plan is buying, so your SIP and your plan pull in the same direction."*
3. Step 2 "Pick the funds" (lines ~45-47): split by cadence. Keep the existing cap/overflow sentences but scope them to lumpsum. Add the SIP rule — draft: *"For a monthly SIP, each part of the plan flows into the funds your latest portfolio review is already buying there — split equally when there is more than one. Where your review isn't buying anything in that part, the money goes to our top-ranked fund for it. SIP amounts are rounded to clean figures, and we don't force a monthly amount to spread across many funds."*
4. "Lumpsum vs SIP" section (line ~55): echo the same SIP sentence.
5. Scan every remaining cap claim (incl. Principle 6) and scope any "no single fund dominates"-style sentence to lumpsum.

- [ ] **Step 2: Refresh the module CLAUDE.mds**

1. `AI_Agents/src/additional_investment/CLAUDE.md`:
   - Header purpose line: "…lumpsum-with-holdings fills allocation deficits; SIP follows the ideal mix" → "…SIP follows the ideal mix and mirrors the latest rebalancing run's BUY funds".
   - Entry/contract input bullet: add `optional rebal_buy_isins_by_subgroup (SIP mirror)`.
   - Gotcha "**Fund selection is holding-agnostic.**": scope to lumpsum and add: `SIP (select_funds_sip): mirrors caller-supplied rebalancing BUY ISINs per subgroup (equal split, per-subgroup ISIN match), rank-1 fallback, NO caps; dust consolidates into the first candidate (selection.py).`
   - Gotcha "**Per-fund cap keys off the DEPLOY amount…**": append `(lumpsum only — the SIP selector applies no caps)`.
2. `AI_Agents/src/CLAUDE.md`, `additional_investment` module line: "SIP follows the ideal mix" → "SIP follows the ideal mix into the latest rebalancing run's BUY funds".
3. `app/domains/additional_investment/CLAUDE.md`:
   - Update the `AINV_ENGINE_VERSION = "ainv-2.0.0"` mention to `"ainv-3.0.0"`.
   - Add a gotcha: `**SIP mirrors the latest rebalancing run.** ainv_engine/service.py reads BUY ISINs via rebalancing_read_service.latest_buy_trades_by_subgroup (acting user; status ignored — product call); enhancement never gate: read failure degrades to rank-1 fallback; sip_rebal_run_id (str) lands in request_extras only when a run sourced funds.`
4. `app/domains/rebalancing/CLAUDE.md`, services layer bullet: add `rebalancing_read_service (read surface: latest run's BUY trades by subgroup — consumed by the additional_investment SIP path)`.

- [ ] **Step 3: Full verification**

Run: `.venv-mac/bin/python -m pytest AI_Agents/src/additional_investment/Testing app/domains/additional_investment app/domains/rebalancing/services/tests -v`
Expected: all PASS. No prompt text changed anywhere (formatter untouched), so the prompt eval gate is not required for this change.

---

## Plan self-review (done at write time)

- **Spec coverage:** steps 1-3 → Task 3/5; 4-7 → Tasks 1/2; 8-10 → Tasks 5/6; 11 → no-op by design (constraint recorded in Global Constraints); 12 → Task 7; 13-15 → Tasks 1-6 test steps + Task 7 step 3.
- **Accepted trade-offs** pinned as characterization tests (Task 1 overshoot/₹0; Task 3 rejected-status test).
- **Type consistency:** `select_funds_sip` signature identical in Task 1 (def), Task 2 (call); builder kwarg name `rebal_buy_isins_by_subgroup` identical across Tasks 2/4/5; read return `tuple[uuid.UUID, dict[str, list[str]]] | None` consistent across Tasks 3/5.
