"""latest_buy_trades_by_subgroup over real sqlite tables (spec 2026-07-05)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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


async def test_sip_excludes_unsaved_candidate(db_session):
    # A newer tilt the customer VIEWED but didn't save must not shift their SIP.
    user = uuid.uuid4()
    plain = await _seed(db_session, _run(user, T0), [
        _trade(None, "large_cap_equities", "PLAIN01", 1000),
    ])
    await _seed(db_session, _run(user, T0 + timedelta(days=1), origin="candidate"), [
        _trade(None, "large_cap_equities", "CAND01", 9000),
    ])
    result = await latest_buy_trades_by_subgroup(db_session, user)
    assert result is not None
    run_id, by_sg = result
    assert run_id == plain  # the committed plain run, not the newer candidate
    assert by_sg == {"large_cap_equities": ["PLAIN01"]}


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
