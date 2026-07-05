"""ORM: additional_investment_runs + targets/buys — columns, enum values, child cascade."""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.all_models  # noqa: F401  -- registers FK target tables (users/portfolios/chat_sessions/practical_asset_allocation_runs) with Base.metadata
from app.domains.additional_investment.models import (
    AdditionalInvestmentBuy,
    AdditionalInvestmentRun,
    AdditionalInvestmentTarget,
    TargetBucket,
    Cadence,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        # Per repo convention, Base.metadata.create_all FAILS on sqlite (an
        # unrelated model uses a Postgres ARRAY). Create only the tables under
        # test; the FK target tables only need their metadata registered (above).
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


def _make_run() -> AdditionalInvestmentRun:
    return AdditionalInvestmentRun(
        user_id=uuid.uuid4(),
        portfolio_id=uuid.uuid4(),
        chat_session_id=None,
        source_allocation_run_id=uuid.uuid4(),
        engine_version="ainv-1.0.0",
        target_bucket=TargetBucket.LONG_TERM,
        cadence=Cadence.SIP_MONTHLY,
        deploy_amount_inr=100000,
        deployed_inr=99800,
        undeployed_inr=200,
        request_input={"deploy_amount_inr": 100000.0},
        user_question="Where should I invest 1 lakh monthly?",
        targets=[
            AdditionalInvestmentTarget(
                subgroup="large_cap", ratio=0.6, target_inr=60000
            )
        ],
        buys=[
            AdditionalInvestmentBuy(
                recommended_fund="HDFC Top 100 Fund",
                isin="INF179K01XXX",
                sub_category="Large Cap Fund",
                asset_subgroup="large_cap",
                rank=1,
                scheme_code="120503",
                amount_inr=59800,
                monthly_amount_inr=5000,
                reason="Rank-1 fund for large_cap",
            )
        ],
    )


@pytest.mark.asyncio
async def test_run_persists_columns_and_enum_round_trip(db_session: AsyncSession):
    run = _make_run()
    db_session.add(run)
    await db_session.flush()

    stored = (
        await db_session.execute(
            select(AdditionalInvestmentRun).where(
                AdditionalInvestmentRun.id == run.id
            )
        )
    ).scalar_one()
    assert stored.target_bucket is TargetBucket.LONG_TERM
    assert stored.cadence is Cadence.SIP_MONTHLY
    assert float(stored.deploy_amount_inr) == 100000.0
    assert float(stored.deployed_inr) == 99800.0
    assert float(stored.undeployed_inr) == 200.0
    assert stored.engine_version == "ainv-1.0.0"
    assert stored.request_input == {"deploy_amount_inr": 100000.0}
    assert stored.user_question == "Where should I invest 1 lakh monthly?"


@pytest.mark.asyncio
async def test_enum_values_match_engine_wire_contract(db_session: AsyncSession):
    # Stored DB representation must equal the engine's string values.
    assert TargetBucket.LONG_TERM.value == "long_term"
    assert TargetBucket.MEDIUM_TERM.value == "medium_term"
    assert Cadence.LUMPSUM.value == "lumpsum"
    assert Cadence.SIP_MONTHLY.value == "sip_monthly"


@pytest.mark.asyncio
async def test_children_persist_and_cascade_delete(db_session: AsyncSession):
    run = _make_run()
    db_session.add(run)
    await db_session.flush()

    targets = (
        await db_session.execute(select(AdditionalInvestmentTarget))
    ).scalars().all()
    buys = (
        await db_session.execute(select(AdditionalInvestmentBuy))
    ).scalars().all()
    assert len(targets) == 1
    assert len(buys) == 1
    assert targets[0].subgroup == "large_cap"
    assert float(targets[0].ratio) == 0.6
    assert buys[0].monthly_amount_inr is not None

    # delete-orphan: removing the parent removes both child collections.
    await db_session.delete(run)
    await db_session.flush()

    targets_after = (
        await db_session.execute(select(AdditionalInvestmentTarget))
    ).scalars().all()
    buys_after = (
        await db_session.execute(select(AdditionalInvestmentBuy))
    ).scalars().all()
    assert targets_after == []
    assert buys_after == []
