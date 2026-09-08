"""Tests for trailing_cagr_for_scheme — DB-NAV → 1/3/5y CAGR (DB-only, no mfapi)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.all_models  # noqa: F401  -- registers ORM tables so FK targets resolve
from app.domains.mutual_funds.models.mf_nav_history import MfNavHistory
from app.domains.mutual_funds.services.fund_returns_service import trailing_cagr_for_scheme


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(MfNavHistory.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    await engine.dispose()


def _nav(d: dt.date, nav: float, code: str = "SCH1") -> MfNavHistory:
    return MfNavHistory(
        id=uuid.uuid4(), scheme_code=code, scheme_name="Test Fund",
        mf_type="EQUITY", nav=nav, nav_date=d,
    )


@pytest.mark.asyncio
async def test_three_year_cagr_from_stored_nav(db_session):
    end = dt.date(2026, 7, 1)
    db_session.add_all([
        _nav(end - dt.timedelta(days=365 * 3), 100.0),
        _nav(end, 200.0),
    ])
    await db_session.commit()

    out = await trailing_cagr_for_scheme(db_session, "SCH1")

    assert out["return_3y_cagr_pct"] == pytest.approx(25.99, abs=0.02)


@pytest.mark.asyncio
async def test_missing_horizon_is_none(db_session):
    end = dt.date(2026, 7, 1)
    db_session.add_all([
        _nav(end - dt.timedelta(days=200), 100.0),
        _nav(end, 120.0),
    ])
    await db_session.commit()

    out = await trailing_cagr_for_scheme(db_session, "SCH1")

    assert out["return_1y_cagr_pct"] is None   # no NAV at/before 1y-ago cutoff
    assert out["return_3y_cagr_pct"] is None


@pytest.mark.asyncio
async def test_unknown_scheme_all_none(db_session):
    out = await trailing_cagr_for_scheme(db_session, "NOPE")
    assert out == {
        "return_1y_cagr_pct": None,
        "return_3y_cagr_pct": None,
        "return_5y_cagr_pct": None,
    }
