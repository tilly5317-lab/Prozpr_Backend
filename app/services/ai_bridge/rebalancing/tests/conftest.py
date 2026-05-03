"""Async DB fixtures for rebalancing tests.

The repo has no shared async-DB fixture infrastructure (existing tests under
``app/services/ai_bridge/`` rely on mocks), so this conftest stands up a
fully isolated per-test in-memory SQLite engine. ``Base.metadata.create_all``
materialises the schema; ``app.database``'s ``@compiles(JSONB, "sqlite")``
shim makes Postgres-only column types portable. Each test gets a fresh
engine + session, disposed at teardown — no cross-test pollution and no
contact with the local dev ``wealth_agent.db``.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import AsyncIterator, Awaitable, Callable

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect imports: register every model with ``Base.metadata`` so
# ``create_all`` materialises the entire schema (FK targets like ``users`` and
# ``mf_fund_metadata`` must exist before children are created).
import app.models  # noqa: F401  -- registers all ORM tables with Base.metadata
from app.database import Base
from app.models.mf.enums import (
    MfOptionType,
    MfPlanType,
    MfTransactionSource,
    MfTransactionType,
)
from app.models.mf.mf_fund_metadata import MfFundMetadata
from app.models.mf.mf_nav_history import MfNavHistory
from app.models.mf.mf_transaction import MfTransaction
from app.models.profile.tax_profile import TaxProfile
from app.models.user import User


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-test in-memory SQLite session; engine disposed at teardown."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def fixture_user(db_session: AsyncSession) -> User:
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_test_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def fixture_buy_txn_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfTransaction]]:
    async def _make(
        *, user: User, scheme_code: str,
        units: Decimal, nav: Decimal, txn_date: date,
    ) -> MfTransaction:
        await _ensure_fund_metadata(db_session, scheme_code)
        txn = MfTransaction(
            user_id=user.id,
            scheme_code=scheme_code,
            folio_number="TEST_FOLIO",
            transaction_type=MfTransactionType.BUY,
            transaction_date=txn_date,
            units=units,
            nav=nav,
            amount=units * nav,
            source_system=MfTransactionSource.MANUAL,
        )
        db_session.add(txn)
        await db_session.flush()
        return txn
    return _make


@pytest_asyncio.fixture
async def fixture_sell_txn_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfTransaction]]:
    async def _make(
        *, user: User, scheme_code: str,
        units: Decimal, nav: Decimal, txn_date: date,
    ) -> MfTransaction:
        await _ensure_fund_metadata(db_session, scheme_code)
        txn = MfTransaction(
            user_id=user.id,
            scheme_code=scheme_code,
            folio_number="TEST_FOLIO",
            transaction_type=MfTransactionType.SELL,
            transaction_date=txn_date,
            units=units,
            nav=nav,
            amount=units * nav,
            source_system=MfTransactionSource.MANUAL,
        )
        db_session.add(txn)
        await db_session.flush()
        return txn
    return _make


@pytest_asyncio.fixture
async def fixture_nav_isin_factory(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[MfNavHistory]]:
    async def _make(
        *, scheme_code: str, isin: str,
        nav: Decimal | None = None, on_date: date | None = None,
    ) -> MfNavHistory:
        await _ensure_fund_metadata(db_session, scheme_code)
        row = MfNavHistory(
            scheme_code=scheme_code,
            isin=isin,
            scheme_name=f"Scheme {scheme_code}",
            mf_type="EQUITY",
            nav=nav if nav is not None else Decimal("100"),
            nav_date=on_date or date.today(),
        )
        db_session.add(row)
        await db_session.flush()
        return row
    return _make


async def _ensure_fund_metadata(db_session: AsyncSession, scheme_code: str) -> None:
    """Idempotent: insert ``MfFundMetadata`` if not already present."""
    from sqlalchemy import select

    existing = (await db_session.execute(
        select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code)
    )).scalar_one_or_none()
    if existing is not None:
        return
    db_session.add(MfFundMetadata(
        scheme_code=scheme_code,
        scheme_name=f"Scheme {scheme_code}",
        amc_name="Test AMC",
        category="Equity",
        sub_category="Large Cap Fund",
        plan_type=MfPlanType.DIRECT,
        option_type=MfOptionType.GROWTH,
        is_active=True,
        asset_class="equity",
        asset_subgroup="low_beta_equities",
    ))
    await db_session.flush()


# ── Input-builder fixtures (Task 6) ──────────────────────────────────────────


# rank-1 ISIN of low_beta_equities in the canonical CSV
_RANK1_ISIN = "INF209K01YY7"


@pytest_asyncio.fixture
async def fixture_user_with_dob(db_session: AsyncSession) -> User:
    """User with a date_of_birth (some downstream code expects it)."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_dob_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
def fixture_goal_allocation_output_one_subgroup():
    """Minimal ``GoalAllocationOutput`` with exactly one subgroup row.

    ``low_beta_equities`` total ₹10L, anchored to ranks in the canonical CSV.
    """
    from app.services.ai_bridge.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]
        AggregatedSubgroupRow,
        ClientSummary,
        GoalAllocationOutput,
    )

    return GoalAllocationOutput(
        client_summary=ClientSummary(
            age=35,
            effective_risk_score=50.0,
            total_corpus=1000000.0,
            goals=[],
        ),
        bucket_allocations=[],
        aggregated_subgroups=[
            AggregatedSubgroupRow(
                subgroup="low_beta_equities",
                emergency=0.0,
                short_term=0.0,
                medium_term=0.0,
                long_term=1000000.0,
                total=1000000.0,
            )
        ],
        future_investments_summary=[],
        grand_total=1000000.0,
        all_amounts_in_multiples_of_100=True,
        asset_class_breakdown=_minimal_long_term_equity_breakdown(1_000_000),
    )


def _minimal_long_term_equity_breakdown(long_term_equity: int):
    """Build a minimal AssetClassBreakdown with one all-equity long-term row."""
    from app.services.ai_bridge.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]
        AssetClassBreakdown,
        AssetClassSplitBlock,
        BucketAssetClassSplit,
    )

    def _empty(bucket: str) -> BucketAssetClassSplit:
        return BucketAssetClassSplit(bucket=bucket, equity=0, debt=0, others=0)

    long_term = BucketAssetClassSplit(
        bucket="long_term",
        equity=long_term_equity,
        debt=0,
        others=0,
        equity_pct=100.0,
        debt_pct=0.0,
        others_pct=0.0,
    )
    block = AssetClassSplitBlock(
        per_bucket=[_empty("emergency"), _empty("short_term"), _empty("medium_term"), long_term],
        equity_total=long_term_equity,
        debt_total=0,
        others_total=0,
        equity_total_pct=100.0,
        debt_total_pct=0.0,
        others_total_pct=0.0,
    )
    return AssetClassBreakdown(
        planned=block,
        actual=block,
        actual_sum_matches_grand_total=True,
    )


@pytest.fixture
def fixture_one_subgroup_ranking(monkeypatch):
    """Restrict ``get_fund_ranking()`` to ``low_beta_equities`` only.

    The full CSV has ~17 subgroups and ~170 ISINs; tests would otherwise need
    to seed NAVs for all of them. Patching the module-level reference inside
    ``input_builder`` keeps test data minimal.
    """
    from app.services.ai_bridge.rebalancing import fund_rank as fr_mod
    from app.services.ai_bridge.rebalancing import input_builder as ib_mod

    full = fr_mod.get_fund_ranking()
    constrained = {"low_beta_equities": full["low_beta_equities"]}
    monkeypatch.setattr(ib_mod, "get_fund_ranking", lambda: constrained)
    return constrained


@pytest_asyncio.fixture
async def fixture_seed_low_beta_navs(db_session: AsyncSession) -> None:
    """Seed a fallback MfNavHistory + MfFundMetadata row for every rank in low_beta_equities.

    Inserted at an old date so a holding fixture's today-NAV wins as
    ``_latest_nav_by_isin``. NAV=100 is a placeholder for non-held ranks the
    builder still needs to price.
    """
    from app.services.ai_bridge.rebalancing import fund_rank as fr_mod

    seed_date = date(2020, 1, 1)
    rows = fr_mod.get_fund_ranking().get("low_beta_equities", [])
    for rr in rows:
        scheme_code = f"SCH_{rr.isin}"
        await _ensure_fund_metadata(db_session, scheme_code)
        db_session.add(MfNavHistory(
            scheme_code=scheme_code,
            isin=rr.isin,
            scheme_name=rr.fund_name,
            mf_type="EQUITY",
            nav=Decimal("100"),
            nav_date=seed_date,
        ))
    await db_session.flush()


async def _add_holding(
    db: AsyncSession,
    *,
    user: User,
    scheme_code: str,
    isin: str,
    units: Decimal,
    nav: Decimal,
    txn_date: date,
    asset_subgroup: str = "low_beta_equities",
    sub_category: str = "Large Cap Fund",
) -> None:
    """Insert MfFundMetadata + MfNavHistory + a BUY MfTransaction together."""
    from sqlalchemy import select

    existing = (await db.execute(
        select(MfFundMetadata).where(MfFundMetadata.scheme_code == scheme_code)
    )).scalar_one_or_none()
    if existing is None:
        db.add(MfFundMetadata(
            scheme_code=scheme_code,
            scheme_name=f"Scheme {scheme_code}",
            amc_name="Test AMC",
            category="Equity",
            sub_category=sub_category,
            plan_type=MfPlanType.DIRECT,
            option_type=MfOptionType.GROWTH,
            is_active=True,
            asset_class="equity",
            asset_subgroup=asset_subgroup,
        ))
    db.add(MfNavHistory(
        scheme_code=scheme_code,
        isin=isin,
        scheme_name=f"Scheme {scheme_code}",
        mf_type="EQUITY",
        nav=nav,
        nav_date=date.today(),
    ))
    db.add(MfTransaction(
        user_id=user.id,
        scheme_code=scheme_code,
        folio_number="TEST_FOLIO",
        transaction_type=MfTransactionType.BUY,
        transaction_date=txn_date,
        units=units,
        nav=nav,
        amount=units * nav,
        source_system=MfTransactionSource.MANUAL,
    ))
    await db.flush()


@pytest_asyncio.fixture
async def fixture_user_with_holdings(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> tuple[User, str]:
    """User holding 10 units of the rank-1 fund at NAV 60 (cost 50)."""
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{_RANK1_ISIN}",
        isin=_RANK1_ISIN,
        units=Decimal("10"),
        nav=Decimal("60"),
        txn_date=date(2024, 1, 1),
    )
    return fixture_user_with_dob, _RANK1_ISIN


@pytest_asyncio.fixture
async def fixture_user_with_bad_holding(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> User:
    """User holding an ISIN that is NOT in the fund-rank CSV."""
    bad_isin = "INF000BAD0001"
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code="BAD_SCHEME_001",
        isin=bad_isin,
        units=Decimal("3"),
        nav=Decimal("50"),
        txn_date=date(2024, 1, 1),
        asset_subgroup="low_beta_equities",
        sub_category="Large Cap Fund",
    )
    return fixture_user_with_dob


@pytest_asyncio.fixture
async def fixture_user_with_two_holdings(
    db_session: AsyncSession, fixture_user_with_dob: User,
) -> User:
    """User holding two distinct ISINs at known NAVs (60 and 80)."""
    rank1 = _RANK1_ISIN
    rank2 = "INF846K01DP8"  # rank-2 of low_beta_equities
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{rank1}",
        isin=rank1,
        units=Decimal("10"),
        nav=Decimal("60"),
        txn_date=date(2024, 1, 1),
    )
    await _add_holding(
        db_session,
        user=fixture_user_with_dob,
        scheme_code=f"SCH_{rank2}",
        isin=rank2,
        units=Decimal("5"),
        nav=Decimal("80"),
        txn_date=date(2024, 1, 1),
    )
    return fixture_user_with_dob


@pytest_asyncio.fixture
async def fixture_user_with_holdings_no_tax_profile(
    db_session: AsyncSession, fixture_user_with_holdings: tuple[User, str],
) -> User:
    """Alias for fixture_user_with_holdings — no TaxProfile attached by default."""
    user, _ = fixture_user_with_holdings
    # Sanity: ensure we really have no TaxProfile row.
    from sqlalchemy import select

    existing = (await db_session.execute(
        select(TaxProfile).where(TaxProfile.user_id == user.id)
    )).scalar_one_or_none()
    assert existing is None
    return user


# ── Persistence fixtures (Task 7) ────────────────────────────────────────────


@pytest.fixture
def fixture_rebalancing_response():
    """Minimal, valid ``RebalancingComputeResponse`` for persistence tests."""
    from datetime import datetime

    from app.services.ai_bridge.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from Rebalancing.models import (  # type: ignore[import-not-found]
        KnobSnapshot,
        RebalancingComputeResponse,
        RebalancingRunMetadata,
        RebalancingTotals,
    )

    return RebalancingComputeResponse(
        rows=[],
        subgroups=[],
        totals=RebalancingTotals(
            total_buy_inr=Decimal(0),
            total_sell_inr=Decimal(0),
            net_cash_flow_inr=Decimal(0),
            total_stcg_realised=Decimal(0),
            total_ltcg_realised=Decimal(0),
            total_stcg_net_off=Decimal(0),
            total_tax_estimate_inr=Decimal(0),
            total_exit_load_inr=Decimal(0),
            unrebalanced_remainder_inr=Decimal(0),
            rows_count=0,
            funds_to_buy_count=0,
            funds_to_sell_count=0,
            funds_to_exit_count=0,
            funds_held_count=0,
        ),
        metadata=RebalancingRunMetadata(
            computed_at=datetime(2026, 4, 29, 12, 0, 0),
            engine_version="test-1.0.0",
            request_corpus_inr=Decimal(0),
            knob_snapshot=KnobSnapshot(
                multi_fund_cap_pct=20.0,
                others_fund_cap_pct=10.0,
                rebalance_min_change_pct=0.10,
                exit_floor_rating=5,
                ltcg_annual_exemption_inr=Decimal("125000"),
                stcg_rate_equity_pct=20.0,
                ltcg_rate_equity_pct=12.5,
                st_threshold_months_equity=12,
                st_threshold_months_debt=24,
                multi_cap_sub_categories=[],
            ),
            request_id=uuid.uuid4(),
        ),
        trade_list=[],
    )


@pytest_asyncio.fixture
async def fixture_allocation_row(
    db_session: AsyncSession, fixture_user_with_dob: User,
):
    """Insert a parent ALLOCATION row that REBALANCING_TRADES can FK back to."""
    from app.models.portfolio import Portfolio
    from app.models.rebalancing import (
        RebalancingRecommendation,
        RebalancingStatus,
        RecommendationType,
    )

    portfolio = Portfolio(user_id=fixture_user_with_dob.id, name="Primary", is_primary=True)
    db_session.add(portfolio)
    await db_session.flush()

    rec = RebalancingRecommendation(
        portfolio_id=portfolio.id,
        recommendation_type=RecommendationType.ALLOCATION,
        source_allocation_id=None,
        status=RebalancingStatus.pending,
        recommendation_data={"goal_allocation_output": {}},
        reason="Test allocation snapshot",
    )
    db_session.add(rec)
    await db_session.flush()
    return rec


# ── Service fixtures (Task 9) ────────────────────────────────────────────────


@pytest_asyncio.fixture
async def fixture_user_no_dob(db_session: AsyncSession) -> User:
    """User without a date_of_birth set."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_no_dob_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest_asyncio.fixture
async def fixture_user_with_dob_no_holdings(db_session: AsyncSession) -> User:
    """User with date_of_birth but no MfTransaction rows."""
    suffix = uuid.uuid4().hex[:8]
    user = User(
        id=uuid.uuid4(),
        email=f"rebal_no_holdings_{suffix}@example.com",
        country_code="+91",
        mobile="9999999999",
        phone=f"+91-9999{suffix}",
        date_of_birth=date(1990, 1, 1),
    )
    db_session.add(user)
    await db_session.flush()
    return user


def _serialised_one_subgroup_allocation() -> dict:
    """JSON payload mirroring fixture_goal_allocation_output_one_subgroup."""
    from app.services.ai_bridge.common import ensure_ai_agents_path

    ensure_ai_agents_path()

    from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]
        AggregatedSubgroupRow,
        ClientSummary,
        GoalAllocationOutput,
    )

    output = GoalAllocationOutput(
        client_summary=ClientSummary(
            age=35,
            effective_risk_score=50.0,
            total_corpus=1000000.0,
            goals=[],
        ),
        bucket_allocations=[],
        aggregated_subgroups=[
            AggregatedSubgroupRow(
                subgroup="low_beta_equities",
                emergency=0.0,
                short_term=0.0,
                medium_term=0.0,
                long_term=1000000.0,
                total=1000000.0,
            )
        ],
        future_investments_summary=[],
        grand_total=1000000.0,
        all_amounts_in_multiples_of_100=True,
        asset_class_breakdown=_minimal_long_term_equity_breakdown(1_000_000),
    )
    return output.model_dump(mode="json")


async def _insert_allocation_row(
    db: AsyncSession, user_id: uuid.UUID, *, age_days: int,
):
    from datetime import datetime, timedelta, timezone

    from app.models.portfolio import Portfolio
    from app.models.rebalancing import (
        RebalancingRecommendation,
        RebalancingStatus,
        RecommendationType,
    )
    from sqlalchemy import select

    portfolio = (await db.execute(
        select(Portfolio).where(Portfolio.user_id == user_id, Portfolio.is_primary == True)
    )).scalar_one_or_none()
    if portfolio is None:
        portfolio = Portfolio(user_id=user_id, name="Primary", is_primary=True)
        db.add(portfolio)
        await db.flush()

    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    rec = RebalancingRecommendation(
        portfolio_id=portfolio.id,
        recommendation_type=RecommendationType.ALLOCATION,
        source_allocation_id=None,
        status=RebalancingStatus.pending,
        recommendation_data={
            "goal_allocation_output": _serialised_one_subgroup_allocation(),
        },
        reason="Test allocation snapshot",
        created_at=created,
    )
    db.add(rec)
    await db.flush()
    return rec


@pytest_asyncio.fixture
async def fixture_recent_allocation_row(
    db_session: AsyncSession, fixture_user_with_holdings: tuple[User, str],
):
    user, _ = fixture_user_with_holdings
    return await _insert_allocation_row(db_session, user.id, age_days=1)


@pytest_asyncio.fixture
async def fixture_old_allocation_row(
    db_session: AsyncSession, fixture_user_with_holdings: tuple[User, str],
):
    user, _ = fixture_user_with_holdings
    return await _insert_allocation_row(db_session, user.id, age_days=180)


@pytest.fixture
def fixture_goal_allocation_outcome(fixture_goal_allocation_output_one_subgroup):
    """An ``AllocationRunOutcome`` carrying the canonical one-subgroup output."""
    from app.services.ai_bridge.asset_allocation.service import AllocationRunOutcome

    return AllocationRunOutcome(
        result=fixture_goal_allocation_output_one_subgroup,
        blocking_message=None,
        rebalancing_recommendation_id=uuid.uuid4(),
        allocation_snapshot_id=uuid.uuid4(),
    )
