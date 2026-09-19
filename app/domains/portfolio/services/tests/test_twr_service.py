"""Tests for the TWR DB adapter (compute_twr_series)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers every ORM Table so FK targets (users, etc.) resolve
from app.domains.benchmarks.models import BenchmarkIndex, BenchmarkIndexValue
from app.domains.benchmarks.services.benchmark_data_service import NIFTY_50_CODE
from app.domains.mutual_funds.models import MfTransaction
from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.portfolio.models.user_portfolio_nav_history import UserPortfolioNavHistory
from app.domains.portfolio.services.twr_service import compute_twr_series


@pytest_asyncio.fixture
async def db_session():
    # Per backend CLAUDE.md: create only the tables under test (Base.metadata.create_all
    # fails on sqlite because an unrelated model uses a Postgres ARRAY). sqlite does not
    # enforce FKs by default, so FK columns to un-created tables are fine for inserts.
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(UserPortfolioNavHistory.__table__.create)
        await conn.run_sync(MfTransaction.__table__.create)
        # compute_twr_series now also computes XIRR (reads MfNavHistory) and reads the
        # Nifty series from the benchmarks domain (BenchmarkIndex + value rows).
        await conn.run_sync(MfNavHistory.__table__.create)
        await conn.run_sync(BenchmarkIndex.__table__.create)
        await conn.run_sync(BenchmarkIndexValue.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _nav(user_id, d, value):
    return UserPortfolioNavHistory(
        id=uuid.uuid4(), user_id=user_id, recorded_date=d,
        total_value=value, total_invested=value, gain_percentage=0.0,
    )


@pytest.mark.asyncio
async def test_compute_twr_series_real_data(db_session: AsyncSession):
    uid = uuid.uuid4()
    db_session.add_all([
        _nav(uid, date(2024, 1, 1), 100.0),
        _nav(uid, date(2024, 1, 2), 110.0),
        _nav(uid, date(2024, 1, 3), 121.0),
    ])
    # A SELL with a NEGATIVE stored amount must still reduce (abs + type sign).
    db_session.add(MfTransaction(
        id=uuid.uuid4(), user_id=uid, scheme_code="SCH1", folio_number="F1",
        transaction_type=MfTransactionType.SELL, transaction_date=date(2024, 1, 3),
        units=-1.0, nav=10.0, amount=-10.0,
    ))
    nifty_id = uuid.uuid4()
    db_session.add(BenchmarkIndex(
        id=nifty_id, code=NIFTY_50_CODE, display_name="Nifty 50",
        short_name="Nifty 50", provider="NSE", asset_class="EQUITY",
    ))
    db_session.add_all([
        BenchmarkIndexValue(id=uuid.uuid4(), benchmark_index_id=nifty_id, value_date=date(2024, 1, 1), tri_value=200.0),
        BenchmarkIndexValue(id=uuid.uuid4(), benchmark_index_id=nifty_id, value_date=date(2024, 1, 3), tri_value=220.0),
    ])
    await db_session.flush()

    res = await compute_twr_series(db_session, uid)
    assert res.has_data is True
    assert len(res.points) == 3
    assert res.points[0].portfolio_index == pytest.approx(1.0)
    # Day 3: (121 − (−10)) / 110 = 1.19090..., linked from day 2's 1.1 → 1.30999...
    assert res.points[-1].portfolio_index == pytest.approx(1.31, abs=1e-2)
    # Nifty normalized to inception: 200→1.0, 220→1.1 (Jan 2 has no TRI → on-or-before 200).
    assert res.points[0].nifty_index == pytest.approx(1.0)
    assert res.points[1].nifty_index == pytest.approx(1.0)
    assert res.points[-1].nifty_index == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_compute_twr_series_no_history(db_session: AsyncSession):
    res = await compute_twr_series(db_session, uuid.uuid4())
    assert res.has_data is False
    assert res.points == []
